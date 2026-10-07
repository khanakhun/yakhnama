"""The production env example and compose file against ``Settings``.

The example is the operator's starting point for a production deployment (ADR 0023).
These tests keep it honest: once its ``change-me`` placeholders are replaced with
generated secrets it passes the production guard, unfilled it does not start, it
documents every setting, and the compose file hands every setting it names to the
application processes.
"""

import re
import secrets
from pathlib import Path
from typing import Final

import pytest
import yaml
from pydantic import ValidationError

from yakhnama.platform.settings import Settings, production_problems

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]
EXAMPLE_FILE: Final = REPOSITORY_ROOT / "production.env.example"
COMPOSE_FILE: Final = REPOSITORY_ROOT / "docker-compose.production.yml"
PLACEHOLDER: Final = "change-me"
SETTINGS_PREFIX: Final = "YAKHNAMA_"
_REFERENCE: Final = re.compile(r"\$\{(?P<name>[A-Z0-9_]+)\}")


def _parse_env_file(text: str) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines, expanding ``${NAME}`` like docker compose does."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, raw_value = line.partition("=")
        assert separator, f"not KEY=VALUE: {line!r}"
        values[key] = _REFERENCE.sub(lambda match: values[match["name"]], raw_value)
    return values


def _filled_example() -> dict[str, str]:
    """Return the example with every placeholder replaced by its own random value."""
    text = EXAMPLE_FILE.read_text(encoding="utf-8")
    filled = "\n".join(
        line.replace(PLACEHOLDER, secrets.token_urlsafe(48))
        for line in text.splitlines()
    )
    return _parse_env_file(filled)


def _settings_environment(values: dict[str, str]) -> dict[str, str]:
    return {
        key: value for key, value in values.items() if key.startswith(SETTINGS_PREFIX)
    }


def _apply(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for key, value in _settings_environment(values).items():
        monkeypatch.setenv(key, value)


def test_filled_example_passes_the_production_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _apply(monkeypatch, _filled_example())

    settings = Settings(_env_file=None)

    assert settings.environment == "production"
    assert production_problems(settings) == []


def test_filled_example_uses_the_compose_services_and_public_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _filled_example()
    _apply(monkeypatch, values)

    settings = Settings(_env_file=None)

    assert settings.oidc_issuer == "https://auth.example.org/realms/yakhnama"
    assert settings.storage_endpoint_url == "https://files.example.org"
    assert settings.database_url.hosts()[0]["host"] == "postgres"
    assert settings.redis_url is not None
    assert settings.redis_url.host == "redis"
    assert settings.clamav_host == "clamav"
    assert settings.cors_allow_origins == []


def test_unfilled_example_does_not_start(monkeypatch: pytest.MonkeyPatch) -> None:
    text = EXAMPLE_FILE.read_text(encoding="utf-8")
    _apply(monkeypatch, _parse_env_file(text))

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_example_documents_every_settings_field() -> None:
    documented = {
        key.removeprefix(SETTINGS_PREFIX).lower()
        for key in _settings_environment(_filled_example())
    }

    assert documented == set(Settings.model_fields)


def test_compose_passes_every_documented_setting_to_the_application() -> None:
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    passed = {
        name
        for name in compose["x-app-environment"]
        if name.startswith(SETTINGS_PREFIX)
    }

    assert passed == set(_settings_environment(_filled_example()))
