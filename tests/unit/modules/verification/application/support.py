"""Builders shared by the verification application tests.

Patterns: Fake.
"""

from datetime import UTC, datetime, timedelta
from typing import Final

from tests.factories.verification import VerificationCaseTestFactory
from tests.fakes.clock import SteppingClock
from tests.fakes.identity import actor_with
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.fakes.verification import (
    FakeReportOwnerLookup,
    FakeReviewerEligibility,
    InMemoryVerificationUnitOfWork,
)
from tests.unit.modules.verification.domain.walks import PATHS
from yakhnama.modules.identity.public import AuthorisationPolicy, Role
from yakhnama.modules.verification.application.authorisation import (
    moderation_policy,
)
from yakhnama.modules.verification.application.handlers import (
    VerificationHandlerDependencies,
)
from yakhnama.modules.verification.domain.entities import VerificationCase
from yakhnama.modules.verification.domain.value_objects import (
    TargetKind,
    VerificationState,
    VerificationTarget,
)
from yakhnama.shared_kernel.ids import EntityId

OPENED_AT: Final = datetime(2026, 9, 1, tzinfo=UTC)
HANDLER_NOW: Final = datetime(2026, 9, 23, tzinfo=UTC)
_IDS: Final = SequentialIdGenerator(seed=515)
MODERATOR_ID: Final = _IDS.new_id()
REPORTER_ID: Final = _IDS.new_id()
REVIEWER_ID: Final = _IDS.new_id()
MODERATOR = actor_with({Role.MODERATOR}, user_id=MODERATOR_ID)
REPORTER = actor_with(user_id=REPORTER_ID)
REASON: Final = "Cross-checked with the district bulletin."


def new_id() -> EntityId:
    """Return a fresh, deterministic UUIDv7."""
    return _IDS.new_id()


def target(kind: TargetKind = TargetKind.REPORT) -> VerificationTarget:
    """Return a target of ``kind`` with a fresh id."""
    return VerificationTarget(kind=kind, target_id=new_id())


def case_in(
    state: VerificationState, *, kind: TargetKind = TargetKind.REPORT
) -> VerificationCase:
    """Return a case about a fresh target of ``kind`` walked into ``state``."""
    initial = (
        VerificationState.DRAFT
        if state is VerificationState.DRAFT
        else VerificationState.SUBMITTED
    )
    case = VerificationCaseTestFactory.build(
        target=target(kind),
        initial_state=initial,
        state=initial,
        created_at=OPENED_AT,
        updated_at=OPENED_AT,
    )
    clock = SteppingClock(OPENED_AT + timedelta(minutes=1), timedelta(minutes=1))
    for step in PATHS[state]:
        case = case.transition(
            step,
            actor_id=MODERATOR_ID,
            reason=REASON,
            is_human=True,
            clock=clock,
            ids=SequentialIdGenerator(seed=9),
        ).state
    return case


def dependencies(
    *cases: VerificationCase,
    policy: AuthorisationPolicy | None = None,
    owners: dict[EntityId, EntityId] | None = None,
    reviewers: tuple[EntityId, ...] = (REVIEWER_ID,),
) -> tuple[InMemoryVerificationUnitOfWork, VerificationHandlerDependencies]:
    """Return a fake unit of work holding ``cases`` and the handler dependencies."""
    uow = InMemoryVerificationUnitOfWork(cases)
    deps = VerificationHandlerDependencies(
        uow_factory=InMemoryUnitOfWorkFactory(uow),
        policy=policy or moderation_policy(),
        clock=SteppingClock(HANDLER_NOW, timedelta(seconds=1)),
        ids=SequentialIdGenerator(seed=77),
        report_owners=FakeReportOwnerLookup(owners),
        reviewers=FakeReviewerEligibility(reviewers),
    )
    return uow, deps
