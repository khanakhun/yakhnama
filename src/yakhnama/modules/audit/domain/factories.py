r"""Turning a domain event into an ``AuditEntry``, and the canonical JSON it hashes.

``from_domain_event`` is the only way entries are built. It copies **ids and codes
only** from the event (``event_id``, ``aggregate_id``, ``aggregate_type``,
``event_type``, ``occurred_at``) and reduces the whole event to ``payload_digest``.
No field of the event's payload is copied, so free text an event might carry never
reaches the audit log, while the digest still proves what the event said: anyone
holding the event can recompute it and compare.

Canonical JSON (``canonical_json``) makes the digest reproducible across processes
and Python versions:

- the model is dumped in Python mode and every value is converted to JSON by
  Pydantic's own serialiser (UUIDs and datetimes as strings, UTC as ``Z``);
- object keys are sorted and the output has no insignificant whitespace;
- **sets are sorted** by their canonical encoding, because a ``frozenset`` iterates
  in an order that depends on the process's hash seed; lists and tuples keep their
  order, which is meaningful;
- non-ASCII characters are escaped (``\uXXXX``), so the bytes do not depend on an
  encoder's Unicode handling.

For an event the object also carries ``event_type``, which is a class attribute and
therefore not part of the dump. Changing any of these rules changes every digest, so
it needs an ADR.

Patterns: Factory.
"""

import json
from collections.abc import Mapping
from typing import Final

from pydantic import BaseModel, TypeAdapter

from yakhnama.modules.audit.domain.entities import AuditEntry
from yakhnama.modules.audit.domain.value_objects import (
    NO_STATE_DIGESTS,
    AuditTarget,
    StateDigests,
    compute_digest,
)
from yakhnama.shared_kernel.events import DomainEvent
from yakhnama.shared_kernel.ids import EntityId, IdGenerator

_JSON_NATIVE: Final = (str, int, float, bool, type(None))


# Serialising through an ``object`` adapter makes Pydantic infer each leaf's runtime
# type (UUID, datetime, Enum, Decimal, ...) and apply its standard JSON form, the same
# form the outbox and the API use, without one adapter per type.
_LEAF_ADAPTER: Final[TypeAdapter[object]] = TypeAdapter(object)


def _encode(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _canonical(value: object) -> object:
    if isinstance(value, BaseModel):
        return _canonical(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        return {str(_canonical(key)): _canonical(item) for key, item in value.items()}
    if isinstance(value, set | frozenset):
        return sorted((_canonical(item) for item in value), key=_encode)
    if isinstance(value, list | tuple):
        return [_canonical(item) for item in value]
    if isinstance(value, _JSON_NATIVE):
        return value
    return _LEAF_ADAPTER.dump_python(value, mode="json")


def canonical_json(model: BaseModel) -> bytes:
    """Return the canonical JSON encoding of ``model`` (rules in the module docs).

    Args:
        model: Any Pydantic model, for example an aggregate state.

    Returns:
        ASCII bytes, identical for equal models in every process.

    Raises:
        ValueError: If the model holds a non-finite float, which has no JSON form.
    """
    return _encode(_canonical(model)).encode("ascii")


def canonical_event_json(event: DomainEvent) -> bytes:
    """Return the canonical JSON of ``event``, including its ``event_type``.

    Args:
        event: The domain event.

    Returns:
        ASCII bytes, identical for equal events in every process.

    Raises:
        ValueError: If the event holds a non-finite float.
    """
    fields = {**event.model_dump(mode="python"), "event_type": event.event_type}
    return _encode(_canonical(fields)).encode("ascii")


def compute_state_digest(state: BaseModel) -> str:
    """Return the digest of an aggregate state, for ``before_digest``/``after_digest``.

    Args:
        state: The aggregate instance before or after a change.

    Returns:
        The ``Digest`` of its canonical JSON.

    Raises:
        ValueError: If the state holds a non-finite float.
    """
    return compute_digest(canonical_json(state))


def from_domain_event(
    event: DomainEvent,
    *,
    actor_id: EntityId | None,
    request_id: str | None,
    ids: IdGenerator,
    state_digests: StateDigests = NO_STATE_DIGESTS,
) -> AuditEntry:
    """Build the audit entry that records ``event``.

    Deterministic apart from the entry's own id: the same event always yields the
    same ``occurred_at``, ``action``, ``target`` and ``payload_digest``. The entry
    time is the event's ``occurred_at``, not the time the subscriber ran, so a
    delayed relay does not move history.

    Args:
        event: The domain event to record.
        actor_id: The user who caused it; ``None`` records a ``system`` actor.
        request_id: Correlation id of the causing request or task, if any.
        ids: Source of the entry id.
        state_digests: Digests of the aggregate state before and after the
            change, if the caller has them (see ``compute_state_digest``); none by
            default.

    Returns:
        The new, immutable entry.

    Raises:
        pydantic.ValidationError: If the event's ``event_type`` is longer than an
            ``AuditAction`` allows (64 characters), its ``aggregate_type`` longer
            than a ``TargetType`` (64), or a digest or request id is malformed.
    """
    return AuditEntry(
        id=ids.new_id(),
        occurred_at=event.occurred_at,
        actor_id=actor_id,
        actor_kind="system" if actor_id is None else "user",
        action=event.event_type,
        target=AuditTarget(
            target_type=event.aggregate_type, target_id=event.aggregate_id
        ),
        before_digest=state_digests.before,
        after_digest=state_digests.after,
        request_id=request_id,
        event_id=event.event_id,
        payload_digest=compute_digest(canonical_event_json(event)),
    )
