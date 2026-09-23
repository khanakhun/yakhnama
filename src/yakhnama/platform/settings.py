"""Typed application settings loaded from the environment.

Configuration comes only from ``YAKHNAMA_*`` environment variables (and a local
``.env`` file for development), never from literals in code, so that no secret or
deployment-specific value is committed (``AGENTS.md`` §4, hard rules).

Patterns: Settings (pydantic-settings ``BaseSettings``, proposed in ADR 0011).
"""

import functools
from typing import Annotated, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LogFormat = Literal["json", "console"]

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
    """

    model_config = SettingsConfigDict(
        env_prefix="YAKHNAMA_",
        env_file=".env",
        # The shared .env also holds docker compose variables (POSTGRES_USER, ...).
        # "match_prefix" ignores those while still treating an unknown YAKHNAMA_* key
        # as an extra, which ``extra="forbid"`` rejects, so typos fail loudly.
        dotenv_filtering="match_prefix",
        extra="forbid",
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
