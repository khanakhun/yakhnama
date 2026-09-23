"""Typed application settings loaded from the environment.

Configuration comes only from ``YAKHNAMA_*`` environment variables (and a local
``.env`` file for development), never from literals in code, so that no secret or
deployment-specific value is committed (``AGENTS.md`` §4, hard rules).

Patterns: Settings (pydantic-settings ``BaseSettings``, proposed in ADR 0011).
"""

import functools
from pathlib import Path
from typing import Annotated, Final, Literal

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from yakhnama.shared_kernel.ids import EntityId

Environment = Literal["development", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LogFormat = Literal["json", "console"]
TelemetryExporter = Literal["none", "console", "otlp"]

# The only driver the async engine in ``platform/db.py`` supports (ADR 0004).
ASYNC_POSTGRES_SCHEME: Final = "postgresql+asyncpg"
# Matches the development credentials in ``docker-compose.yml``; real deployments
# override it through ``YAKHNAMA_DATABASE_URL``. The port must equal
# ``POSTGRES_HOST_PORT``.
DEVELOPMENT_DATABASE_URL: Final = PostgresDsn(
    "postgresql+asyncpg://yakhnama:yakhnama-dev-only@127.0.0.1:5432/yakhnama"
)

# An origin is scheme + host + port; 2048 matches the common URL length ceiling and
# keeps a malformed environment value from becoming an unbounded allocation.
CorsOrigin = Annotated[str, Field(min_length=1, max_length=2048)]


class Settings(BaseSettings):
    """Runtime configuration for the Yakhnama backend.

    Values are read from environment variables prefixed with ``YAKHNAMA_`` (for example
    ``YAKHNAMA_LOG_LEVEL=DEBUG``) and from a ``.env`` file in the working directory.
    Unknown ``YAKHNAMA_*`` keys in the ``.env`` file and unknown constructor arguments
    are rejected (keys without the prefix, such as docker compose variables, are
    ignored), so a typo fails loudly instead of being silently ignored. Complex
    values such as ``cors_allow_origins`` are given as JSON, for example
    ``YAKHNAMA_CORS_ALLOW_ORIGINS='["https://yakhnama.org"]'``.

    Implements: Settings (pydantic-settings ``BaseSettings``, proposed in ADR 0011).
    Configuration is an environment boundary, not a domain concept, so it is neither
    a Value Object nor an Entity even though the model is immutable in use.

    Attributes:
        app_name: Human-readable application name shown in the OpenAPI document.
        environment: Deployment environment; drives environment-specific behaviour.
        log_level: Minimum level emitted by the structured logger.
        log_format: ``json`` for machine-readable logs, ``console`` for local reading.
        cors_allow_origins: Origins allowed by CORS; empty means no cross-origin access.
        public_coordinate_decimals: Decimal places kept when coordinates are published
            on public endpoints, to protect reporter locations (spec §10).
        database_url: PostgreSQL DSN; must use the ``postgresql+asyncpg`` driver. It
            carries a password, so it is excluded from ``repr`` and must never be
            logged.
        database_pool_size: Connections kept open by the engine's pool.
        database_echo: Log every SQL statement (bound parameters are always hidden,
            see ``platform/db.py``); for local debugging only.
        otel_enabled: Turn OpenTelemetry tracing on.
        otel_exporter: Where spans go: ``none`` (collected, not exported),
            ``console`` (stdout) or ``otlp`` (not installed yet, see
            ``platform/telemetry.py``).
        otel_exporter_endpoint: Collector endpoint for the ``otlp`` exporter; an
            empty value means none.
        otel_service_name: ``service.name`` resource attribute on every span.
        docs_enabled: Serve the OpenAPI document and the interactive docs.
        health_ready_timeout_seconds: Upper bound for each readiness check.
        reference_data_dir: Directory holding the versioned reference YAML files
            read by ``python -m yakhnama.seed``. A relative path resolves against the
            working directory. It is checked when the seed reads it, not when the
            settings load, so the API starts without it.
        seed_actor_id: The system actor recorded on every change the seed makes.
            Must be a UUIDv7. When ``None``, each seed run generates a fresh UUIDv7
            system actor and logs it, so its changes can still be told apart. A
            Phase 1 placeholder: Phase 2 replaces it with a real identity.
    """

    model_config = SettingsConfigDict(
        env_prefix="YAKHNAMA_",
        env_file=".env",
        # The shared .env also holds docker compose variables (POSTGRES_USER, ...).
        # "match_prefix" ignores those while still treating an unknown YAKHNAMA_* key
        # as an extra, which ``extra="forbid"`` rejects, so typos fail loudly.
        dotenv_filtering="match_prefix",
        extra="forbid",
        # A validation error would otherwise print the rejected value, and values such
        # as ``database_url`` embed a password.
        hide_input_in_errors=True,
    )

    app_name: str = Field(default="Yakhnama", min_length=1, max_length=100)
    environment: Environment = "development"
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "json"
    cors_allow_origins: list[CorsOrigin] = Field(default_factory=list, max_length=50)
    # Two decimals is roughly 1.1 km at the equator. The final precision is a
    # maintainer decision (reporter safety versus research usefulness); this default is
    # a proposal recorded as an open question, not a domain fact.
    public_coordinate_decimals: int = Field(default=2, ge=0, le=6)

    # repr=False keeps the password out of tracebacks and debug output that print the
    # settings object.
    database_url: PostgresDsn = Field(default=DEVELOPMENT_DATABASE_URL, repr=False)
    # 64 is far above what one API process needs and keeps a typo from exhausting the
    # server's max_connections.
    database_pool_size: int = Field(default=5, ge=1, le=64)
    database_echo: bool = False

    otel_enabled: bool = False
    otel_exporter: TelemetryExporter = "none"
    otel_exporter_endpoint: str | None = Field(default=None, max_length=512)
    otel_service_name: str = Field(default="yakhnama", min_length=1, max_length=100)

    docs_enabled: bool = True
    health_ready_timeout_seconds: float = Field(default=2.0, ge=0.1, le=30.0)

    reference_data_dir: Path = Path("data/reference")
    # EntityId rather than a bare UUID: every actor id in the system is a UUIDv7
    # (ADR 0006), so a wrong value fails at startup instead of inside the seed.
    seed_actor_id: EntityId | None = None

    @field_validator("database_url", mode="after")
    @classmethod
    def _require_asyncpg_driver(cls, database_url: PostgresDsn) -> PostgresDsn:
        # Any other driver would fail later, at the first query, with a less helpful
        # error; the message names the scheme only, never the DSN with its password.
        if database_url.scheme != ASYNC_POSTGRES_SCHEME:
            message = (
                f"database_url must use the {ASYNC_POSTGRES_SCHEME} scheme, "
                f"got {database_url.scheme}"
            )
            raise ValueError(message)
        return database_url

    @field_validator("otel_exporter_endpoint", "seed_actor_id", mode="before")
    @classmethod
    def _empty_value_means_none(cls, value: object) -> object:
        # An environment variable cannot hold None; an empty value is its spelling.
        return None if value == "" else value


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, loading them from the environment once.

    The result is cached so every caller sees the same instance. Tests that change the
    environment must call ``get_settings.cache_clear()`` before and after, which the
    autouse fixture in ``tests/conftest.py`` does.

    Returns:
        The validated settings for this process.

    Raises:
        pydantic.ValidationError: If an environment value is invalid.
    """
    return Settings()
