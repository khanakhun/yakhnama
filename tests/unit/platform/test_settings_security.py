"""Unit tests for the Phase 2 settings: OIDC, rate limits, HTTP limits, the guard."""

from typing import Any

import pytest
from pydantic import PostgresDsn, RedisDsn, ValidationError

from yakhnama.platform.settings import (
    DEVELOPMENT_TRUSTED_HOSTS,
    MEBIBYTE,
    SCALAR_CDN_URL,
    Settings,
    is_secure_or_loopback_url,
    production_problems,
)

PRODUCTION_DATABASE_URL = PostgresDsn(
    "postgresql+asyncpg://yakhnama:secret@db.internal:5432/yakhnama"
)


def _safe_production_values() -> dict[str, Any]:
    # Any: keyword arguments of mixed types for the Settings constructor.
    return {
        "environment": "production",
        "database_url": PRODUCTION_DATABASE_URL,
        "docs_enabled": False,
        "log_format": "json",
        "otel_exporter": "none",
        "cors_allow_origins": ["https://yakhnama.org"],
        "oidc_issuer": "https://id.yakhnama.org/realms/yakhnama",
        "rate_limit_enabled": True,
        "rate_limit_backend": "redis",
        "task_queue_backend": "redis",
        "redis_url": RedisDsn("redis://cache.internal:6379/0"),
        "trusted_hosts": ["api.yakhnama.org"],
    }


def test_settings_new_fields_without_environment_use_documented_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.oidc_issuer is None
    assert settings.oidc_audience == "yakhnama-api"
    assert settings.oidc_jwks_url is None
    assert settings.oidc_allowed_algorithms == ["RS256"]
    assert settings.oidc_leeway_seconds == 10
    assert settings.oidc_http_timeout_seconds == 5.0
    assert settings.jwks_cache_ttl_seconds == 600
    assert settings.idempotency_ttl_hours == 72
    assert settings.rate_limit_enabled is True
    assert settings.rate_limit_backend == "memory"
    assert settings.redis_url is None
    assert settings.rate_limit_anonymous_per_minute == 60
    assert settings.rate_limit_authenticated_per_minute == 300
    assert settings.trusted_hosts == list(DEVELOPMENT_TRUSTED_HOSTS)
    assert settings.max_request_body_bytes == MEBIBYTE
    assert settings.request_id_header == "X-Request-ID"
    assert settings.docs_scalar_js_url == SCALAR_CDN_URL
    assert settings.oidc_roles_claim == "realm_access.roles"
    assert settings.redis_socket_timeout_seconds == 0.25


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://id.example.org/realms/x", True),
        ("http://localhost:8080/realms/x", True),
        ("http://127.0.0.1:8080", True),
        ("http://[::1]:8080", True),
        ("http://id.example.org", False),
        ("ftp://id.example.org", False),
        ("https://", False),
        ("not a url", False),
    ],
)
def test_is_secure_or_loopback_url_classifies_urls(url: str, *, expected: bool) -> None:
    result = is_secure_or_loopback_url(url)

    assert result is expected


@pytest.mark.parametrize(
    "field", ["oidc_issuer", "oidc_jwks_url", "docs_scalar_js_url"]
)
def test_settings_plain_http_url_on_public_host_raises_validation_error(
    field: str,
) -> None:
    with pytest.raises(ValidationError, match="https"):
        Settings.model_validate({field: "http://id.example.org/x"})


@pytest.mark.parametrize("field", ["oidc_issuer", "oidc_jwks_url", "redis_url"])
def test_settings_empty_env_value_means_none(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    monkeypatch.setenv(f"YAKHNAMA_{field.upper()}", "")

    settings = Settings(_env_file=None)

    assert getattr(settings, field) is None


@pytest.mark.parametrize("algorithms", [[], ["HS256"], ["none"], ["RS512"]])
def test_settings_algorithms_outside_allow_list_raise_validation_error(
    algorithms: list[str],
) -> None:
    with pytest.raises(ValidationError, match="oidc_allowed_algorithms"):
        Settings.model_validate({"oidc_allowed_algorithms": algorithms})


def test_settings_duplicate_algorithms_are_deduplicated_in_order() -> None:
    settings = Settings(
        _env_file=None, oidc_allowed_algorithms=["ES256", "RS256", "ES256"]
    )

    assert settings.oidc_allowed_algorithms == ["ES256", "RS256"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("oidc_leeway_seconds", -1),
        ("oidc_leeway_seconds", 61),
        ("jwks_cache_ttl_seconds", 59),
        ("jwks_cache_ttl_seconds", 86_401),
        ("idempotency_ttl_hours", 0),
        ("idempotency_ttl_hours", 169),
        ("max_request_body_bytes", 1023),
        ("max_request_body_bytes", 64 * MEBIBYTE + 1),
        ("rate_limit_anonymous_per_minute", 0),
        ("rate_limit_authenticated_per_minute", 100_001),
        ("oidc_http_timeout_seconds", 0.1),
        ("request_id_header", "X Request Id"),
        ("request_id_header", ""),
        ("trusted_hosts", []),
        ("redis_socket_timeout_seconds", 0.01),
        ("redis_socket_timeout_seconds", 5.1),
        ("oidc_roles_claim", "roles..admin"),
        ("oidc_roles_claim", "realm access"),
        ("oidc_roles_claim", ""),
    ],
)
def test_settings_value_out_of_bounds_raises_validation_error(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError, match=field):
        Settings.model_validate({field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("oidc_leeway_seconds", 0),
        ("oidc_leeway_seconds", 60),
        ("jwks_cache_ttl_seconds", 60),
        ("jwks_cache_ttl_seconds", 86_400),
        ("idempotency_ttl_hours", 1),
        ("idempotency_ttl_hours", 168),
        ("max_request_body_bytes", 1024),
        ("max_request_body_bytes", 64 * MEBIBYTE),
    ],
)
def test_settings_value_at_bounds_is_accepted(field: str, value: int) -> None:
    settings = Settings.model_validate({field: value})

    assert getattr(settings, field) == value


