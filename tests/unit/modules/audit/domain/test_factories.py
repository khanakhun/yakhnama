"""Unit tests for ``yakhnama.modules.audit.domain.factories``."""

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import ClassVar

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ConfigDict
from pydantic import ValidationError as PydanticValidationError

from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.audit.domain.factories import (
    canonical_event_json,
    canonical_json,
    compute_state_digest,
    from_domain_event,
)
from yakhnama.modules.audit.domain.value_objects import (
    AuditTarget,
    StateDigests,
    compute_digest,
    is_digest,
)
from yakhnama.shared_kernel.events import DomainEvent
from yakhnama.shared_kernel.value_objects import DatePrecision, DateWithPrecision

OCCURRED_AT = datetime(2026, 9, 1, 12, tzinfo=UTC)
FREE_TEXT_MARKER = "Nasir Khan of Passu, phone 0300-1234567"


class NoteWritten(DomainEvent):
    """A test event carrying free text, a set and a nested value.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "testing.note_written"

    aggregate_type: str = "note"
    note: str
    tags: frozenset[str] = frozenset()
    steps: tuple[str, ...] = ()
    observed: DateWithPrecision | None = None
    amount: float = 0.0


class LongNamedEvent(DomainEvent):
    """A test event whose type is longer than an audit action allows.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "testing." + "x" * 60

    aggregate_type: str = "note"


