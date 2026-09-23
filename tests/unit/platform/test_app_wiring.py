"""The platform as ``create_app`` wires it.

Covers the docs page, the middleware order, the readiness cache, the container
bindings and the OpenAPI snapshot writer.
"""

import dataclasses
import json
import logging
import runpy
import sys
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from pydantic import RedisDsn
from redis.asyncio import Redis
from starlette.requests import Request

from tests.fakes.auth import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    FakeJwksClient,
    InMemoryIdempotencyStore,
    StaticRateLimiter,
    access_token_claims,
    issue_token,
    session_key_pair,
)
from tests.fakes.clock import FrozenClock
from tests.unit.platform.asgi import client_for
from yakhnama.main import DOCS_PATH, OPENAPI_PATH, create_app
from yakhnama.platform.api_docs import build_api_reference, script_hash_sources
from yakhnama.platform.auth.dependencies import CurrentPrincipal
from yakhnama.platform.auth.tokens import TokenValidator
from yakhnama.platform.container import (
    Container,
    build_container,
    build_rate_limiter,
    build_token_validator,
)
from yakhnama.platform.health import CheckStatus, ReadinessCache, get_readiness_cache
from yakhnama.platform.http import API_CONTENT_SECURITY_POLICY
from yakhnama.platform.idempotency.sqlalchemy_store import SqlAlchemyIdempotencyStore
from yakhnama.platform.openapi_snapshot import (
    main,
    render_openapi_snapshot,
    snapshot_settings,
    write_openapi_snapshot,
)
from yakhnama.platform.ratelimit.limiter import InMemoryRateLimiter
from yakhnama.platform.ratelimit.redis_limiter import RedisRateLimiter
from yakhnama.platform.settings import SCALAR_CDN_URL, Settings

IDEMPOTENCY_KEY = "0192f4c1-0000-7000-8000-000000000001"


def test_build_api_reference_page_loads_bundle_and_hashes_inline_script() -> None:
    page = build_api_reference(
        openapi_url=OPENAPI_PATH, title="Yakhnama", script_url=SCALAR_CDN_URL
    )

    hashes = script_hash_sources(page.html)
    assert f'<script src="{SCALAR_CDN_URL}">' in page.html
    assert len(hashes) == 1
    assert f"script-src {SCALAR_CDN_URL} {hashes[0]}" in page.content_security_policy
    assert "default-src 'none'" in page.content_security_policy
    assert "fastapi.tiangolo.com" not in page.html
    assert "'unsafe-eval'" not in page.content_security_policy


def test_script_hash_sources_without_inline_script_is_empty() -> None:
    hashes = script_hash_sources('<script src="x.js"></script>')

    assert hashes == ()


async def test_docs_route_serves_scalar_with_its_own_policy(app: FastAPI) -> None:
    async with client_for(app) as client:
        page = await client.get(DOCS_PATH)
        document = await client.get(OPENAPI_PATH)

    assert page.status_code == 200
    assert "Scalar.createApiReference" in page.text
    assert page.headers["content-security-policy"].startswith("default-src 'none'")
    assert "script-src" in page.headers["content-security-policy"]
    assert document.headers["content-security-policy"] == API_CONTENT_SECURITY_POLICY


@pytest.mark.parametrize("path", ["/api/v1/redoc", "/docs", "/redoc"])
async def test_swagger_and_redoc_are_not_served(app: FastAPI, path: str) -> None:
    async with client_for(app) as client:
        response = await client.get(path)

    assert response.status_code == 404


async def test_docs_disabled_serves_neither_page_nor_document(
    settings: Settings,
) -> None:
    app = create_app(settings.model_copy(update={"docs_enabled": False}))

    async with client_for(app) as client:
        page = await client.get(DOCS_PATH)
        document = await client.get(OPENAPI_PATH)

    assert (page.status_code, document.status_code) == (404, 404)


async def test_app_response_carries_request_id_and_security_headers(
    app: FastAPI,
) -> None:
    async with client_for(app) as client:
        response = await client.get("/health/live")

    assert len(response.headers["x-request-id"]) == 36
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == API_CONTENT_SECURITY_POLICY
    assert "x-ratelimit-limit" not in response.headers