def test_settings_redis_backend_without_url_raises_validation_error() -> None:
    with pytest.raises(ValidationError, match="redis_url is required"):
        Settings(_env_file=None, rate_limit_backend="redis")


def test_settings_redis_url_is_hidden_from_repr() -> None:
    settings = Settings(
        _env_file=None,
        rate_limit_backend="redis",
        redis_url=RedisDsn("redis://:hunter2@cache:6379/0"),
    )

    text = repr(settings)

    assert "hunter2" not in text


def test_settings_trusted_hosts_env_var_is_parsed_from_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YAKHNAMA_TRUSTED_HOSTS", '["api.yakhnama.org"]')

    settings = Settings(_env_file=None)

    assert settings.trusted_hosts == ["api.yakhnama.org"]


def test_settings_safe_production_values_are_accepted() -> None:
    settings = Settings(_env_file=None, **_safe_production_values())

    assert settings.environment == "production"
    assert production_problems(settings) == []


@pytest.mark.parametrize(
    ("override", "problem"),
    [
        ({"database_url": None}, "database_url"),
        ({"docs_enabled": True}, "docs_enabled"),
        ({"log_format": "console"}, "log_format"),
        ({"otel_exporter": "console"}, "otel_exporter"),
        ({"cors_allow_origins": ["*"]}, "cors_allow_origins"),
        ({"cors_allow_origins": ["http://yakhnama.org"]}, "cors_allow_origins"),
        ({"oidc_issuer": None}, "oidc_issuer"),
        ({"rate_limit_enabled": False}, "rate_limit_enabled"),
        ({"rate_limit_backend": "memory"}, "rate_limit_backend"),
        ({"task_queue_backend": "memory"}, "task_queue_backend"),
        ({"trusted_hosts": ["*"]}, "trusted_hosts"),
        ({"trusted_hosts": ["api.yakhnama.org", "testserver"]}, "trusted_hosts"),
        ({"trusted_hosts": ["TEST"]}, "trusted_hosts"),
        ({"cors_allow_origins": ["http://localhost:3000"]}, "cors_allow_origins"),
        ({"oidc_issuer": "http://localhost:8080/realms/yakhnama"}, "oidc_issuer"),
        ({"log_level": "DEBUG"}, "log_level"),
        ({"database_echo": True}, "database_echo"),
    ],
)
def test_settings_production_guard_each_rule_rejects_its_unsafe_value(
    override: dict[str, object], problem: str
) -> None:
    values = _safe_production_values()
    values.update(override)
    if values["database_url"] is None:
        del values["database_url"]

    with pytest.raises(ValidationError, match=f"unsafe production settings: {problem}"):
        Settings(_env_file=None, **values)


def test_settings_production_guard_each_rule_is_reported_on_its_own() -> None:
    values = _safe_production_values()
    values["log_level"] = "DEBUG"

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, **values)

    message = str(caught.value)
    assert "log_level must not be 'DEBUG'" in message
    assert "database_echo" not in message
    assert "oidc_issuer" not in message


def test_settings_production_guard_reports_every_problem_without_values() -> None:
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, environment="production")

    message = str(caught.value)
    assert "database_url must not be the development default" in message
    assert "docs_enabled must be false" in message
    assert "oidc_issuer must be set" in message
    assert "rate_limit_backend must be 'redis'" in message
    assert "yakhnama-dev-only" not in message


@pytest.mark.parametrize("environment", ["development", "test"])
def test_settings_unsafe_values_outside_production_are_accepted(
    environment: str,
) -> None:
    settings = Settings.model_validate(
        {"environment": environment, "cors_allow_origins": ["*"]}
    )

    assert production_problems(settings) != []


def test_settings_environment_from_env_var_counts_as_explicitly_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YAKHNAMA_ENVIRONMENT", "test")

    settings = Settings(_env_file=None)

    assert "environment" in settings.model_fields_set


def test_settings_environment_omitted_is_not_in_fields_set() -> None:
    settings = Settings(_env_file=None)

    assert "environment" not in settings.model_fields_set
