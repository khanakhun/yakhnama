"""Unit tests for the HTTP hardening middlewares in ``platform/http.py``."""

import itertools
from collections.abc import AsyncIterator

import httpx
import pytest
import structlog
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.testing import capture_logs

from tests.fakes.ids import SequentialIdGenerator
from tests.unit.platform.asgi import RecordingApp, client_for, request_id_app
from yakhnama.platform.http import (
    API_CONTENT_SECURITY_POLICY,
    STRICT_TRANSPORT_SECURITY,
    UNMATCHED_ROUTE,
    RequestBodyGuardMiddleware,
    RequestIdMiddleware,
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
    TrustedHostMiddleware,
    build_cors_options,
    contains_nul,
    host_of,
    is_scanned_media_type,
    is_trusted_host,
)
from yakhnama.platform.settings import Settings

MAX_BYTES = 1024


def test_request_id_middleware_accepts_well_formed_inbound_id() -> None:
    middleware = RequestIdMiddleware(
        request_id_app, header_name="X-Request-ID", id_generator=SequentialIdGenerator()
    )

    request_id = middleware.request_id_for(
        {"type": "http", "headers": [(b"x-request-id", b"abc-123.X_y")]}
    )

    assert request_id == "abc-123.X_y"


@pytest.mark.parametrize("inbound", [b"has space", b"<script>", b"x" * 129, b""])
def test_request_id_middleware_replaces_malformed_inbound_id(inbound: bytes) -> None:
    generator = SequentialIdGenerator()
    middleware = RequestIdMiddleware(
        request_id_app, header_name="X-Request-ID", id_generator=generator
    )

    request_id = middleware.request_id_for(
        {"type": "http", "headers": [(b"x-request-id", inbound)]}
    )

    assert request_id != inbound.decode()
    assert len(request_id) == 36


async def test_request_id_middleware_generates_echoes_and_records_id() -> None:
    app = RequestIdMiddleware(
        request_id_app, header_name="X-Request-ID", id_generator=SequentialIdGenerator()
    )

    async with client_for(app) as client:
        response = await client.get("/")

    assert response.headers["x-request-id"] == response.json()["request_id"]


async def test_request_id_middleware_binds_id_to_log_context() -> None:
    async def logging_app(scope: Scope, receive: Receive, send: Send) -> None:
        structlog.get_logger("test").info("inside")
        await request_id_app(scope, receive, send)

    app = RequestIdMiddleware(
        logging_app, header_name="X-Request-ID", id_generator=SequentialIdGenerator()
    )

    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        async with client_for(app) as client:
            await client.get("/", headers={"X-Request-ID": "trace-1"})

    assert logs[0]["request_id"] == "trace-1"


async def test_request_logging_middleware_logs_fields_without_personal_data() -> None:
    ticks = itertools.count(start=10.0, step=0.25)
    app = RequestLoggingMiddleware(RecordingApp(status=201), timer=lambda: next(ticks))

    with capture_logs() as logs:
        async with client_for(app) as client:
            await client.post(
                "/api/v1/places/123?q=phone-0300",
                content=b'{"email": "a@b.c"}',
                headers={
                    "Authorization": "Bearer secret",
                    "X-Forwarded-For": "1.2.3.4",
                },
            )

    assert logs == [
        {
            "event": "http_request",
            "method": "POST",
            "route": UNMATCHED_ROUTE,
            "status": 201,
            "duration_ms": 250.0,
            "request_id": None,
            "log_level": "info",
        }
    ]


async def test_request_logging_middleware_uses_route_template() -> None:
    class Route:
        """A matched route as Starlette records it.

        Implements: Fake (of a Starlette route).
        """

        path = "/api/v1/places/{place_id}"

    inner = RecordingApp()

    async def routed(scope: Scope, receive: Receive, send: Send) -> None:
        scope["route"] = Route()
        await inner(scope, receive, send)

    app = RequestLoggingMiddleware(routed)

    with capture_logs() as logs:
        async with client_for(app) as client:
            await client.get("/api/v1/places/0192")

    assert logs[0]["route"] == "/api/v1/places/{place_id}"


async def test_request_logging_middleware_logs_500_when_app_raises() -> None:
    app = RequestLoggingMiddleware(RecordingApp(error=RuntimeError("boom")))

    with capture_logs() as logs:
        async with client_for(app) as client:
            await client.get("/")

    assert logs[0]["status"] == 500


async def test_security_headers_middleware_adds_api_headers() -> None:
    app = SecurityHeadersMiddleware(RecordingApp(), is_hsts_enabled=False)

    async with client_for(app) as client:
        response = await client.get("/api/v1/x")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy"] == API_CONTENT_SECURITY_POLICY
    assert "strict-transport-security" not in response.headers


async def test_security_headers_middleware_adds_hsts_when_enabled() -> None:
    app = SecurityHeadersMiddleware(RecordingApp(), is_hsts_enabled=True)

    async with client_for(app) as client:
        response = await client.get("/")

    assert response.headers["strict-transport-security"] == STRICT_TRANSPORT_SECURITY