async def test_app_untrusted_host_gets_400_with_request_id(app: FastAPI) -> None:
    async with client_for(app, base_url="http://evil.test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 400
    assert response.json()["instance"] == response.headers["x-request-id"]


async def test_app_oversized_body_gets_413_problem(settings: Settings) -> None:
    app = create_app(settings.model_copy(update={"max_request_body_bytes": 1024}))

    async with client_for(app) as client:
        response = await client.post("/api/v1/anything", content=b"x" * 2048)

    assert response.status_code == 413
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_app_api_request_is_rate_limited_with_headers(settings: Settings) -> None:
    container = dataclasses.replace(
        build_container(settings),
        rate_limiter=StaticRateLimiter(is_allowed=False, retry_after_seconds=12),
    )
    app = create_app(settings, container)

    async with client_for(app) as client:
        response = await client.get("/api/v1/not-a-route")

    assert response.status_code == 429
    assert response.headers["retry-after"] == "12"
    assert response.headers["x-request-id"] == response.json()["instance"]


async def test_app_rate_limit_disabled_installs_no_limiter(settings: Settings) -> None:
    limiter = StaticRateLimiter(is_allowed=False)
    container = dataclasses.replace(build_container(settings), rate_limiter=limiter)
    app = create_app(
        settings.model_copy(update={"rate_limit_enabled": False}), container
    )

    async with client_for(app) as client:
        response = await client.get("/api/v1/not-a-route")

    assert response.status_code == 404
    assert limiter.calls == []


async def test_app_cors_preflight_from_configured_origin_is_answered(
    settings: Settings,
) -> None:
    app = create_app(
        settings.model_copy(update={"cors_allow_origins": ["https://yakhnama.org"]})
    )

    async with client_for(app) as client:
        response = await client.options(
            "/api/v1/not-a-route",
            headers={
                "Origin": "https://yakhnama.org",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://yakhnama.org"


@pytest.fixture
def idempotent_app(settings: Settings, clock: FrozenClock) -> FastAPI:
    validator = TokenValidator(
        jwks_client=FakeJwksClient([session_key_pair()]),
        issuer=TEST_ISSUER,
        audience=TEST_AUDIENCE,
        algorithms=["RS256"],
        leeway_seconds=0,
        clock=clock,
    )
    container = dataclasses.replace(
        build_container(settings),
        clock=clock,
        token_validator=validator,
        idempotency_store=InMemoryIdempotencyStore(),
    )
    app = create_app(settings, container)
    created: list[str] = []

    async def create_thing(principal: CurrentPrincipal) -> dict[str, int]:
        created.append(principal.subject)
        return {"number": len(created)}

    app.add_api_route("/api/v1/things", create_thing, methods=["POST"], status_code=201)
    return app


async def test_app_idempotent_post_replays_and_conflicts(
    idempotent_app: FastAPI,
) -> None:
    token = issue_token(access_token_claims(), session_key_pair())
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": IDEMPOTENCY_KEY}

    async with client_for(idempotent_app) as client:
        first = await client.post("/api/v1/things", json={"a": 1}, headers=headers)
        replay = await client.post("/api/v1/things", json={"a": 1}, headers=headers)
        conflict = await client.post("/api/v1/things", json={"a": 2}, headers=headers)

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json() == first.json() == {"number": 1}
    assert replay.headers["idempotent-replayed"] == "true"
    assert conflict.status_code == 409


async def test_app_anonymous_post_with_key_gets_route_401(
    idempotent_app: FastAPI,
) -> None:
    async with client_for(idempotent_app) as client:
        response = await client.post(
            "/api/v1/things", json={}, headers={"Idempotency-Key": IDEMPOTENCY_KEY}
        )

    assert response.status_code == 401


def test_create_app_without_explicit_environment_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # caplog rather than capture_logs: create_app reconfigures structlog first.
    with caplog.at_level(logging.WARNING, logger="yakhnama.main"):
        create_app(Settings(_env_file=None, log_format="console"))

    events = [
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict)
        and record.msg.get("event") == "environment_defaulted"
    ]
    assert events[0]["environment"] == "development"


def test_create_app_with_explicit_environment_logs_no_warning(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="yakhnama.main"):
        create_app(settings)

    assert "environment_defaulted" not in caplog.text


def test_create_app_stores_readiness_cache(app: FastAPI) -> None:
    cache = app.state.readiness_cache

    assert isinstance(cache, ReadinessCache)


async def test_readiness_cache_reuses_result_within_ttl(clock: FrozenClock) -> None:
    cache = ReadinessCache(clock, ttl=timedelta(seconds=1))
    calls: list[int] = []

    async def check() -> CheckStatus:
        calls.append(1)
        return "ok"

    first = await cache.get(check)
    clock.advance(timedelta(milliseconds=999))
    second = await cache.get(check)
    clock.advance(timedelta(milliseconds=1))
    third = await cache.get(check)

    assert (first, second, third) == ("ok", "ok", "ok")
    assert len(calls) == 2


def test_get_readiness_cache_without_create_app_raises() -> None:
    request = Request({"type": "http", "app": FastAPI()})

    with pytest.raises(RuntimeError, match="readiness_cache"):
        get_readiness_cache(request)


def test_build_token_validator_without_issuer_returns_none(
    settings: Settings, clock: FrozenClock
) -> None:
    validator, http_client = build_token_validator(settings, clock)

    assert (validator, http_client) == (None, None)


async def test_build_token_validator_with_issuer_owns_http_client(
    settings: Settings, clock: FrozenClock
) -> None:
    configured = settings.model_copy(update={"oidc_issuer": TEST_ISSUER})

    validator, http_client = build_token_validator(configured, clock)

    assert isinstance(validator, TokenValidator)
    assert isinstance(http_client, httpx.AsyncClient)
    assert http_client.follow_redirects is False
    await http_client.aclose()


def test_build_rate_limiter_memory_backend_has_no_client(
    settings: Settings, clock: FrozenClock
) -> None:
    limiter, redis = build_rate_limiter(settings, clock)

    assert isinstance(limiter, InMemoryRateLimiter)
    assert redis is None


async def test_build_rate_limiter_redis_backend_owns_client(
    settings: Settings, clock: FrozenClock
) -> None:
    configured = settings.model_copy(
        update={
            "rate_limit_backend": "redis",
            "redis_url": RedisDsn("redis://127.0.0.1:1/0"),
        }
    )

    limiter, redis = build_rate_limiter(configured, clock)

    assert isinstance(limiter, RedisRateLimiter)
    assert isinstance(redis, Redis)
    await redis.aclose()


async def test_build_container_binds_idempotency_store_and_closes_clients(
    settings: Settings,
) -> None:
    configured = settings.model_copy(
        update={
            "oidc_issuer": TEST_ISSUER,
            "rate_limit_backend": "redis",
            "redis_url": RedisDsn("redis://127.0.0.1:1/0"),
        }
    )
    container: Container = build_container(configured)

    await container.aclose()

    assert isinstance(container.idempotency_store, SqlAlchemyIdempotencyStore)
    assert container.http_client is not None
    assert container.http_client.is_closed


def test_snapshot_settings_ignore_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YAKHNAMA_APP_NAME", "Local Override")

    settings = snapshot_settings()

    assert settings.app_name == "Yakhnama"
    assert settings.docs_enabled is True


def test_render_openapi_snapshot_is_deterministic_sorted_json() -> None:
    first = render_openapi_snapshot()
    second = render_openapi_snapshot()

    document = json.loads(first)
    assert first == second
    assert first.endswith("\n")
    assert list(document) == sorted(document)
    assert "/health/live" in document["paths"]


def test_write_openapi_snapshot_creates_file(tmp_path: Path) -> None:
    output = tmp_path / "contract" / "openapi.json"

    write_openapi_snapshot(output)

    assert output.read_text(encoding="utf-8") == render_openapi_snapshot()


def test_openapi_snapshot_main_writes_to_output_argument(tmp_path: Path) -> None:
    output = tmp_path / "openapi.json"

    main(["--output", str(output)])

    assert json.loads(output.read_text(encoding="utf-8"))["openapi"].startswith("3.")


def test_openapi_snapshot_module_runs_as_script(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "openapi.json"
    monkeypatch.setattr(sys, "argv", ["openapi_snapshot", "--output", str(output)])
    monkeypatch.delitem(sys.modules, "yakhnama.platform.openapi_snapshot")

    runpy.run_module("yakhnama.platform.openapi_snapshot", run_name="__main__")

    assert output.is_file()
