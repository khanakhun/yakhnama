"""Unit tests for the Phase 3 task queue and outbox relay settings."""

import pytest
from pydantic import RedisDsn, ValidationError

from yakhnama.platform.settings import Settings


def test_settings_task_and_outbox_fields_use_documented_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.task_queue_backend == "memory"
    assert settings.outbox_relay_interval_seconds == 5
    assert settings.outbox_batch_size == 100
    assert settings.outbox_max_attempts == 5
    assert settings.outbox_lease_seconds == 120
    assert settings.outbox_subscriber_timeout_seconds == 30
    assert settings.outbox_retention_days == 30
    assert settings.idempotency_purge_interval_minutes == 60


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outbox_relay_interval_seconds", 0),
        ("outbox_relay_interval_seconds", 61),
        ("outbox_batch_size", 0),
        ("outbox_batch_size", 1001),
        ("outbox_max_attempts", 0),
        ("outbox_max_attempts", 101),
        ("outbox_lease_seconds", 4),
        ("outbox_lease_seconds", 3601),
        ("outbox_subscriber_timeout_seconds", 0),
        ("outbox_subscriber_timeout_seconds", 301),
        ("outbox_retention_days", 0),
        ("outbox_retention_days", 366),
        ("idempotency_purge_interval_minutes", 4),
        ("idempotency_purge_interval_minutes", 1441),
        ("task_queue_backend", "rabbitmq"),
    ],
)
def test_settings_task_value_out_of_bounds_raises_validation_error(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError, match=field):
        Settings.model_validate({field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outbox_relay_interval_seconds", 1),
        ("outbox_relay_interval_seconds", 60),
        ("outbox_retention_days", 1),
        ("outbox_retention_days", 365),
        ("idempotency_purge_interval_minutes", 5),
        ("idempotency_purge_interval_minutes", 1440),
        ("outbox_subscriber_timeout_seconds", 1),
        ("outbox_lease_seconds", 3600),
    ],
)
def test_settings_task_value_at_bounds_is_accepted(field: str, value: int) -> None:
    settings = Settings.model_validate({field: value})

    assert getattr(settings, field) == value


def test_settings_redis_task_queue_without_url_raises_validation_error() -> None:
    with pytest.raises(ValidationError, match="task_queue_backend is 'redis'"):
        Settings(_env_file=None, task_queue_backend="redis")


def test_settings_redis_task_queue_with_url_is_accepted() -> None:
    settings = Settings(
        _env_file=None,
        task_queue_backend="redis",
        redis_url=RedisDsn("redis://cache:6379/0"),
    )

    assert settings.task_queue_backend == "redis"


def test_settings_lease_shorter_than_subscriber_timeout_raises_validation_error() -> (
    None
):
    with pytest.raises(ValidationError, match="outbox_lease_seconds must be at least"):
        Settings(
            _env_file=None,
            outbox_lease_seconds=20,
            outbox_subscriber_timeout_seconds=21,
        )


def test_settings_task_values_from_env_vars_are_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YAKHNAMA_OUTBOX_RETENTION_DAYS", "90")
    monkeypatch.setenv("YAKHNAMA_TASK_QUEUE_BACKEND", "memory")

    settings = Settings(_env_file=None)

    assert settings.outbox_retention_days == 90