async def test_security_headers_middleware_uses_page_policy_on_its_path_only() -> None:
    app = SecurityHeadersMiddleware(
        RecordingApp(),
        is_hsts_enabled=False,
        page_policies={"/api/v1/docs": "default-src 'self'"},
    )

    async with client_for(app) as client:
        docs = await client.get("/api/v1/docs")
        other = await client.get("/api/v1/docs/x")

    assert docs.headers["content-security-policy"] == "default-src 'self'"
    assert other.headers["content-security-policy"] == API_CONTENT_SECURITY_POLICY


async def test_security_headers_middleware_keeps_header_set_by_route() -> None:
    inner = RecordingApp(headers=((b"x-frame-options", b"SAMEORIGIN"),))
    app = SecurityHeadersMiddleware(inner, is_hsts_enabled=False)

    async with client_for(app) as client:
        response = await client.get("/")

    assert response.headers.get_list("x-frame-options") == ["SAMEORIGIN"]


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("api.yakhnama.org", "api.yakhnama.org"),
        ("API.Yakhnama.org:8443", "api.yakhnama.org"),
        ("[::1]:8000", "::1"),
        ("", ""),
    ],
)
def test_host_of_strips_port_and_brackets(header: str, expected: str) -> None:
    host = host_of({"type": "http", "headers": [(b"host", header.encode())]})

    assert host == expected


@pytest.mark.parametrize(
    ("host", "patterns", "expected"),
    [
        ("api.yakhnama.org", ["api.yakhnama.org"], True),
        ("a.yakhnama.org", ["*.yakhnama.org"], True),
        ("yakhnama.org", ["*.yakhnama.org"], False),
        ("evilyakhnama.org", ["*.yakhnama.org"], False),
        ("anything", ["*"], True),
        ("evil.test", ["localhost"], False),
        ("", ["*"], False),
    ],
)
def test_is_trusted_host_matches_patterns(
    host: str, patterns: list[str], *, expected: bool
) -> None:
    result = is_trusted_host(host, patterns)

    assert result is expected


async def test_trusted_host_middleware_untrusted_host_gets_400_problem() -> None:
    inner = RecordingApp()
    app = TrustedHostMiddleware(inner, trusted_hosts=["api.yakhnama.org"])

    async with client_for(app, base_url="http://evil.test") as client:
        response = await client.get("/")

    assert response.status_code == 400
    assert response.json()["type"] == "https://yakhnama.org/problems/invalid-host"
    assert inner.calls == 0


async def test_trusted_host_middleware_trusted_host_passes() -> None:
    app = TrustedHostMiddleware(RecordingApp(), trusted_hosts=["localhost"])

    async with client_for(app) as client:
        response = await client.get("/")

    assert response.status_code == 200


def test_build_cors_options_without_origins_returns_none(settings: Settings) -> None:
    options = build_cors_options(settings)

    assert options is None


def test_build_cors_options_never_allows_credentials() -> None:
    settings = Settings(_env_file=None, cors_allow_origins=["https://yakhnama.org"])

    options = build_cors_options(settings)

    assert options is not None
    assert options.allow_credentials is False
    assert options.allow_origins == ("https://yakhnama.org",)
    assert "X-Request-ID" in options.allow_headers
    assert "X-Request-ID" in options.expose_headers
    assert "*" not in options.allow_methods


async def _cors_preflight(origin: str) -> httpx.Response:
    settings = Settings(_env_file=None, cors_allow_origins=["https://yakhnama.org"])
    options = build_cors_options(settings)
    assert options is not None
    app = CORSMiddleware(
        RecordingApp(),
        allow_origins=options.allow_origins,
        allow_methods=options.allow_methods,
        allow_headers=options.allow_headers,
        allow_credentials=options.allow_credentials,
    )
    async with client_for(app) as client:
        return await client.options(
            "/api/v1/x",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Idempotency-Key",
            },
        )


async def test_cors_allowed_origin_preflight_succeeds() -> None:
    response = await _cors_preflight("https://yakhnama.org")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://yakhnama.org"
    assert "access-control-allow-credentials" not in response.headers


async def test_cors_other_origin_preflight_is_refused() -> None:
    response = await _cors_preflight("https://evil.test")

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        (None, True),
        ("application/json", True),
        ("application/merge-patch+json; charset=utf-8", True),
        ("application/x-www-form-urlencoded", True),
        ("text/csv", True),
        ("multipart/form-data; boundary=x", False),
        ("image/jpeg", False),
    ],
)
def test_is_scanned_media_type_classifies_types(
    content_type: str | None, *, expected: bool
) -> None:
    result = is_scanned_media_type(content_type)

    assert result is expected


