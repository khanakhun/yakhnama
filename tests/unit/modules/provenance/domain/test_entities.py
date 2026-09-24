"""Unit tests for ``yakhnama.modules.provenance.domain.entities``."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from tests.factories.provenance import TEST_SOURCE_URL, SourceTestFactory
from tests.fakes.clock import FrozenClock, SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.provenance.domain.entities import Source
from yakhnama.modules.provenance.domain.errors import SourceImmutableError
from yakhnama.modules.provenance.domain.events import (
    SourceDetailsUpdated,
    SourceReferenced,
)
from yakhnama.modules.provenance.domain.value_objects import (
    Licence,
    SourceDetails,
    SourceOwner,
    SourceRef,
)
from yakhnama.shared_kernel.value_objects import DatePrecision, DateWithPrecision

CREATED_AT = datetime(2026, 9, 1, tzinfo=UTC)
CHANGED_AT = datetime(2026, 9, 2, tzinfo=UTC)

new_details = st.builds(
    SourceDetails,
    title=st.sampled_from(["Title A", "Title B"]),
    citation=st.sampled_from(["Citation A", "Citation B"]),
    url=st.sampled_from([None, TEST_SOURCE_URL]),
    publisher=st.sampled_from([None, "Publisher"]),
)


def _clock() -> SteppingClock:
    return SteppingClock(CHANGED_AT, timedelta(seconds=1))


def _source(**fields: object) -> Source:
    return SourceTestFactory.build(
        factory_use_construct=False, **{"created_at": CREATED_AT, **fields}
    )


def _referenced_source() -> Source:
    return _source(is_referenced=True, version=2, updated_at=CHANGED_AT)


# --------------------------------------------------------------------------- #
# Construction                                                                #
# --------------------------------------------------------------------------- #


def test_source_timestamps_in_other_offset_are_normalised_to_utc() -> None:
    plus_five = timezone(timedelta(hours=5))
    local = datetime(2026, 9, 1, 5, tzinfo=plus_five)

    source = _source(created_at=local, updated_at=local)

    assert source.created_at == CREATED_AT
    assert source.created_at.tzinfo is UTC
    assert source.updated_at.tzinfo is UTC


def test_source_updated_before_created_is_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="updated_at"):
        _source(updated_at=CREATED_AT - timedelta(seconds=1))


def test_source_naive_timestamp_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        _source(
            created_at=datetime(2026, 9, 1),  # noqa: DTZ001  # reason: the naive value under test
            updated_at=CREATED_AT,
        )


def test_source_attribute_assignment_is_refused() -> None:
    source = _source()

    with pytest.raises(PydanticValidationError):
        source.title = "Changed"  # type: ignore[misc]  # reason: asserting frozen


# --------------------------------------------------------------------------- #
# Queries                                                                     #
# --------------------------------------------------------------------------- #


def test_source_details_property_returns_descriptive_fields() -> None:
    retrieved = DateWithPrecision(value=CREATED_AT, precision=DatePrecision.MONTH)
    source = _source(
        url=TEST_SOURCE_URL,
        licence=Licence.spdx("CC-BY-4.0"),
        retrieved_at=retrieved,
        publisher="Publisher",
        language="en",
    )

    details = source.details

    assert details == SourceDetails(
        title=source.title,
        citation=source.citation,
        url=TEST_SOURCE_URL,
        licence=Licence.spdx("CC-BY-4.0"),
        retrieved_at=retrieved,
        publisher="Publisher",
        language="en",
    )


def test_source_ref_and_owner_point_back_to_the_source() -> None:
    ids = SequentialIdGenerator()
    actor_id, organization_id = ids.new_id(), ids.new_id()
    source = _source(owner_actor_id=actor_id, organization_id=organization_id)

    ref, owner = source.ref, source.owner

    assert ref == SourceRef(source_id=source.id)
    assert owner == SourceOwner(actor_id=actor_id, organization_id=organization_id)


def test_source_is_mutable_until_referenced() -> None:
    unreferenced, referenced = _source(), _referenced_source()

    assert unreferenced.is_mutable
    assert not referenced.is_mutable


# --------------------------------------------------------------------------- #
# update_details                                                              #
# --------------------------------------------------------------------------- #


def test_source_update_details_changes_fields_and_emits_event() -> None:
    source = _source(publisher="Old publisher")
    details = SourceDetails(
        title=source.title, citation="New citation", url=TEST_SOURCE_URL
    )
    ids = SequentialIdGenerator()

    change = source.update_details(details, clock=_clock(), ids=ids)

    updated = change.state
    assert updated.details == details
    assert updated.publisher is None
    assert updated.version == source.version + 1
    assert updated.updated_at == CHANGED_AT
    assert updated.created_at == source.created_at
    assert updated.id == source.id
    assert change.events == (
        SourceDetailsUpdated(
            event_id=ids.issued[0],
            occurred_at=CHANGED_AT,
            aggregate_id=source.id,
            version=updated.version,
            changed_fields=frozenset({"citation", "url", "publisher"}),
        ),
    )
    assert source.citation != "New citation"


def test_source_update_details_same_details_returns_unchanged_without_events() -> None:
    source = _source()
    clock = FrozenClock(CHANGED_AT)

    change = source.update_details(
        source.details, clock=clock, ids=SequentialIdGenerator()
    )

    assert change.state is source
    assert change.events == ()
    assert clock.calls == 0


def test_source_update_details_clock_before_created_at_is_rejected() -> None:
    source = _source()
    details = SourceDetails(title="Other", citation=source.citation)

    with pytest.raises(PydanticValidationError, match="updated_at"):
        source.update_details(
            details,
            clock=FrozenClock(CREATED_AT - timedelta(days=1)),
            ids=SequentialIdGenerator(),
        )


def test_source_update_details_on_referenced_source_raises_immutable() -> None:
    source = _referenced_source()
    details = SourceDetails(title="Other", citation=source.citation)

    with pytest.raises(SourceImmutableError) as raised:
        source.update_details(details, clock=_clock(), ids=SequentialIdGenerator())

    assert raised.value.details == {"source_id": str(source.id)}


def test_source_update_details_on_referenced_source_same_details_still_raises() -> None:
    source = _referenced_source()

    with pytest.raises(SourceImmutableError):
        source.update_details(
            source.details, clock=_clock(), ids=SequentialIdGenerator()
        )


# --------------------------------------------------------------------------- #
# mark_referenced                                                             #
# --------------------------------------------------------------------------- #


def test_source_mark_referenced_freezes_source_and_emits_event() -> None:
    source = _source()
    ids = SequentialIdGenerator()

    change = source.mark_referenced(clock=_clock(), ids=ids)

    referenced = change.state
    assert referenced.is_referenced
    assert referenced.version == source.version + 1
    assert referenced.updated_at == CHANGED_AT
    assert referenced.details == source.details
    assert change.events == (
        SourceReferenced(
            event_id=ids.issued[0],
            occurred_at=CHANGED_AT,
            aggregate_id=source.id,
            version=referenced.version,
        ),
    )
    assert not source.is_referenced


def test_source_mark_referenced_twice_is_idempotent() -> None:
    first = _source().mark_referenced(clock=_clock(), ids=SequentialIdGenerator())
    clock = FrozenClock(CHANGED_AT + timedelta(days=1))

    second = first.state.mark_referenced(clock=clock, ids=SequentialIdGenerator())

    assert second.state is first.state
    assert second.events == ()
    assert clock.calls == 0


@given(st.lists(new_details, min_size=1, max_size=6))
def test_source_after_reference_every_update_is_refused_and_state_is_stable(
    attempts: list[SourceDetails],
) -> None:
    clock, ids = _clock(), SequentialIdGenerator()
    referenced = _source().mark_referenced(clock=clock, ids=ids).state
    snapshot = referenced.model_dump()

    for details in attempts:
        with pytest.raises(SourceImmutableError):
            referenced.update_details(details, clock=clock, ids=ids)
        again = referenced.mark_referenced(clock=clock, ids=ids)
        assert again.state is referenced
        assert again.events == ()

    assert referenced.model_dump() == snapshot


@given(st.lists(new_details, max_size=6))
def test_source_versions_rise_by_one_per_effective_update(
    attempts: list[SourceDetails],
) -> None:
    clock, ids = _clock(), SequentialIdGenerator()
    source = _source()
    effective = 0

    for details in attempts:
        change = source.update_details(details, clock=clock, ids=ids)
        effective += len(change.events)
        source = change.state

    assert source.version == 1 + effective
