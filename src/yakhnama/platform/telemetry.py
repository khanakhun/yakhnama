"""OpenTelemetry tracing for the API and the database, off unless configured.

When ``otel_enabled`` is set, ``configure_telemetry`` builds a ``TracerProvider`` with
the ``service.name`` resource, instruments the FastAPI app and the SQLAlchemy engine
with it and, per ``otel_exporter``, exports spans to the console or nowhere. The
provider is passed to each instrumentor explicitly instead of being installed as the
process-global provider, which OpenTelemetry allows to be set only once. The SQLAlchemy
instrumentor is still a process-wide singleton: while one app is instrumented, a second
``configure_telemetry`` leaves its engine uninstrumented (the library logs a warning),
so ``OpenTelemetryAdapter.shutdown`` must run when an app stops (the lifespan does).

Personal data never leaves in a span (``AGENTS.md`` §5, spec §10):
``PersonalDataSpanProcessor`` runs on every span as it starts, and a server request hook
runs again on the HTTP server span; both remove query strings from URL attributes
(search terms and filters can identify a reporter) and replace client address, port and
user agent with ``"[REDACTED]"``. Request and response bodies and headers are never
captured (no ``http_capture_headers_*`` option is set), health probes are excluded, and
SQL spans carry the statement with placeholders only (no bound values) and no SQL
commenter.

The OTLP exporter is not installed (``opentelemetry-exporter-otlp`` is not a
dependency yet), so ``otel_exporter="otlp"`` fails fast with instructions instead of
silently dropping spans.

Patterns: Adapter.
"""

from typing import Any, Final
from urllib.parse import urlsplit, urlunsplit

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SpanExporter,
)
from sqlalchemy.ext.asyncio import AsyncEngine

from yakhnama.platform.settings import Settings
from yakhnama.shared_kernel.errors import ValidationError

REDACTED: Final = "[REDACTED]"

# Probes run every few seconds and would drown real traces; matched as regular
# expressions against the request URL by the instrumentor.
EXCLUDED_URLS: Final = "/health/live,/health/ready"

# Attributes that may carry a query string, under the old and the new HTTP semantic
# conventions; the instrumentor emits either set depending on OTEL_SEMCONV_STABILITY_*.
URL_ATTRIBUTES: Final = frozenset({"http.url", "url.full", "http.target"})
# Attributes whose whole value is personal data: the client's network address and its
# user agent (a fingerprint), plus the query string on its own.
PERSONAL_ATTRIBUTES: Final = frozenset(
    {
        "url.query",
        "net.peer.ip",
        "net.peer.port",
        "http.client_ip",
        "client.address",
        "client.port",
        "http.user_agent",
        "user_agent.original",
    }
)


def strip_query(url: str) -> str:
    """Return ``url`` without its query string and fragment.

    Args:
        url: An absolute URL or a request target such as ``/places?q=hunza``.

    Returns:
        The same URL ending at its path, for example ``/places``.
    """
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def scrub_personal_data(span: trace.Span) -> None:
    """Remove query strings and redact personal attributes on a recording span.

    Args:
        span: Any span; only SDK spans expose their attributes, so a non-recording
            span (for example one whose parent was not sampled) is left alone, as it
            is never exported.
    """
    if not isinstance(span, Span):
        return
    for key, value in list((span.attributes or {}).items()):
        if key in PERSONAL_ATTRIBUTES:
            span.set_attribute(key, REDACTED)
        elif key in URL_ATTRIBUTES and isinstance(value, str):
            span.set_attribute(key, strip_query(value))


# ``scope`` is the raw ASGI scope, a plain dict by the ASGI specification; this is the
# instrumentation boundary, not a Yakhnama layer boundary.
def _scrub_server_span(span: trace.Span, scope: dict[str, Any]) -> None:
    """Scrub the HTTP server span after the ASGI middleware set its attributes.

    The middleware applies the request attributes again after the span has started,
    which would undo ``PersonalDataSpanProcessor.on_start``; this hook runs after that.
    """
    del scope
    scrub_personal_data(span)


class PersonalDataSpanProcessor(SpanProcessor):
    """Scrubs personal data from span attributes before any exporter sees them.

    Instrumentations set their request attributes when the span is created, so
    ``on_start`` sees and can overwrite them. The FastAPI server span is also scrubbed
    by ``_scrub_server_span`` because its middleware re-applies them later.

    Implements: Adapter. It makes third-party instrumentation follow the
    no-personal-data rule.
    """

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        """Remove query strings and redact personal attributes on ``span``.

        Args:
            span: The span that has just started.
            parent_context: Unused; part of the ``SpanProcessor`` interface.
        """
        del parent_context
        scrub_personal_data(span)


class OpenTelemetryAdapter:
    """Holds the tracing set-up of one application and tears it down.

    Implements: Adapter.

    Attributes:
        tracer_provider: The provider every instrumentation of this app uses.
    """

    def __init__(self, tracer_provider: TracerProvider, app: FastAPI) -> None:
        """Wrap an already configured provider and instrumented app.

        Args:
            tracer_provider: The provider built by ``configure_telemetry``.
            app: The instrumented application.
        """
        self.tracer_provider = tracer_provider
        self._app = app

    def shutdown(self) -> None:
        """Remove the instrumentation and flush and stop span export."""
        FastAPIInstrumentor.uninstrument_app(self._app)
        SQLAlchemyInstrumentor().uninstrument()
        self.tracer_provider.shutdown()


def _build_exporter(settings: Settings) -> SpanExporter | None:
    """Return the span exporter selected by ``settings.otel_exporter``."""
    if settings.otel_exporter == "console":
        return ConsoleSpanExporter(service_name=settings.otel_service_name)
    if settings.otel_exporter == "otlp":
        message = (
            "YAKHNAMA_OTEL_EXPORTER=otlp needs the 'opentelemetry-exporter-otlp' "
            "package, which is not installed. Add it with 'poetry add "
            "opentelemetry-exporter-otlp' and wire it in platform/telemetry.py, or use "
            "'console' or 'none'."
        )
        raise ValidationError(message)
    return None


def configure_telemetry(
    settings: Settings, app: FastAPI, engine: AsyncEngine
) -> OpenTelemetryAdapter | None:
    """Instrument ``app`` and ``engine`` with tracing when ``otel_enabled`` is set.

    Must be called before the application starts serving, because the FastAPI
    instrumentation adds a middleware.

    Args:
        settings: Supplies the ``otel_*`` settings.
        app: The application to instrument.
        engine: The application's database engine.

    Returns:
        The adapter to shut down when the application stops, or ``None`` when
        telemetry is disabled.

    Raises:
        ValidationError: If ``otel_exporter`` is ``"otlp"`` (not installed).
    """
    if not settings.otel_enabled:
        return None
    exporter = _build_exporter(settings)
    tracer_provider = TracerProvider(
        resource=Resource.create({SERVICE_NAME: settings.otel_service_name})
    )
    # Scrubbing happens in on_start and exporters act in on_end, so no exporter can
    # ever see an unscrubbed span.
    tracer_provider.add_span_processor(PersonalDataSpanProcessor())
    if exporter is not None:
        tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
    FastAPIInstrumentor.instrument_app(
        app,
        server_request_hook=_scrub_server_span,
        tracer_provider=tracer_provider,
        excluded_urls=EXCLUDED_URLS,
    )
    SQLAlchemyInstrumentor().instrument(
        engine=engine.sync_engine,
        tracer_provider=tracer_provider,
        enable_commenter=False,
    )
    return OpenTelemetryAdapter(tracer_provider, app)