@pytest.mark.parametrize(
    ("data", "is_json", "expected"),
    [
        (b'{"a": "x\\u0000y"}', True, True),
        (b'{"a": "x\\\\\\u0000y"}', True, True),
        (b'{"a": "x\\\\u0000y"}', True, False),
        (b'{"a": "x\\u0000y"}', False, False),
        (b"a\x00b", False, True),
        (b"a=%00", False, True),
        (b'{"a": "plain"}', True, False),
    ],
)
def test_contains_nul_detects_raw_percent_and_escaped_nul(
    data: bytes, *, is_json: bool, expected: bool
) -> None:
    result = contains_nul(data, is_json=is_json)

    assert result is expected


def _guard(inner: RecordingApp) -> RequestBodyGuardMiddleware:
    return RequestBodyGuardMiddleware(inner, max_bytes=MAX_BYTES)


async def test_body_guard_passes_body_through_unchanged() -> None:
    inner = RecordingApp()

    async with client_for(_guard(inner)) as client:
        response = await client.post("/", content=b'{"name": "Hunza"}')

    assert response.status_code == 200
    assert inner.bodies == [b'{"name": "Hunza"}']


async def test_body_guard_declared_oversized_body_gets_413() -> None:
    inner = RecordingApp()

    async with client_for(_guard(inner)) as client:
        response = await client.post("/", content=b"x" * (MAX_BYTES + 1))

    assert response.status_code == 413
    assert response.json()["type"] == "https://yakhnama.org/problems/payload-too-large"
    assert inner.calls == 0


async def test_body_guard_streamed_oversized_body_gets_413() -> None:
    inner = RecordingApp()

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(3):
            yield b"x" * (MAX_BYTES // 2)

    async with client_for(_guard(inner)) as client:
        response = await client.post("/", content=chunks())

    assert response.status_code == 413
    assert inner.calls == 0


async def test_body_guard_body_at_limit_is_accepted() -> None:
    inner = RecordingApp()

    async with client_for(_guard(inner)) as client:
        response = await client.post(
            "/", content=b"x" * MAX_BYTES, headers={"content-type": "image/png"}
        )

    assert response.status_code == 200


async def test_body_guard_malformed_content_length_is_left_to_the_server() -> None:
    inner = RecordingApp()
    app = _guard(inner)
    messages: list[Message] = [
        {"type": "http.request", "body": b"{}", "more_body": False}
    ]

    async def receive() -> Message:
        return messages.pop(0)

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "query_string": b"",
        "headers": [(b"content-length", b"abc")],
    }
    await app(scope, receive, send)

    assert inner.bodies == [b"{}"]


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (b'{"name": "a\\u0000"}', "application/json"),
        (b'{"name": "a\\u0000"}', None),
        (b"name=a%00", "application/x-www-form-urlencoded"),
    ],
)
async def test_body_guard_nul_in_text_body_gets_422(
    body: bytes, content_type: str | None
) -> None:
    inner = RecordingApp()
    headers = {} if content_type is None else {"content-type": content_type}

    async with client_for(_guard(inner)) as client:
        request = client.build_request("POST", "/", content=body, headers=headers)
        response = await client.send(request)

    assert response.status_code == 422
    assert response.json()["type"] == "https://yakhnama.org/problems/nul-character"
    assert inner.calls == 0


async def test_body_guard_nul_in_binary_body_is_allowed() -> None:
    inner = RecordingApp()

    async with client_for(_guard(inner)) as client:
        response = await client.post(
            "/", content=b"\x89PNG\x00\x00", headers={"content-type": "image/png"}
        )

    assert response.status_code == 200


@pytest.mark.parametrize("url", ["/search?q=a%00b", "/places/a%00b"])
async def test_body_guard_nul_in_query_or_path_gets_422(url: str) -> None:
    inner = RecordingApp()

    async with client_for(_guard(inner)) as client:
        response = await client.get(url)

    assert response.status_code == 422
    assert inner.calls == 0


def _wrap(middleware_name: str, inner: ASGIApp) -> ASGIApp:
    if middleware_name == "logging":
        return RequestLoggingMiddleware(inner)
    if middleware_name == "body":
        return RequestBodyGuardMiddleware(inner, max_bytes=MAX_BYTES)
    if middleware_name == "headers":
        return SecurityHeadersMiddleware(inner, is_hsts_enabled=False)
    if middleware_name == "host":
        return TrustedHostMiddleware(inner, trusted_hosts=["localhost"])
    return RequestIdMiddleware(
        inner, header_name="X-Request-ID", id_generator=SequentialIdGenerator()
    )


@pytest.mark.parametrize(
    "middleware_name", ["logging", "body", "headers", "host", "request-id"]
)
async def test_middlewares_pass_lifespan_scope_through(middleware_name: str) -> None:
    calls: list[str] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        del receive, send
        calls.append(str(scope["type"]))

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        del message

    await _wrap(middleware_name, inner)({"type": "lifespan"}, receive, send)

    assert calls == ["lifespan"]
