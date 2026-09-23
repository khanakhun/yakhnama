"""Typed application settings loaded from the environment.

Configuration comes only from ``YAKHNAMA_*`` environment variables (and a local
``.env`` file for development), never from literals in code, so that no secret or
deployment-specific value is committed (``AGENTS.md`` §4, hard rules).

Patterns: Settings (pydantic-settings ``BaseSettings``, proposed in ADR 0011).
"""

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Final, Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    Field,
    PostgresDsn,
    RedisDsn,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from yakhnama.shared_kernel.ids import EntityId

Environment = Literal["development", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LogFormat = Literal["json", "console"]
TelemetryExporter = Literal["none", "console", "otlp"]
# The allow-list of JWS algorithms a token may be signed with (ADR 0015). Symmetric
# algorithms (HS*) and "none" are impossible to configure, not merely off by default.
SigningAlgorithm = Literal["RS256", "ES256"]
RateLimitBackend = Literal["memory", "redis"]
TaskQueueBackend = Literal["memory", "redis"]
# "noop" scans nothing and is refused in production; "clamav" streams each original
# to a clamd daemon (modules/media/infrastructure/adapters/scanner.py).
MalwareScannerBackend = Literal["noop", "clamav"]

# The only driver the async engine in ``platform/db.py`` supports (ADR 0004).
ASYNC_POSTGRES_SCHEME: Final = "postgresql+asyncpg"
# Matches the development credentials in ``docker-compose.yml``; real deployments
# override it through ``YAKHNAMA_DATABASE_URL``. The port must equal
# ``POSTGRES_HOST_PORT``.
DEVELOPMENT_DATABASE_URL: Final = PostgresDsn(
    "postgresql+asyncpg://yakhnama:yakhnama-dev-only@127.0.0.1:5432/yakhnama"
)

# Match the MinIO service and its development credentials in docker-compose.yml
# (MINIO_HOST_PORT, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD); real deployments override
# them through YAKHNAMA_STORAGE_*. The production guard refuses the secret below.
DEVELOPMENT_STORAGE_ENDPOINT_URL: Final = "http://127.0.0.1:9000"
DEVELOPMENT_STORAGE_ACCESS_KEY_ID: Final = "minioadmin"
DEVELOPMENT_STORAGE_SECRET: Final = "minioadmin-dev-only"  # noqa: S105  # reason: the public development credential of docker-compose.yml, refused in production
# S3 bucket naming rules: 3-63 lower-case letters, digits, dots and hyphens.
BUCKET_NAME_PATTERN: Final = r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$"

# An origin is scheme + host + port; 2048 matches the common URL length ceiling and
# keeps a malformed environment value from becoming an unbounded allocation.
CorsOrigin = Annotated[str, Field(min_length=1, max_length=2048)]
# 253 is the longest DNS name; a leading "*." allows one wildcard level.
TrustedHost = Annotated[str, Field(min_length=1, max_length=255)]

# Hosts a plain-http URL may point at: a developer's own machine, never the network.
LOOPBACK_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1"})
# "testserver" is the host of Starlette's TestClient and "test" the host of the
# in-process httpx client in tests/conftest.py; neither resolves on a real network.
DEVELOPMENT_TRUSTED_HOSTS: Final = (
    "localhost",
    "127.0.0.1",
    "::1",
    "testserver",
    "test",
)
# The Scalar bundle location. scalar-fastapi ships no JavaScript, so the bundle comes
# from this CDN unless an operator points it at a self-hosted copy (ADR 0014).
SCALAR_CDN_URL: Final = "https://cdn.jsdelivr.net/npm/@scalar/api-reference"
# Hosts of the in-process test clients; never valid for a deployed service.
TEST_CLIENT_HOSTS: Final = frozenset({"testserver", "test"})
# A dotted path into the token claims, e.g. "realm_access.roles".
CLAIM_PATH_PATTERN: Final = r"^[A-Za-z0-9_:-]+(\.[A-Za-z0-9_:-]+)*$"
KIBIBYTE: Final = 1024
MEBIBYTE: Final = 1024 * KIBIBYTE


def _default_algorithms() -> list[SigningAlgorithm]:
    # RS256 is what Keycloak and Zitadel sign access tokens with by default.
    return ["RS256"]


def is_secure_or_loopback_url(url: str) -> bool:
    """Tell whether ``url`` is ``https`` or plain ``http`` on a loopback host.

    Args:
        url: An absolute URL or origin.

    Returns:
        ``True`` for ``https://...`` with a host, or ``http://`` on ``localhost``,
        ``127.0.0.1`` or ``::1``; ``False`` for anything else, including a URL
        with no host.
    """
    parts = urlsplit(url)
    if not parts.hostname:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS


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
        docs_scalar_js_url: Where the browser loads the Scalar bundle from; the CDN
            by default, a self-hosted copy in hardened deployments (ADR 0014).
        oidc_issuer: The identity provider's issuer URL, compared exactly with the
            token's ``iss`` claim. ``None`` disables bearer authentication (every
            protected route answers 401). Must be ``https`` except on a loopback
            host.
        oidc_audience: The ``aud`` value every access token must carry.
        oidc_jwks_url: The JWKS endpoint. When ``None`` it is read lazily from the
            issuer's ``/.well-known/openid-configuration`` on the first request.
        oidc_allowed_algorithms: The JWS algorithms a token may be signed with.
        oidc_leeway_seconds: Clock skew tolerated on ``exp``, ``nbf`` and ``iat``.
        oidc_http_timeout_seconds: Timeout for each discovery or JWKS request.
        jwks_cache_ttl_seconds: How long fetched signing keys are trusted before
            they are fetched again.
        idempotency_ttl_hours: How long a stored ``Idempotency-Key`` response is
            replayed before it is purged (ADR 0016).
        rate_limit_enabled: Apply the per-client rate limits (ADR 0017).
        rate_limit_backend: ``memory`` (one process, development and tests) or
            ``redis`` (shared by every process).
        redis_url: Redis DSN for the ``redis`` backend. It may carry a password,
            so it is excluded from ``repr`` and must never be logged.
        redis_socket_timeout_seconds: Connect and read timeout of each Redis call;
            a slow Redis then fails open quickly instead of stalling requests.
        rate_limit_anonymous_per_minute: Requests per minute for one client IP
            without a valid token.
        rate_limit_authenticated_per_minute: Requests per minute for one principal.
        trusted_hosts: ``Host`` header values the API answers; anything else gets
            400. ``*.example.org`` allows one wildcard subdomain level.
        max_request_body_bytes: Largest request body accepted; larger bodies get
            413 before the route runs.
        request_id_header: Header that carries the request id in and out.
        task_queue_backend: ``memory`` (tasks run inside the process that enqueues
            them; development and tests) or ``redis`` (a Redis stream consumed by
            ``poe worker``; required in production). ``redis`` needs ``redis_url``.
        outbox_relay_interval_seconds: How often the scheduler enqueues
            ``outbox.relay_once``.
        outbox_batch_size: Outbox messages claimed per relay run.
        outbox_max_attempts: Delivery attempts after which a message is
            dead-lettered.
        outbox_lease_seconds: How long a claimed message is reserved for the relay
            that claimed it; after that another relay may claim it again. Must be
            at least ``outbox_subscriber_timeout_seconds``.
        outbox_subscriber_timeout_seconds: Upper bound for one subscriber call; a
            slower call is cancelled and counts as a failed attempt.
        outbox_retention_days: Published outbox messages older than this are
            deleted by ``outbox.purge_published``; pending and dead-lettered
            messages are never purged.
        idempotency_purge_interval_minutes: How often the scheduler enqueues
            ``idempotency.purge_expired``.
        storage_endpoint_url: The S3-compatible endpoint; an empty value means
            AWS's regional default. Must be ``https`` except on a loopback host.
        storage_region: The region requests are signed for.
        storage_access_key_id: The storage access key id; excluded from ``repr``.
        storage_secret_access_key: The storage secret key; a ``SecretStr``, so it
            is masked in ``repr``, logs and validation errors.
        storage_private_bucket: Bucket of the unchanged, private originals.
        storage_public_bucket: Bucket of the metadata-stripped public copies; must
            differ from the private bucket.
        storage_presign_ttl_seconds: Lifetime of every presigned upload and
            download URL.
        malware_scanner: ``noop`` (development and tests; scans nothing, refused
            in production) or ``clamav`` (needs ``clamav_host``).
        clamav_host: Host of the clamd daemon for the ``clamav`` scanner.
        clamav_port: clamd's TCP port.
        clamav_timeout_seconds: Upper bound for one whole scan.
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

    docs_scalar_js_url: str = Field(default=SCALAR_CDN_URL, max_length=2048)

    oidc_issuer: str | None = Field(default=None, min_length=1, max_length=2048)
    oidc_audience: str = Field(default="yakhnama-api", min_length=1, max_length=255)
    oidc_jwks_url: str | None = Field(default=None, min_length=1, max_length=2048)
    oidc_allowed_algorithms: list[SigningAlgorithm] = Field(
        default_factory=_default_algorithms, min_length=1, max_length=2
    )
    oidc_roles_claim: str = Field(
        default="realm_access.roles", max_length=200, pattern=CLAIM_PATH_PATTERN
    )
    # A minute is generous for NTP-synchronised hosts; more would let an expired
    # token live on noticeably past its lifetime.
    oidc_leeway_seconds: int = Field(default=10, ge=0, le=60)
    oidc_http_timeout_seconds: float = Field(default=5.0, ge=0.5, le=30.0)
    jwks_cache_ttl_seconds: int = Field(default=600, ge=60, le=86_400)

    idempotency_ttl_hours: int = Field(default=72, ge=1, le=168)

    rate_limit_enabled: bool = True
    rate_limit_backend: RateLimitBackend = "memory"
    redis_url: RedisDsn | None = Field(default=None, repr=False)
    # A rate-limit check sits on every request; a quarter second is far above a
    # healthy Redis round trip and bounds the added latency when it is not.
    redis_socket_timeout_seconds: float = Field(default=0.25, ge=0.05, le=5.0)
    # Operational defaults, not domain facts: generous for a person or a script,
    # tight enough that one client cannot starve the rest. Tuned per deployment.
    rate_limit_anonymous_per_minute: int = Field(default=60, ge=1, le=100_000)
    rate_limit_authenticated_per_minute: int = Field(default=300, ge=1, le=100_000)

    trusted_hosts: list[TrustedHost] = Field(
        default_factory=lambda: list(DEVELOPMENT_TRUSTED_HOSTS),
        min_length=1,
        max_length=50,
    )
    max_request_body_bytes: int = Field(default=MEBIBYTE, ge=KIBIBYTE, le=64 * MEBIBYTE)
    request_id_header: str = Field(
        default="X-Request-ID", pattern=r"^[A-Za-z][A-Za-z0-9-]{0,63}$"
    )

    # Background work (ADR 0007, ADR 0008). Every default below is a proposed
    # operational value, not a domain fact, recorded as an open question in the
    # Phase 3 report until the maintainer confirms it.
    task_queue_backend: TaskQueueBackend = "memory"
    # Five seconds bounds how long a committed change waits for its side effects
    # (audit, best figures) while costing one indexed query per interval when idle.
    outbox_relay_interval_seconds: int = Field(default=5, ge=1, le=60)
    outbox_batch_size: int = Field(default=100, ge=1, le=1000)
    outbox_max_attempts: int = Field(default=5, ge=1, le=100)
    outbox_lease_seconds: int = Field(default=120, ge=5, le=3600)
    outbox_subscriber_timeout_seconds: int = Field(default=30, ge=1, le=300)
    outbox_retention_days: int = Field(default=30, ge=1, le=365)
    idempotency_purge_interval_minutes: int = Field(default=60, ge=5, le=1440)

    # Object storage (ADR 0009). The development values match docker-compose.yml.
    storage_endpoint_url: str | None = Field(
        default=DEVELOPMENT_STORAGE_ENDPOINT_URL, min_length=1, max_length=2048
    )
    storage_region: str = Field(default="us-east-1", pattern=r"^[a-z0-9-]{1,32}$")
    storage_access_key_id: str = Field(
        default=DEVELOPMENT_STORAGE_ACCESS_KEY_ID,
        min_length=1,
        max_length=128,
        repr=False,
    )
    storage_secret_access_key: SecretStr = Field(
        default=SecretStr(DEVELOPMENT_STORAGE_SECRET), min_length=1, max_length=256
    )
    storage_private_bucket: str = Field(
        default="yakhnama-media-private", pattern=BUCKET_NAME_PATTERN
    )
    storage_public_bucket: str = Field(
        default="yakhnama-media-public", pattern=BUCKET_NAME_PATTERN
    )
    # Proposed: 15 minutes lets a phone on a weak network start a 50 MiB upload
    # while keeping a leaked link short-lived. S3 checks expiry when a request
    # starts, so a slow upload that started in time still completes.
    storage_presign_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    malware_scanner: MalwareScannerBackend = "noop"
    clamav_host: str | None = Field(default=None, min_length=1, max_length=255)
    clamav_port: int = Field(default=3310, ge=1, le=65_535)
    # Proposed: a 50 MiB file streams to a local clamd in seconds; a minute leaves
    # room for a loaded daemon without holding a worker indefinitely.
    clamav_timeout_seconds: float = Field(default=60.0, ge=1.0, le=600.0)

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

    @field_validator(
        "otel_exporter_endpoint",
        "seed_actor_id",
        "oidc_issuer",
        "oidc_jwks_url",
        "redis_url",
        "storage_endpoint_url",
        "clamav_host",
        mode="before",
    )
    @classmethod
    def _empty_value_means_none(cls, value: object) -> object:
        # An environment variable cannot hold None; an empty value is its spelling.
        return None if value == "" else value

    @field_validator(
        "oidc_issuer", "oidc_jwks_url", "docs_scalar_js_url", "storage_endpoint_url"
    )
    @classmethod
    def _require_secure_url(cls, url: str | None) -> str | None:
        # Keys and issuer metadata fetched over plain http could be swapped by anyone
        # on the path, which would let them mint accepted tokens.
        if url is not None and not is_secure_or_loopback_url(url):
            message = "the URL must use https (plain http only on a loopback host)"
            raise ValueError(message)
        return url

    @field_validator("oidc_allowed_algorithms", mode="before")
    @classmethod
    def _deduplicate_algorithms(cls, value: object) -> object:
        # Before validation, so a repeated entry does not count against the length
        # bound; the list's type is checked right after.
        return list(dict.fromkeys(value)) if isinstance(value, list) else value

    @model_validator(mode="after")
    def _require_redis_url_for_redis_backend(self) -> Self:
        if self.rate_limit_backend == "redis" and self.redis_url is None:
            message = "redis_url is required when rate_limit_backend is 'redis'"
            raise ValueError(message)
        if self.task_queue_backend == "redis" and self.redis_url is None:
            message = "redis_url is required when task_queue_backend is 'redis'"
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _require_consistent_media_storage(self) -> Self:
        # One bucket for both would put every private original, EXIF and GPS
        # included, behind whatever access the public copies get.
        if self.storage_private_bucket == self.storage_public_bucket:
            message = "storage_private_bucket and storage_public_bucket must differ"
            raise ValueError(message)
        if self.malware_scanner == "clamav" and self.clamav_host is None:
            message = "clamav_host is required when malware_scanner is 'clamav'"
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _require_lease_to_cover_one_subscriber(self) -> Self:
        # A lease shorter than one subscriber call would expire during every slow
        # call, so a second relay would deliver the same message concurrently.
        if self.outbox_lease_seconds < self.outbox_subscriber_timeout_seconds:
            message = (
                "outbox_lease_seconds must be at least "
                "outbox_subscriber_timeout_seconds"
            )
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _guard_production(self) -> Self:
        # Every rule is checked and reported at once, by field name only: a value may
        # be a secret (database_url), and one message listing every problem saves an
        # operator a redeploy per mistake.
        if self.environment != "production":
            return self
        problems = production_problems(self)
        if problems:
            message = "unsafe production settings: " + "; ".join(problems)
            raise ValueError(message)
        return self


