"""Unit tests for the storage and malware-scanner settings and their guards."""

from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from yakhnama.platform.settings import (
    DEVELOPMENT_STORAGE_ENDPOINT_URL,
    DEVELOPMENT_STORAGE_SECRET,
    Settings,
    production_problems,
)


def test_settings_storage_defaults_match_the_development_stack() -> None:
    settings = Settings(_env_file=None)

    assert settings.storage_endpoint_url == DEVELOPMENT_STORAGE_ENDPOINT_URL
    assert settings.storage_region == "us-east-1"
    assert settings.storage_access_key_id == "minioadmin"
    assert settings.storage_secret_access_key.get_secret_value() == (
        DEVELOPMENT_STORAGE_SECRET
    )
    assert settings.storage_private_bucket == "yakhnama-media-private"
    assert settings.storage_public_bucket == "yakhnama-media-public"
    assert settings.storage_presign_ttl_seconds == 900
    assert settings.malware_scanner == "noop"
    assert settings.clamav_host is None
    assert settings.clamav_port == 3310
    assert settings.clamav_timeout_seconds == 60.0


def test_settings_storage_secret_is_hidden_from_repr() -> None:
    settings = Settings(
        _env_file=None,
        storage_secret_access_key=SecretStr("s3cr3t-value"),
        storage_access_key_id="AKIAEXAMPLEKEYID",
    )

    shown = repr(settings) + str(settings)

    assert "s3cr3t-value" not in shown
    assert "AKIAEXAMPLEKEYID" not in shown


def test_settings_storage_secret_is_hidden_from_validation_errors() -> None:
    with pytest.raises(ValidationError) as caught:
        # model_validate: the raw string arrives as it would from the environment.
        Settings.model_validate({"storage_secret_access_key": "x" * 300})

    assert "x" * 300 not in str(caught.value)


def test_settings_storage_values_are_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YAKHNAMA_STORAGE_SECRET_ACCESS_KEY", "from-env")
    monkeypatch.setenv("YAKHNAMA_STORAGE_ENDPOINT_URL", "")

    settings = Settings(_env_file=None)

    assert settings.storage_secret_access_key.get_secret_value() == "from-env"
    assert settings.storage_endpoint_url is None


@pytest.mark.parametrize("ttl", [59, 3601])
def test_settings_presign_ttl_outside_bounds_is_rejected(ttl: int) -> None:
    with pytest.raises(ValidationError, match="storage_presign_ttl_seconds"):
        Settings(_env_file=None, storage_presign_ttl_seconds=ttl)


@pytest.mark.parametrize("ttl", [60, 3600])
def test_settings_presign_ttl_at_bounds_is_accepted(ttl: int) -> None:
    settings = Settings(_env_file=None, storage_presign_ttl_seconds=ttl)

    assert settings.storage_presign_ttl_seconds == ttl


@pytest.mark.parametrize(
    "override",
    [
        {"storage_endpoint_url": "http://minio.internal:9000"},
        {"storage_private_bucket": "Not_A_Bucket"},
        {"storage_public_bucket": "ab"},
        {"storage_region": "US EAST"},
        {"malware_scanner": "virustotal"},
        {"clamav_port": 0},
    ],
)
def test_settings_invalid_storage_value_is_rejected(override: dict[str, Any]) -> None:
    # Any: keyword arguments of mixed types for the Settings constructor.
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **override)


def test_settings_same_bucket_for_originals_and_copies_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must differ"):
        Settings(
            _env_file=None,
            storage_private_bucket="media",
            storage_public_bucket="media",
        )


def test_settings_clamav_without_host_is_rejected() -> None:
    with pytest.raises(ValidationError, match="clamav_host is required"):
        Settings(_env_file=None, malware_scanner="clamav")


def test_settings_clamav_with_host_is_accepted() -> None:
    settings = Settings(
        _env_file=None, malware_scanner="clamav", clamav_host="clamd.internal"
    )

    assert settings.malware_scanner == "clamav"


def test_production_problems_development_storage_and_noop_scanner_are_reported() -> (
    None
):
    settings = Settings(_env_file=None, environment="test")

    problems = production_problems(settings)

    assert "malware_scanner must not be 'noop'" in problems
    assert "storage_secret_access_key must not be the development default" in problems
    assert "storage_endpoint_url must use https" in problems
    assert DEVELOPMENT_STORAGE_SECRET not in " ".join(problems)


def test_production_problems_hardened_storage_reports_no_storage_problem() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        storage_endpoint_url="https://s3.eu-central-1.amazonaws.com",
        storage_secret_access_key=SecretStr("rotated-secret"),
        storage_access_key_id="rotated-key-id",
        malware_scanner="clamav",
        clamav_host="clamd.internal",
    )

    problems = " ".join(production_problems(settings))

    assert "storage" not in problems
    assert "malware_scanner" not in problems


def test_production_problems_default_aws_endpoint_is_accepted() -> None:
    settings = Settings(_env_file=None, environment="test", storage_endpoint_url="")

    problems = " ".join(production_problems(settings))

    assert "storage_endpoint_url" not in problems
