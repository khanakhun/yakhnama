"""Unit tests for ``yakhnama.platform.settings``."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from yakhnama.platform.settings import (
    ASYNC_POSTGRES_SCHEME,
    DEVELOPMENT_DATABASE_URL,
    Settings,
    get_settings,
)


def test_settings_without_environment_uses_documented_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_name == "Yakhnama"
    assert settings.environment == "development"
    assert settings.log_level == "INFO"
    assert settings.log_format == "json"
    assert settings.cors_allow_origins == []
    assert settings.public_coordinate_decimals == 2


def test_settings_log_level_env_var_overrides_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YAKHNAMA_LOG_LEVEL", "DEBUG")

    settings = Settings(_env_file=None)

    assert settings.log_level == "DEBUG"


def test_settings_invalid_log_level_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YAKHNAMA_LOG_LEVEL", "VERBOSE")

    with pytest.raises(ValidationError, match="log_level"):
        Settings(_env_file=None)


def test_settings_unknown_key_in_env_file_raises_validation_error(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("YAKHNAMA_UNKNOWN_SETTING=1\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="extra_forbidden"):
        Settings(_env_file=env_file)


def test_settings_unprefixed_key_in_env_file_is_ignored(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "POSTGRES_USER=yakhnama\nYAKHNAMA_LOG_LEVEL=WARNING\n", encoding="utf-8"
    )

    settings = Settings(_env_file=env_file)

    assert settings.log_level == "WARNING"


def test_settings_cors_origins_env_var_is_parsed_from_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "YAKHNAMA_CORS_ALLOW_ORIGINS",
        '["https://yakhnama.org", "http://localhost:3000"]',
    )

    settings = Settings(_env_file=None)

    assert settings.cors_allow_origins == [
        "https://yakhnama.org",
        "http://localhost:3000",
    ]


@pytest.mark.parametrize("decimals", [0, 6])
def test_settings_coordinate_decimals_at_bounds_is_accepted(decimals: int) -> None:
    settings = Settings(_env_file=None, public_coordinate_decimals=decimals)

    assert settings.public_coordinate_decimals == decimals


@pytest.mark.parametrize("decimals", [-1, 7])
def test_settings_coordinate_decimals_out_of_bounds_raises_validation_error(
    decimals: int,
) -> None:
    with pytest.raises(ValidationError, match="public_coordinate_decimals"):
        Settings(_env_file=None, public_coordinate_decimals=decimals)


def test_get_settings_called_twice_returns_cached_instance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    first = get_settings()
    second = get_settings()

    assert first is second


def test_get_settings_after_cache_clear_reads_environment_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    first = get_settings()
    monkeypatch.setenv("YAKHNAMA_LOG_LEVEL", "ERROR")

    get_settings.cache_clear()
    second = get_settings()

    assert first.log_level == "INFO"
    assert second.log_level == "ERROR"


_ENV_EXAMPLE = Path(__file__).resolve().parents[3] / ".env.example"


def test_settings_loaded_from_env_example_equals_defaults() -> None:
    from_example = Settings(_env_file=_ENV_EXAMPLE)

    assert from_example == Settings(_env_file=None)


def test_env_example_documents_every_settings_field() -> None:
    lines = _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    documented = {
        line.split("=", 1)[0].removeprefix("YAKHNAMA_").lower()
        for line in lines
        if line.startswith("YAKHNAMA_")
    }

    missing = set(Settings.model_fields) - documented

    assert missing == set()


def test_settings_without_environment_uses_documented_platform_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.database_url == DEVELOPMENT_DATABASE_URL
    assert settings.database_pool_size == 5
    assert settings.database_echo is False
    assert settings.otel_enabled is False
    assert settings.otel_exporter == "none"
    assert settings.otel_exporter_endpoint is None
    assert settings.otel_service_name == "yakhnama"
    assert settings.docs_enabled is True
    assert settings.health_ready_timeout_seconds == 2.0


def test_settings_database_url_env_var_with_asyncpg_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "YAKHNAMA_DATABASE_URL", "postgresql+asyncpg://app:pw@db.internal:6543/records"
    )

    settings = Settings(_env_file=None)

    assert settings.database_url.scheme == ASYNC_POSTGRES_SCHEME
    assert settings.database_url.path == "/records"


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://app:pw@127.0.0.1:5432/records",
        "postgresql+psycopg://app:pw@127.0.0.1:5432/records",
    ],
)
def test_settings_database_url_without_asyncpg_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    monkeypatch.setenv("YAKHNAMA_DATABASE_URL", database_url)

    with pytest.raises(ValidationError, match="postgresql\\+asyncpg"):
        Settings(_env_file=None)


def test_settings_database_url_not_postgres_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YAKHNAMA_DATABASE_URL", "mysql://app:pw@127.0.0.1/records")

    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)


def test_settings_invalid_database_url_error_does_not_echo_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "YAKHNAMA_DATABASE_URL", "postgresql://app:hunter2-secret@127.0.0.1/records"
    )

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)

    assert "hunter2-secret" not in str(caught.value)


def test_settings_repr_does_not_contain_database_password() -> None:
    settings = Settings(_env_file=None)

    rendered = repr(settings)

    assert "yakhnama-dev-only" not in rendered
    assert "database_url" not in rendered


@pytest.mark.parametrize("pool_size", [1, 64])
def test_settings_pool_size_at_bounds_is_accepted(pool_size: int) -> None:
    settings = Settings(_env_file=None, database_pool_size=pool_size)

    assert settings.database_pool_size == pool_size


@pytest.mark.parametrize("pool_size", [0, 65])
def test_settings_pool_size_out_of_bounds_raises_validation_error(
    pool_size: int,
) -> None:
    with pytest.raises(ValidationError, match="database_pool_size"):
        Settings(_env_file=None, database_pool_size=pool_size)


@pytest.mark.parametrize("timeout", [0.1, 30.0])
def test_settings_ready_timeout_at_bounds_is_accepted(timeout: float) -> None:
    settings = Settings(_env_file=None, health_ready_timeout_seconds=timeout)

    assert settings.health_ready_timeout_seconds == timeout


@pytest.mark.parametrize("timeout", [0.09, 30.1])
def test_settings_ready_timeout_out_of_bounds_raises_validation_error(
    timeout: float,
) -> None:
    with pytest.raises(ValidationError, match="health_ready_timeout_seconds"):
        Settings(_env_file=None, health_ready_timeout_seconds=timeout)


def test_settings_unknown_otel_exporter_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YAKHNAMA_OTEL_EXPORTER", "jaeger")

    with pytest.raises(ValidationError, match="otel_exporter"):
        Settings(_env_file=None)


def test_settings_empty_otel_endpoint_env_var_becomes_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YAKHNAMA_OTEL_EXPORTER_ENDPOINT", "")

    settings = Settings(_env_file=None)

    assert settings.otel_exporter_endpoint is None


def test_settings_otel_endpoint_env_var_is_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YAKHNAMA_OTEL_EXPORTER_ENDPOINT", "http://127.0.0.1:4318")

    settings = Settings(_env_file=None)

    assert settings.otel_exporter_endpoint == "http://127.0.0.1:4318"


def test_settings_otel_endpoint_over_512_characters_raises_validation_error() -> None:
    with pytest.raises(ValidationError, match="otel_exporter_endpoint"):
        Settings(_env_file=None, otel_exporter_endpoint="http://x/" + "a" * 504)


def test_settings_empty_otel_service_name_raises_validation_error() -> None:
    with pytest.raises(ValidationError, match="otel_service_name"):
        Settings(_env_file=None, otel_service_name="")


def test_settings_seed_defaults_are_reference_dir_and_no_actor() -> None:
    settings = Settings(_env_file=None)

    assert settings.reference_data_dir == Path("data/reference")
    assert settings.seed_actor_id is None


def test_settings_reference_data_dir_need_not_exist_at_load(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = tmp_path / "missing"
    monkeypatch.setenv("YAKHNAMA_REFERENCE_DATA_DIR", str(missing))

    settings = Settings(_env_file=None)

    assert settings.reference_data_dir == missing


def test_settings_seed_actor_id_uuid7_env_var_is_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor_id = "01920000-0000-7000-8000-000000000001"
    monkeypatch.setenv("YAKHNAMA_SEED_ACTOR_ID", actor_id)

    settings = Settings(_env_file=None)

    assert str(settings.seed_actor_id) == actor_id


def test_settings_seed_actor_id_empty_env_var_means_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YAKHNAMA_SEED_ACTOR_ID", "")

    settings = Settings(_env_file=None)

    assert settings.seed_actor_id is None


def test_settings_seed_actor_id_not_uuid7_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YAKHNAMA_SEED_ACTOR_ID", "6f1c2b0e-4a5d-4c3b-9a8e-1d2c3b4a5f60")

    with pytest.raises(ValidationError, match="seed_actor_id"):
        Settings(_env_file=None)
