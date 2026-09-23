"""Unit tests for ``yakhnama.modules.provenance.domain.factories``."""

from datetime import UTC, datetime

from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.provenance.domain.events import SourceRegistered
from yakhnama.modules.provenance.domain.factories import SourceFactory
from yakhnama.modules.provenance.domain.value_objects import (
    SYSTEM_OWNER,
    Licence,
    SourceDetails,
    SourceOwner,
    SourceType,
)

NOW = datetime(2026, 9, 1, tzinfo=UTC)


def test_source_factory_register_creates_unreferenced_version_one() -> None:
    ids = SequentialIdGenerator()
    owner_ids = SequentialIdGenerator(seed=7)
    owner = SourceOwner(actor_id=owner_ids.new_id(), organization_id=owner_ids.new_id())
    details = SourceDetails(
        title="PMD daily bulletin",
        citation="Pakistan Meteorological Department, daily bulletin",
        url="https://example.test/bulletin",
        licence=Licence.custom("Terms of the publisher"),
    )

    change = SourceFactory().register(
        SourceType.GOVERNMENT, details, owner, clock=FrozenClock(NOW), ids=ids
    )

    source = change.state
    assert source.id == ids.issued[0]
    assert source.source_type is SourceType.GOVERNMENT
    assert source.details == details
    assert source.owner == owner
    assert not source.is_referenced
    assert source.version == 1
    assert source.created_at == source.updated_at == NOW
    assert change.events == (
        SourceRegistered(
            event_id=ids.issued[1],
            occurred_at=NOW,
            aggregate_id=source.id,
            version=1,
            source_type=SourceType.GOVERNMENT,
            owner_actor_id=owner.actor_id,
            organization_id=owner.organization_id,
        ),
    )


def test_source_factory_register_system_owner_has_no_actor() -> None:
    details = SourceDetails(title="GLOF inventory", citation="Dataset citation")

    change = SourceFactory().register(
        SourceType.DATASET,
        details,
        SYSTEM_OWNER,
        clock=FrozenClock(NOW),
        ids=SequentialIdGenerator(),
    )

    assert change.state.owner_actor_id is None
    assert change.state.organization_id is None
    registered = change.events[0]
    assert isinstance(registered, SourceRegistered)
    assert registered.owner_actor_id is None
