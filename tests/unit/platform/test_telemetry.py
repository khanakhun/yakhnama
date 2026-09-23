"""Unit tests for ``yakhnama.platform.telemetry``; spans stay in memory."""

from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from yakhnama.main import create_app
from yakhnama.platform.settings import Settings, TelemetryExporter
from yakhnama.platform.telemetry import (
    REDACTED,
    OpenTelemetryAdapter,
    PersonalDataSpanProcessor,
    configure_telemetry,
    scrub_personal_data,
    strip_query,
)
from yakhnama.shared_kernel.errors import ValidationError

CLIENT_ADDRESS = "203.0.113.9"


def _settings(
    *,
    otel_enabled: bool = False,
    otel_exporter: TelemetryExporter = "none",
    otel_service_name: str = "yakhnama",
) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        log_format="console",
        otel_enabled=otel_enabled,
        otel_exporter=otel_exporter,
        otel_service_name=otel_service_name,
    )


@pytest.fixture
def traced_app() -> Iterator[tuple[FastAPI, InMemorySpanExporter]]:
    app = create_app(_settings(otel_enabled=True))

    async def search(q: str) -> dict[str, str]:
        return {"echo": q}

    app.add_api_route("/search", search)
    adapter = app.state.telemetry
    assert isinstance(adapter, OpenTelemetryAdapter)
    exporter = InMemorySpanExporter()
    adapter.tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))

    yield app, exporter

    adapter.shutdown()


async def _get(app: FastAPI, url: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=(CLIENT_ADDRESS, 51000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(url, headers={"User-Agent": "reporter-phone/1.0"})


def _attribute_values(spans: tuple[ReadableSpan, ...]) -> list[str]:
    return [str(value) for span in spans for value in (span.attributes or {}).values()]


def test_configure_telemetry_disabled_returns_none_and_instruments_nothing(
    app: FastAPI,
) -> None:
    telemetry = app.state.telemetry

    assert telemetry is None
    assert SQLAlchemyInstrumentor().is_instrumented_by_opentelemetry is False


def test_configure_telemetry_otlp_exporter_raises_validation_error() -> None:
    settings = _settings(otel_enabled=True, otel_exporter="otlp")

    with pytest.raises(ValidationError, match="opentelemetry-exporter-otlp"):
        create_app(settings)

    assert SQLAlchemyInstrumentor().is_instrumented_by_opentelemetry is False


def test_configure_telemetry_console_exporter_returns_adapter_that_shuts_down() -> None:
    app = create_app(_settings(otel_enabled=True, otel_exporter="console"))
    adapter = app.state.telemetry
    assert isinstance(adapter, OpenTelemetryAdapter)

    is_instrumented = SQLAlchemyInstrumentor().is_instrumented_by_opentelemetry
    adapter.shutdown()

    assert is_instrumented is True
    assert SQLAlchemyInstrumentor().is_instrumented_by_opentelemetry is False


def test_configure_telemetry_resource_carries_service_name() -> None:
    app = create_app(_settings(otel_enabled=True, otel_service_name="yakhnama-test"))
    adapter = app.state.telemetry
    assert isinstance(adapter, OpenTelemetryAdapter)

    attributes = adapter.tracer_provider.resource.attributes
    adapter.shutdown()

    assert attributes["service.name"] == "yakhnama-test"


async def test_traced_request_spans_omit_query_string_client_address_and_agent(
    traced_app: tuple[FastAPI, InMemorySpanExporter],
) -> None:
    app, exporter = traced_app

    response = await _get(app, "/search?q=hunza-reporter")

    spans = exporter.get_finished_spans()
    values = _attribute_values(spans)
    assert response.status_code == 200
    assert spans
    assert not [value for value in values if "hunza-reporter" in value]
    assert not [value for value in values if CLIENT_ADDRESS in value]
    assert not [value for value in values if "reporter-phone" in value]
    assert REDACTED in values


async def test_traced_request_server_span_keeps_path_and_route(
    traced_app: tuple[FastAPI, InMemorySpanExporter],
) -> None:
    app, exporter = traced_app

    await _get(app, "/search?q=hunza")

    server_spans = [
        span
        for span in exporter.get_finished_spans()
        if span.kind is trace.SpanKind.SERVER
    ]
    (server_span,) = server_spans
    attributes = server_span.attributes or {}
    assert attributes["http.target"] == "/search"
    assert attributes["http.url"] == "http://test/search"
    assert attributes["http.route"] == "/search"


async def test_traced_health_probe_produces_no_spans(
    traced_app: tuple[FastAPI, InMemorySpanExporter],
) -> None:
    app, exporter = traced_app

    await _get(app, "/health/live")

    assert exporter.get_finished_spans() == ()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://test/places?q=hunza#top", "http://test/places"),
        ("/places?q=hunza", "/places"),
        ("/places", "/places"),
    ],
)
def test_strip_query_removes_query_and_fragment(url: str, expected: str) -> None:
    stripped = strip_query(url)

    assert stripped == expected


def test_personal_data_span_processor_on_start_scrubs_initial_attributes() -> None:
    provider = TracerProvider()
    provider.add_span_processor(PersonalDataSpanProcessor())
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer(__name__)

    with tracer.start_as_current_span(
        "request",
        attributes={
            "url.full": "https://api.test/reports?reporter=ali",
            "url.query": "reporter=ali",
            "client.address": CLIENT_ADDRESS,
            "http.request.method": "GET",
        },
    ):
        pass

    (span,) = exporter.get_finished_spans()
    assert dict(span.attributes or {}) == {
        "url.full": "https://api.test/reports",
        "url.query": REDACTED,
        "client.address": REDACTED,
        "http.request.method": "GET",
    }
    provider.shutdown()


def test_scrub_personal_data_non_recording_span_is_left_alone() -> None:
    span = trace.NonRecordingSpan(trace.INVALID_SPAN_CONTEXT)

    scrub_personal_data(span)

    assert span.is_recording() is False


def test_configure_telemetry_disabled_directly_returns_none(app: FastAPI) -> None:
    container = app.state.container

    telemetry = configure_telemetry(_settings(), app, container.engine)

    assert telemetry is None
