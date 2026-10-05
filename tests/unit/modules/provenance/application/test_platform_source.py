"""Unit tests for ``RegisterPlatformSource`` and its handler (ADR 0020)."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.provenance import InMemoryProvenanceUnitOfWork
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.modules.provenance.application.commands import RegisterPlatformSource
from yakhnama.modules.provenance.application.handlers import (
    RegisterPlatformSourceHandler,
)
from yakhnama.modules.provenance.domain.events import (
    SourceReferenced,
    SourceRegistered,
)
from yakhnama.modules.provenance.domain.value_objects import SourceDetails, SourceType

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
DETAILS = SourceDetails(
    title="Guest community report", citation="Yakhnama guest community report"
)


async def test_register_platform_source_stores_ownerless_referenced_source() -> None:
    uow = InMemoryProvenanceUnitOfWork()
    handler = RegisterPlatformSourceHandler(
        InMemoryUnitOfWorkFactory(uow), FrozenClock(NOW), SequentialIdGenerator(seed=9)
    )

    detail = await handler(
        RegisterPlatformSource(source_type=SourceType.CITIZEN, details=DETAILS)
    )

    stored = uow.sources.committed[detail.id]
    assert stored.owner_actor_id is None
    assert stored.organization_id is None
    assert stored.is_referenced is True
    assert stored.title == DETAILS.title
    assert [type(event) for event in uow.committed_events] == [
        SourceRegistered,
        SourceReferenced,
    ]


@pytest.mark.parametrize(
    "source_type",
    [
        SourceType.GOVERNMENT,
        SourceType.NEWS,
        SourceType.SATELLITE,
        SourceType.RESEARCH,
        SourceType.DATASET,
    ],
)
def test_register_platform_source_ranked_type_is_refused(
    source_type: SourceType,
) -> None:
    with pytest.raises(ValidationError, match="citizen or organisation"):
        RegisterPlatformSource(source_type=source_type, details=DETAILS)
