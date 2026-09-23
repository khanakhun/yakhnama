"""Unit tests for ``yakhnama.shared_kernel.tasks``."""

import inspect
from typing import get_protocol_members
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from yakhnama.shared_kernel.tasks import (
    MAX_DELAY_SECONDS,
    TASK_PAYLOAD_MAX_FIELDS,
    TASK_PAYLOAD_STRING_MAX_LENGTH,
    ScheduledTask,
    TaskId,
    TaskName,
    TaskQueue,
)

EVENT_ID = UUID("01920000-0000-7000-8000-000000000001")
task_name_adapter: TypeAdapter[str] = TypeAdapter(TaskName)
task_id = TaskId(value="3f2a9c")


def _scheduled(**overrides: object) -> ScheduledTask:
    fields: dict[str, object] = {
        "task_id": task_id,
        "task_name": "outbox.relay",
        "payload": {"event_id": EVENT_ID, "attempt": 1},
    }
    fields.update(overrides)
    return ScheduledTask.model_validate(fields)


# --------------------------------------------------------------------------- #
# TaskName and TaskId                                                         #
# --------------------------------------------------------------------------- #


@given(name=st.from_regex(r"\A[a-z][a-z0-9_.]{1,63}\Z"))
def test_task_name_matching_pattern_returns_value(name: str) -> None:
    result = task_name_adapter.validate_python(name)

    assert result == name


@pytest.mark.parametrize(
    "name", ["", "a", "Outbox.relay", "1outbox", "outbox-relay", "a" + "b" * 64]
)
def test_task_name_malformed_raises_validation_error(name: str) -> None:
    with pytest.raises(PydanticValidationError):
        task_name_adapter.validate_python(name)


def test_task_id_str_returns_raw_value() -> None:
    identifier = TaskId(value="abc-123")

    text = str(identifier)

    assert text == "abc-123"


@pytest.mark.parametrize("value", ["", "has space", "x" * 129, "tab\tid"])
def test_task_id_malformed_raises_validation_error(value: str) -> None:
    with pytest.raises(PydanticValidationError):
        TaskId(value=value)


def test_task_id_is_frozen_and_hashable() -> None:
    identifier = TaskId(value="abc")

    with pytest.raises(PydanticValidationError):
        identifier.value = "other"  # type: ignore[misc]  # reason: frozen model

    assert hash(identifier) == hash(TaskId(value="abc"))


# --------------------------------------------------------------------------- #
# ScheduledTask                                                               #
# --------------------------------------------------------------------------- #


def test_scheduled_task_defaults_have_no_key_and_no_delay() -> None:
    task = ScheduledTask(task_id=task_id, task_name="idempotency.purge")

    assert dict(task.payload) == {}
    assert task.idempotency_key is None
    assert task.delay_seconds == 0


def test_scheduled_task_payload_scalar_types_are_preserved() -> None:
    payload = {"event_id": EVENT_ID, "is_retry": True, "attempt": 2, "note": "x"}

    task = _scheduled(payload=payload)

    assert dict(task.payload) == payload
    assert task.payload["is_retry"] is True
    assert isinstance(task.payload["event_id"], UUID)
    assert _scheduled(payload={"count": "1"}).payload["count"] == "1"


def test_scheduled_task_payload_is_read_only() -> None:
    task = _scheduled()

    with pytest.raises(TypeError):
        task.payload["event_id"] = None  # type: ignore[index]  # reason: read-only


@pytest.mark.parametrize(
    "payload",
    [
        {"latitude": 36.3},
        {"snapshot": {"name": "x"}},
        {"ids": [1, 2]},
        {"Bad-Key": 1},
        {"note": "x" * (TASK_PAYLOAD_STRING_MAX_LENGTH + 1)},
        {f"field_{index}": index for index in range(TASK_PAYLOAD_MAX_FIELDS + 1)},
    ],
)
def test_scheduled_task_payload_outside_contract_raises_validation_error(
    payload: dict[str, object],
) -> None:
    with pytest.raises(PydanticValidationError):
        _scheduled(payload=payload)


@pytest.mark.parametrize("delay_seconds", [-1, MAX_DELAY_SECONDS + 1])
def test_scheduled_task_delay_out_of_range_raises_validation_error(
    delay_seconds: int,
) -> None:
    with pytest.raises(PydanticValidationError):
        _scheduled(delay_seconds=delay_seconds)


@pytest.mark.parametrize("key", ["", "with space", "k" * 256])
def test_scheduled_task_malformed_idempotency_key_raises_validation_error(
    key: str,
) -> None:
    with pytest.raises(PydanticValidationError):
        _scheduled(idempotency_key=key)


def test_scheduled_task_json_round_trip_returns_equal_value() -> None:
    task = _scheduled(idempotency_key="relay:2026-09-23", delay_seconds=30)

    restored = ScheduledTask.model_validate_json(task.model_dump_json())

    assert restored == task
    assert hash(restored) == hash(task)
    assert task.model_dump()["payload"] == {"event_id": EVENT_ID, "attempt": 1}


def test_scheduled_task_different_payload_is_not_equal() -> None:
    task = _scheduled()

    other = _scheduled(payload={"event_id": EVENT_ID, "attempt": 2})

    assert task != other


# --------------------------------------------------------------------------- #
# TaskQueue port                                                              #
# --------------------------------------------------------------------------- #


def test_task_queue_protocol_declares_only_enqueue() -> None:
    members = get_protocol_members(TaskQueue)

    assert members == {"enqueue"}


def test_task_queue_enqueue_signature_is_pinned() -> None:
    signature = inspect.signature(TaskQueue.enqueue)

    parameters = {
        name: (parameter.kind, parameter.default)
        for name, parameter in signature.parameters.items()
    }

    assert inspect.iscoroutinefunction(TaskQueue.enqueue)
    assert parameters == {
        "self": (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
        "task_name": (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
        "payload": (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
        "idempotency_key": (inspect.Parameter.KEYWORD_ONLY, None),
        "delay_seconds": (inspect.Parameter.KEYWORD_ONLY, 0),
    }
    assert signature.return_annotation is TaskId