def production_problems(settings: Settings) -> list[str]:
    """List every rule of the production guard that ``settings`` break.

    The rules come from the Phase 1 security review (``docs/plans/phase-2.md`` §2).
    They are evaluated whatever the environment, so they can be tested one by one;
    ``Settings`` enforces them only when ``environment`` is ``"production"``.

    Args:
        settings: The settings to inspect.

    Returns:
        One short message per broken rule, naming the field but never its value.
    """
    return [message for is_broken, message in PRODUCTION_RULES if is_broken(settings)]


def _has_unsafe_cors_origin(settings: Settings) -> bool:
    return any(
        origin == "*" or not origin.startswith("https://")
        for origin in settings.cors_allow_origins
    )


def _has_test_client_host(settings: Settings) -> bool:
    return bool(
        TEST_CLIENT_HOSTS.intersection(host.lower() for host in settings.trusted_hosts)
    )


# (is the rule broken?, message naming the field). A table rather than a chain of
# ifs: each rule reads on one line and is tested on its own.
PRODUCTION_RULES: Final[tuple[tuple[Callable[[Settings], bool], str], ...]] = (
    (
        lambda settings: settings.database_url == DEVELOPMENT_DATABASE_URL,
        "database_url must not be the development default",
    ),
    (lambda settings: settings.docs_enabled, "docs_enabled must be false"),
    (lambda settings: settings.log_format != "json", "log_format must be 'json'"),
    (lambda settings: settings.log_level == "DEBUG", "log_level must not be 'DEBUG'"),
    (lambda settings: settings.database_echo, "database_echo must be false"),
    (
        lambda settings: settings.otel_exporter == "console",
        "otel_exporter must not be 'console'",
    ),
    (
        _has_unsafe_cors_origin,
        "cors_allow_origins must hold only https origins, never '*'",
    ),
    (lambda settings: settings.oidc_issuer is None, "oidc_issuer must be set"),
    (
        lambda settings: (
            settings.oidc_issuer is not None
            and not settings.oidc_issuer.startswith("https://")
        ),
        "oidc_issuer must use https",
    ),
    (
        lambda settings: not settings.rate_limit_enabled,
        "rate_limit_enabled must be true",
    ),
    (
        lambda settings: settings.rate_limit_backend != "redis",
        "rate_limit_backend must be 'redis'",
    ),
    (
        lambda settings: settings.task_queue_backend != "redis",
        "task_queue_backend must be 'redis'",
    ),
    (
        lambda settings: "*" in settings.trusted_hosts,
        "trusted_hosts must not contain '*'",
    ),
    (
        _has_test_client_host,
        "trusted_hosts must not contain the test client hosts",
    ),
    (
        lambda settings: settings.malware_scanner == "noop",
        "malware_scanner must not be 'noop'",
    ),
    (
        lambda settings: (
            settings.storage_secret_access_key.get_secret_value()
            == DEVELOPMENT_STORAGE_SECRET
        ),
        "storage_secret_access_key must not be the development default",
    ),
    (
        lambda settings: (
            settings.storage_endpoint_url is not None
            and not settings.storage_endpoint_url.startswith("https://")
        ),
        "storage_endpoint_url must use https",
    ),
)


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