class Snapshot(BaseModel):
    """A test aggregate state.

    Implements: Entity.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    labels: frozenset[str]


def _event(ids: SequentialIdGenerator | None = None, **fields: object) -> NoteWritten:
    source = ids or SequentialIdGenerator()
    return NoteWritten.model_validate(
        {
            "event_id": source.new_id(),
            "occurred_at": OCCURRED_AT,
            "aggregate_id": source.new_id(),
            "note": FREE_TEXT_MARKER,
            **fields,
        }
    )


# --------------------------------------------------------------------------- #
# Canonical JSON                                                              #
# --------------------------------------------------------------------------- #


def test_canonical_event_json_sorts_keys_and_includes_event_type() -> None:
    event = _event(tags=frozenset({"b", "a"}), steps=("second", "first"))

    encoded = canonical_event_json(event)

    decoded = json.loads(encoded)
    assert list(decoded) == sorted(decoded)
    assert decoded["event_type"] == "testing.note_written"
    assert decoded["tags"] == ["a", "b"]
    assert decoded["steps"] == ["second", "first"]
    assert decoded["occurred_at"] == "2026-09-01T12:00:00Z"
    assert decoded["event_id"] == str(event.event_id)
    assert b" " not in encoded.replace(FREE_TEXT_MARKER.encode(), b"")


def test_canonical_event_json_non_ascii_is_escaped() -> None:
    event = _event(note="گلگت")

    encoded = canonical_event_json(event)

    assert encoded.isascii()
    assert json.loads(encoded)["note"] == "گلگت"


def test_canonical_event_json_nested_model_is_an_object() -> None:
    observed = DateWithPrecision(value=OCCURRED_AT, precision=DatePrecision.DAY)

    decoded = json.loads(canonical_event_json(_event(observed=observed)))

    assert decoded["observed"] == {
        "precision": "day",
        "value": "2026-09-01T12:00:00Z",
    }


def test_canonical_event_json_non_finite_float_is_rejected() -> None:
    event = _event(amount=float("nan"))

    with pytest.raises(ValueError, match="JSON"):
        canonical_event_json(event)


@given(st.lists(st.text(max_size=8), max_size=12, unique=True))
def test_canonical_event_json_set_order_never_changes_the_bytes(
    tags: list[str],
) -> None:
    forwards = _event(tags=frozenset(tags))
    backwards = _event(tags=frozenset(reversed(tags)))

    assert canonical_event_json(forwards) == canonical_event_json(backwards)


def test_canonical_json_sorts_sets_and_mappings_in_any_model() -> None:
    snapshot = Snapshot(name="n", labels=frozenset({"z", "y", "x"}))

    encoded = canonical_json(snapshot)

    assert encoded == b'{"labels":["x","y","z"],"name":"n"}'


@given(st.frozensets(st.text(max_size=6), max_size=8))
def test_compute_state_digest_equal_states_have_equal_digests(
    labels: frozenset[str],
) -> None:
    first = Snapshot(name="state", labels=labels)
    second = Snapshot(name="state", labels=frozenset(sorted(labels, reverse=True)))

    digest = compute_state_digest(first)

    assert digest == compute_state_digest(second)
    assert is_digest(digest)


# --------------------------------------------------------------------------- #
# from_domain_event                                                           #
# --------------------------------------------------------------------------- #


def test_from_domain_event_copies_ids_and_codes_and_digests_payload() -> None:
    event = _event()
    ids = SequentialIdGenerator(seed=3)
    actor_id = SequentialIdGenerator(seed=9).new_id()

    entry = from_domain_event(event, actor_id=actor_id, request_id="req-1", ids=ids)

    assert entry.id == ids.issued[0]
    assert entry.occurred_at == event.occurred_at
    assert entry.actor_id == actor_id
    assert entry.actor_kind == "user"
    assert entry.action == "testing.note_written"
    assert entry.target == AuditTarget(target_type="note", target_id=event.aggregate_id)
    assert entry.event_id == event.event_id
    assert entry.request_id == "req-1"
    assert entry.payload_digest == compute_digest(canonical_event_json(event))
    assert entry.before_digest is None
    assert entry.after_digest is None


def test_from_domain_event_without_actor_records_system() -> None:
    entry = from_domain_event(
        _event(), actor_id=None, request_id=None, ids=SequentialIdGenerator()
    )

    assert entry.actor_kind == "system"
    assert entry.actor_id is None
    assert entry.request_id is None


def test_from_domain_event_state_digests_are_recorded() -> None:
    before = compute_state_digest(Snapshot(name="a", labels=frozenset()))
    after = compute_state_digest(Snapshot(name="b", labels=frozenset()))

    entry = from_domain_event(
        _event(),
        actor_id=None,
        request_id=None,
        ids=SequentialIdGenerator(),
        state_digests=StateDigests(before=before, after=after),
    )

    assert entry.before_digest == before
    assert entry.after_digest == after


def test_from_domain_event_same_event_twice_gives_same_entry_but_new_id() -> None:
    event = _event()
    ids = SequentialIdGenerator()

    first = from_domain_event(event, actor_id=None, request_id=None, ids=ids)
    second = from_domain_event(event, actor_id=None, request_id=None, ids=ids)

    assert first.id != second.id
    assert first.model_dump(exclude={"id"}) == second.model_dump(exclude={"id"})


@given(st.text(max_size=40), st.text(max_size=40))
def test_from_domain_event_digest_differs_exactly_when_payload_differs(
    first_note: str, second_note: str
) -> None:
    ids = SequentialIdGenerator()
    event_id, aggregate_id = ids.new_id(), ids.new_id()
    first = _event(event_id=event_id, aggregate_id=aggregate_id, note=first_note)
    second = _event(event_id=event_id, aggregate_id=aggregate_id, note=second_note)

    first_entry = from_domain_event(first, actor_id=None, request_id=None, ids=ids)
    second_entry = from_domain_event(second, actor_id=None, request_id=None, ids=ids)

    are_equal = first_entry.payload_digest == second_entry.payload_digest
    assert are_equal == (first_note == second_note)


def test_from_domain_event_occurred_at_offset_gives_same_digest_as_utc() -> None:
    ids = SequentialIdGenerator()
    event_id, aggregate_id = ids.new_id(), ids.new_id()
    local = OCCURRED_AT.astimezone(timezone(timedelta(hours=5)))
    in_utc = _event(event_id=event_id, aggregate_id=aggregate_id)
    in_local = _event(event_id=event_id, aggregate_id=aggregate_id, occurred_at=local)

    first = from_domain_event(in_utc, actor_id=None, request_id=None, ids=ids)
    second = from_domain_event(in_local, actor_id=None, request_id=None, ids=ids)

    assert first.payload_digest == second.payload_digest


# No hexadecimal letters, digits or punctuation: a note made of them could match part
# of a digest or an id by chance and fail the test for the wrong reason.
FREE_TEXT_ALPHABET = "ghijklmnopqrstuvwxyzGHIJKLMNOPQRSTUVWXYZ \u06af\u0644\u062a"


@given(st.text(alphabet=FREE_TEXT_ALPHABET, min_size=12, max_size=60))
def test_from_domain_event_never_copies_free_text_into_the_entry(
    note: str,
) -> None:
    event = _event(note=note, tags=frozenset({note}), steps=(note,))

    entry = from_domain_event(
        event, actor_id=None, request_id=None, ids=SequentialIdGenerator()
    )

    serialised = entry.model_dump_json()
    assert note.strip() not in serialised
    payload_fields = set(type(event).model_fields) - {"event_id", "occurred_at"}
    assert set(type(entry).model_fields).isdisjoint(payload_fields)


def test_from_domain_event_marker_text_absent_from_every_entry_value() -> None:
    entry = from_domain_event(
        _event(), actor_id=None, request_id=None, ids=SequentialIdGenerator()
    )

    values = [str(value) for value in entry.model_dump(mode="json").values()]

    assert all(FREE_TEXT_MARKER not in value for value in values)
    assert all("Nasir" not in value for value in values)


def test_from_domain_event_event_type_longer_than_action_is_rejected() -> None:
    ids = SequentialIdGenerator()
    event = LongNamedEvent(
        event_id=ids.new_id(), occurred_at=OCCURRED_AT, aggregate_id=ids.new_id()
    )

    with pytest.raises(PydanticValidationError):
        from_domain_event(event, actor_id=None, request_id=None, ids=ids)


def test_from_domain_event_unsafe_request_id_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        from_domain_event(
            _event(),
            actor_id=None,
            request_id="req\r\nX-Injected: 1",
            ids=SequentialIdGenerator(),
        )
