"""The ``TaskQueue`` port: schedule background work without knowing the queue.

Application code enqueues named tasks through ``TaskQueue``; the Taskiq adapter lives in
``platform`` and is bound in the composition root (ADR 0008). Nothing in a module
imports Taskiq.

Delivery contract every adapter and every task honours:

- **At least once.** A task can run more than once (broker redelivery, a worker crash
  after the work but before the acknowledgement, a caller retrying ``enqueue``).
  Every task is idempotent; ``idempotency_key`` is handed to the task so it can
  recognise a repeat. Adapters may also drop a duplicate enqueue with a key they have
  seen, but callers must not rely on that.
- **Not transactional.** ``enqueue`` talks to the broker, not to the database, so it is
  not part of any unit of work. Work that must happen because a transaction
  committed is triggered through a domain event and the outbox (ADR 0007), whose
  relay may then enqueue; a task never assumes the row it was told about exists.
- **Ids, never snapshots.** A payload carries identifiers and non-personal scalar
  fields only (the outbox payload contract from the Phase 2 security review). The
  task reloads current state inside its own unit of work, so a stale payload cannot
  overwrite newer data and no personal data sits in the broker. ``ScheduledTask``
  enforces the shape: at most ``TASK_PAYLOAD_MAX_FIELDS`` snake_case keys whose
  values are UUIDs, integers, booleans, ``None`` or short strings; floats are refused
  because they are how coordinates, and so reporter locations, would leak.

Patterns: Adapter (port side), Value Object, DTO.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Final, Protocol
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_serializer,
    field_validator,
)

TASK_NAME_PATTERN: Final = r"^[a-z][a-z0-9_.]{1,63}$"

TaskName = Annotated[str, StringConstraints(pattern=TASK_NAME_PATTERN)]
"""Routing name of a task, 2 to 64 characters: ``outbox.relay``, ``idempotency.purge``.

Lower-case ASCII letters, digits, ``_`` and ``.``, starting with a letter; by
convention ``<bounded_context_or_platform_area>.<verb_phrase>``.
"""

TASK_ID_MAX_LENGTH: Final = 128
# Printable ASCII without spaces: adapters assign opaque ids (Taskiq uses hex UUIDs),
# and the restriction keeps them safe in logs and headers.
TASK_ID_PATTERN: Final = r"^[\x21-\x7e]{1,128}$"

IDEMPOTENCY_KEY_MAX_LENGTH: Final = 255
TaskIdempotencyKey = Annotated[str, StringConstraints(pattern=r"^[\x21-\x7e]{1,255}$")]
"""Caller-chosen key identifying one logical piece of work, printable ASCII."""

# Proposed operational defaults, not domain facts: a week covers every retention and
# purge schedule planned so far, and longer delays belong in a scheduler.
MAX_DELAY_SECONDS: Final = 7 * 24 * 60 * 60
TASK_PAYLOAD_MAX_FIELDS: Final = 16
TASK_PAYLOAD_STRING_MAX_LENGTH: Final = 200
TASK_PAYLOAD_KEY_PATTERN: Final = r"^[a-z][a-z0-9_]{0,63}$"

TaskPayloadKey = Annotated[str, StringConstraints(pattern=TASK_PAYLOAD_KEY_PATTERN)]
TaskPayloadString = Annotated[
    str, StringConstraints(max_length=TASK_PAYLOAD_STRING_MAX_LENGTH)
]
# Strict int and bool so ``True`` is not stored as ``1`` or ``"1"`` as ``1``; UUID
# before str so an id given as a UUID stays one.
type TaskPayloadValue = UUID | StrictBool | StrictInt | TaskPayloadString | None


class TaskId(BaseModel):
    """The identifier a queue assigned to one enqueued task.

    Opaque to the application; used only to correlate logs and results.

    Implements: Value Object.

    Attributes:
        value: The adapter's id, 1 to 128 printable ASCII characters.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: Annotated[str, StringConstraints(pattern=TASK_ID_PATTERN)]

    def __str__(self) -> str:
        """Return the raw id.

        Returns:
            ``value``.
        """
        return self.value


class ScheduledTask(BaseModel):
    """One task as handed to the queue: what runs, with which ids, and when.

    Adapters validate every ``enqueue`` call into this model before talking to the
    broker, and the in-memory Fake records these, so the payload contract is enforced
    in one place.

    Implements: DTO (proposed in ADR 0012).

    Attributes:
        task_id: The id the queue assigned.
        task_name: Which task runs.
        payload: Ids and non-personal scalars, read-only.
        idempotency_key: Key the task uses to recognise a repeat, if any.
        delay_seconds: Seconds to wait before the task becomes runnable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: TaskId
    task_name: TaskName
    payload: Mapping[TaskPayloadKey, TaskPayloadValue] = Field(
        default_factory=dict, max_length=TASK_PAYLOAD_MAX_FIELDS
    )
    idempotency_key: TaskIdempotencyKey | None = None
    delay_seconds: int = Field(default=0, ge=0, le=MAX_DELAY_SECONDS)

    @field_validator("payload", mode="after")
    @classmethod
    def _freeze_payload(
        cls, payload: Mapping[str, TaskPayloadValue]
    ) -> Mapping[str, TaskPayloadValue]:
        return MappingProxyType(dict(payload))

    @field_serializer("payload")
    def _serialise_payload(
        self, payload: Mapping[str, TaskPayloadValue]
    ) -> dict[str, TaskPayloadValue]:
        return dict(payload)

    def __hash__(self) -> int:
        """Hash by content, as the generated hash cannot hash a mapping.

        Returns:
            A hash equal for equal tasks.
        """
        return hash(
            (
                self.task_id,
                self.task_name,
                frozenset(self.payload.items()),
                self.idempotency_key,
                self.delay_seconds,
            )
        )


class TaskQueue(Protocol):
    """Schedules background tasks; bound to the Taskiq adapter in the composition root.

    Implements: Adapter (port side).
    """

    async def enqueue(
        self,
        task_name: str,
        payload: Mapping[str, object],
        *,
        idempotency_key: str | None = None,
        delay_seconds: int = 0,
    ) -> TaskId:
        """Hand one task to the queue.

        Asynchronous because every real adapter talks to a broker over the network.

        Args:
            task_name: A ``TaskName``.
            payload: Ids and non-personal scalars; validated as
                ``ScheduledTask.payload``.
            idempotency_key: Key passed to the task so a repeat can be recognised.
            delay_seconds: Seconds, 0 to ``MAX_DELAY_SECONDS``, before it may run.

        Returns:
            The id the queue assigned.

        Raises:
            pydantic.ValidationError: If the arguments break the ``ScheduledTask``
                contract.
        """
        ...
