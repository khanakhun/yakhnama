"""``SqlAlchemyEventQueryService.is_source_cited_by_public_event`` on real PostGIS.

The events and their verification cases are written through the real units of
work, so the JSONB containment test on ``source_ids``, the status test and the
verification-state subquery run exactly as in production.
"""

from datetime import UTC, datetime
from typing import Final
from uuid import UUID

import pytest

from tests.factories.base import FACTORY_IDS
from tests.factories.events import EventTestFactory
from tests.factories.verification import VerificationCaseTestFactory
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.events.domain.entities import Event
from yakhnama.modules.events.domain.value_objects import EventStatus
from yakhnama.modules.events.infrastructure.queries import SqlAlchemyEventQueryService
from yakhnama.modules.events.infrastructure.uow import SqlAlchemyEventsUnitOfWork
from yakhnama.modules.verification.domain.entities import VerificationCase
from yakhnama.modules.verification.domain.value_objects import (
    TargetKind,
    VerificationState,
    VerificationTarget,
)
from yakhnama.modules.verification.infrastructure.uow import (
    SqlAlchemyVerificationUnitOfWork,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory

pytestmark = pytest.mark.integration

type EventsFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyEventsUnitOfWork]
type VerificationFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyVerificationUnitOfWork]

CREATED: Final = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
MODERATOR: Final = FACTORY_IDS.new_id()
PUBLIC_ONLY: Final = FACTORY_IDS.new_id()
"""Cited by the published, verified event only (second of its two sources)."""
UNVERIFIED_ONLY: Final = FACTORY_IDS.new_id()
"""Cited by a published event whose case is only under review."""
DRAFT_ONLY: Final = FACTORY_IDS.new_id()
"""Cited by a draft event whose case is verified."""
DISPUTED_ONLY: Final = FACTORY_IDS.new_id()
"""Cited by a published event verified and then disputed."""
UNCITED: Final = FACTORY_IDS.new_id()
SHARED: Final = FACTORY_IDS.new_id()
"""Cited by both the public event and the draft."""


def _event(status: EventStatus, source_ids: tuple[UUID, ...]) -> Event:
    return EventTestFactory.build(
        id=FACTORY_IDS.new_id(),
        status=status,
        status_reason=None,
        merged_into=None,
        source_ids=source_ids,
        created_at=CREATED,
    )


def _case(
    event_id: UUID,
    states: tuple[VerificationState, ...],
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> VerificationCase:
    case = VerificationCaseTestFactory.build(
        target=VerificationTarget(kind=TargetKind.EVENT, target_id=event_id),
        created_at=CREATED,
    )
    for state in states:
        case = case.transition(
            state,
            actor_id=MODERATOR,
            reason="Checked against the sources.",
            is_human=True,
            clock=clock,
            ids=ids,
        ).state
    return case


TO_VERIFIED: Final = (VerificationState.UNDER_REVIEW, VerificationState.VERIFIED)


@pytest.fixture
async def arranged(
    events_uow_factory: EventsFactory,
    verification_uow_factory: VerificationFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    """Store four events citing the sources above, with their cases."""
    public = _event(EventStatus.PUBLISHED, (SHARED, PUBLIC_ONLY))
    unverified = _event(EventStatus.PUBLISHED, (UNVERIFIED_ONLY,))
    draft = _event(EventStatus.DRAFT, (DRAFT_ONLY, SHARED))
    disputed = _event(EventStatus.PUBLISHED, (DISPUTED_ONLY,))
    async with events_uow_factory() as uow:
        for event in (public, unverified, draft, disputed):
            await uow.events.add(event)
        await uow.commit()
    cases = (
        _case(public.id, TO_VERIFIED, clock, ids),
        _case(unverified.id, (VerificationState.UNDER_REVIEW,), clock, ids),
        _case(draft.id, TO_VERIFIED, clock, ids),
        _case(disputed.id, (*TO_VERIFIED, VerificationState.DISPUTED), clock, ids),
    )
    async with verification_uow_factory() as uow:
        for case in cases:
            await uow.verification_cases.add(case)
        await uow.commit()


@pytest.mark.usefixtures("arranged")
@pytest.mark.parametrize(
    ("source_id", "is_expected_cited"),
    [
        (PUBLIC_ONLY, True),
        (SHARED, True),
        (UNVERIFIED_ONLY, False),
        (DRAFT_ONLY, False),
        (DISPUTED_ONLY, False),
        (UNCITED, False),
    ],
    ids=["public", "shared", "unverified", "draft", "disputed", "uncited"],
)
async def test_is_source_cited_by_public_event_only_published_and_verified_count(
    event_queries: SqlAlchemyEventQueryService,
    source_id: UUID,
    *,
    is_expected_cited: bool,
) -> None:
    is_cited = await event_queries.is_source_cited_by_public_event(source_id)

    assert is_cited is is_expected_cited
