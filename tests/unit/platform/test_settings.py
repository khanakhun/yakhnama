"""Unit tests for ``yakhnama.platform.settings``."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from yakhnama.platform.settings import Settings, get_settings


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
