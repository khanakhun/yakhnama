"""Unit tests for the verification command handlers, with in-memory fakes only."""

import pytest

from tests.fakes.identity import AllowAllPolicy, actor_with
from tests.fakes.verification import FakeReportOwnerLookup
from tests.unit.modules.verification.application.support import (
    MODERATOR,
    MODERATOR_ID,
    REASON,
    REPORTER,
    REPORTER_ID,
    REVIEWER_ID,
    case_in,
    dependencies,
    new_id,
    target,
)
from yakhnama.modules.identity.public import Actor, Role
from yakhnama.modules.verification.application.commands import (
    AssignVerificationCase,
    OpenVerificationCase,
    TransitionVerification,
)
from yakhnama.modules.verification.application.handlers import (
    AssignVerificationCaseHandler,
    OpenVerificationCaseHandler,
    TransitionVerificationHandler,
)
from yakhnama.modules.verification.domain.entities import VerificationCase
from yakhnama.modules.verification.domain.errors import (
    CaseAlreadyOpenError,
    HumanRequiredError,
    ReasonRequiredError,
    VerificationCaseNotFoundError,
)
from yakhnama.modules.verification.domain.events import (
    VerificationAssigned,
    VerificationCaseOpened,
    VerificationTransitioned,
)
from yakhnama.modules.verification.domain.value_objects import (
    TargetKind,
    VerificationState,
)
from yakhnama.shared_kernel.errors import (
    InvalidTransitionError,
    PermissionDeniedError,
    ValidationError,
)

S = VerificationState

# --------------------------------------------------------------------------- #
# OpenVerificationCase                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (TargetKind.REPORT, S.SUBMITTED),
        (TargetKind.EVENT, S.DRAFT),
        (TargetKind.CLAIM, S.SUBMITTED),
    ],
)
async def test_open_case_new_target_commits_case_in_initial_state(
    kind: TargetKind, expected: VerificationState
) -> None:
    uow, deps = dependencies()
    subject = target(kind)

    case_id = await OpenVerificationCaseHandler(deps)(
        OpenVerificationCase(actor=MODERATOR, target=subject)
    )

    stored = uow.verification_cases.committed[case_id]
    assert stored.target == subject
    assert stored.state is expected
    assert stored.opened_by == MODERATOR_ID
    (event,) = uow.committed_events
    assert isinstance(event, VerificationCaseOpened)
    assert event.aggregate_id == case_id


async def test_open_case_citizen_is_denied_and_nothing_committed() -> None:
    uow, deps = dependencies()

    with pytest.raises(PermissionDeniedError):
        await OpenVerificationCaseHandler(deps)(
            OpenVerificationCase(actor=REPORTER, target=target())
        )

    assert uow.commit_count == 0
    assert uow.verification_cases.committed == {}


async def test_open_case_anonymous_under_permissive_policy_is_denied() -> None:
    uow, deps = dependencies(policy=AllowAllPolicy())

    with pytest.raises(PermissionDeniedError):
        await OpenVerificationCaseHandler(deps)(
            OpenVerificationCase(actor=Actor.anonymous(), target=target())
        )

    assert uow.commit_count == 0


async def test_open_case_existing_case_raises_conflict() -> None:
    existing = case_in(S.SUBMITTED)
    uow, deps = dependencies(existing)

    with pytest.raises(CaseAlreadyOpenError):
        await OpenVerificationCaseHandler(deps)(
            OpenVerificationCase(actor=MODERATOR, target=existing.target)
        )

    assert uow.commit_count == 0


async def test_open_case_existing_case_with_if_absent_returns_existing_id() -> None:
    existing = case_in(S.SUBMITTED)
    uow, deps = dependencies(existing)

    case_id = await OpenVerificationCaseHandler(deps)(
        OpenVerificationCase(actor=MODERATOR, target=existing.target, if_absent=True)
    )

    assert case_id == existing.id
    assert uow.commit_count == 0
    assert uow.committed_events == ()


# --------------------------------------------------------------------------- #
# TransitionVerification                                                      #
# --------------------------------------------------------------------------- #


def transition(
    case_id: object,
    to_state: VerificationState,
    *,
    actor: Actor = MODERATOR,
    reason: str | None = REASON,
    is_human: bool = True,
) -> TransitionVerification:
    return TransitionVerification.model_validate(
        {
            "actor": actor,
            "case_id": case_id,
            "to_state": to_state,
            "reason": reason,
            "is_human": is_human,
        }
    )


async def test_transition_moderator_walks_table_to_verified() -> None:
    case = case_in(S.SUBMITTED)
    uow, deps = dependencies(case)
    handler = TransitionVerificationHandler(deps)

    await handler(transition(case.id, S.UNDER_REVIEW))
    detail = await handler(transition(case.id, S.VERIFIED))

    stored = uow.verification_cases.committed[case.id]
    assert stored.state is S.VERIFIED
    assert detail.state is S.VERIFIED
    assert detail.next_states == (S.DISPUTED, S.RETRACTED)
    assert [step.to_state for step in stored.history[-2:]] == [
        S.UNDER_REVIEW,
        S.VERIFIED,
    ]
    moves = [
        event.to_state
        for event in uow.committed_events
        if isinstance(event, VerificationTransitioned)
    ]
    assert moves == [S.UNDER_REVIEW, S.VERIFIED]


async def test_transition_outside_table_raises_and_nothing_committed() -> None:
    case = case_in(S.SUBMITTED)
    uow, deps = dependencies(case)

    with pytest.raises(InvalidTransitionError):
        await TransitionVerificationHandler(deps)(transition(case.id, S.VERIFIED))

    assert uow.commit_count == 0
    assert uow.verification_cases.committed[case.id] == case


async def test_transition_automated_move_to_verified_requires_human() -> None:
    case = case_in(S.UNDER_REVIEW)
    uow, deps = dependencies(case)

    with pytest.raises(HumanRequiredError):
        await TransitionVerificationHandler(deps)(
            transition(case.id, S.VERIFIED, is_human=False)
        )

    assert uow.commit_count == 0


async def test_transition_automated_moderator_move_below_verified_commits() -> None:
    case = case_in(S.SUBMITTED)
    uow, deps = dependencies(case)

    await TransitionVerificationHandler(deps)(
        transition(case.id, S.UNDER_REVIEW, is_human=False)
    )

    assert uow.verification_cases.committed[case.id].history[-1].is_human is False


async def test_transition_without_required_reason_raises() -> None:
    case = case_in(S.UNDER_REVIEW)
    uow, deps = dependencies(case)

    with pytest.raises(ReasonRequiredError):
        await TransitionVerificationHandler(deps)(
            transition(case.id, S.REJECTED, reason=None)
        )

    assert uow.commit_count == 0


async def test_transition_moderator_missing_case_raises_not_found() -> None:
    _, deps = dependencies()

    with pytest.raises(VerificationCaseNotFoundError):
        await TransitionVerificationHandler(deps)(transition(new_id(), S.UNDER_REVIEW))


async def test_transition_reporter_resubmits_own_report_case() -> None:
    case = case_in(S.NEEDS_INFORMATION)
    uow, deps = dependencies(case, owners={case.target.target_id: REPORTER_ID})

    detail = await TransitionVerificationHandler(deps)(
        transition(case.id, S.SUBMITTED, actor=REPORTER, reason=None)
    )

    assert detail.state is S.SUBMITTED
    stored = uow.verification_cases.committed[case.id]
    assert stored.history[-1].actor_id == REPORTER_ID


async def test_transition_reporter_of_another_report_is_denied() -> None:
    case = case_in(S.NEEDS_INFORMATION)
    uow, deps = dependencies(case, owners={case.target.target_id: new_id()})

    with pytest.raises(PermissionDeniedError):
        await TransitionVerificationHandler(deps)(
            transition(case.id, S.SUBMITTED, actor=REPORTER, reason=None)
        )

    assert uow.commit_count == 0


async def test_transition_reporter_of_unknown_report_is_denied() -> None:
    case = case_in(S.NEEDS_INFORMATION)
    _, deps = dependencies(case)

    with pytest.raises(PermissionDeniedError):
        await TransitionVerificationHandler(deps)(
            transition(case.id, S.SUBMITTED, actor=REPORTER, reason=None)
        )


@pytest.mark.parametrize(
    ("state", "kind", "to_state"),
    [
        (S.UNDER_REVIEW, TargetKind.REPORT, S.VERIFIED),
        (S.NEEDS_INFORMATION, TargetKind.CLAIM, S.SUBMITTED),
        (S.VERIFIED, TargetKind.REPORT, S.DISPUTED),
    ],
)
async def test_transition_reporter_outside_resubmission_rule_is_denied(
    state: VerificationState, kind: TargetKind, to_state: VerificationState
) -> None:
    case = case_in(state, kind=kind)
    uow, deps = dependencies(case, owners={case.target.target_id: REPORTER_ID})

    with pytest.raises(PermissionDeniedError):
        await TransitionVerificationHandler(deps)(
            transition(case.id, to_state, actor=REPORTER)
        )

    assert uow.commit_count == 0


async def test_transition_citizen_missing_case_is_denied_not_found_hidden() -> None:
    _, deps = dependencies()

    with pytest.raises(PermissionDeniedError):
        await TransitionVerificationHandler(deps)(
            transition(new_id(), S.SUBMITTED, actor=REPORTER, reason=None)
        )


async def test_transition_citizen_automated_move_is_denied_before_reading() -> None:
    case = case_in(S.NEEDS_INFORMATION)
    _, deps = dependencies(case, owners={case.target.target_id: REPORTER_ID})

    with pytest.raises(PermissionDeniedError):
        await TransitionVerificationHandler(deps)(
            transition(case.id, S.SUBMITTED, actor=REPORTER, is_human=False)
        )

    owners = deps.report_owners
    assert isinstance(owners, FakeReportOwnerLookup)
    assert owners.asked == []


async def test_transition_anonymous_is_denied() -> None:
    case = case_in(S.NEEDS_INFORMATION)
    _, deps = dependencies(case)

    with pytest.raises(PermissionDeniedError):
        await TransitionVerificationHandler(deps)(
            transition(case.id, S.SUBMITTED, actor=Actor.anonymous(), reason=None)
        )


# --------------------------------------------------------------------------- #
# AssignVerificationCase                                                      #
# --------------------------------------------------------------------------- #


async def test_assign_case_eligible_reviewer_commits_assignment() -> None:
    case = case_in(S.UNDER_REVIEW)
    uow, deps = dependencies(case)

    detail = await AssignVerificationCaseHandler(deps)(
        AssignVerificationCase(
            actor=MODERATOR, case_id=case.id, reviewer_id=REVIEWER_ID
        )
    )

    assert detail.assigned_to == REVIEWER_ID
    assert uow.verification_cases.committed[case.id].assigned_to == REVIEWER_ID
    (event,) = uow.committed_events
    assert isinstance(event, VerificationAssigned)
    assert event.assigned_by == MODERATOR_ID


async def test_assign_case_same_reviewer_again_records_nothing() -> None:
    case = VerificationCase.model_validate(
        {**dict(case_in(S.UNDER_REVIEW)), "assigned_to": REVIEWER_ID}
    )
    uow, deps = dependencies(case)

    detail = await AssignVerificationCaseHandler(deps)(
        AssignVerificationCase(
            actor=MODERATOR, case_id=case.id, reviewer_id=REVIEWER_ID
        )
    )

    assert detail.version == case.version
    assert uow.committed_events == ()


async def test_assign_case_ineligible_reviewer_raises_validation_error() -> None:
    case = case_in(S.UNDER_REVIEW)
    uow, deps = dependencies(case)

    with pytest.raises(ValidationError):
        await AssignVerificationCaseHandler(deps)(
            AssignVerificationCase(
                actor=MODERATOR, case_id=case.id, reviewer_id=new_id()
            )
        )

    assert uow.commit_count == 0


async def test_assign_case_citizen_is_denied() -> None:
    case = case_in(S.UNDER_REVIEW)
    uow, deps = dependencies(case)

    with pytest.raises(PermissionDeniedError):
        await AssignVerificationCaseHandler(deps)(
            AssignVerificationCase(
                actor=actor_with(), case_id=case.id, reviewer_id=REVIEWER_ID
            )
        )

    assert uow.commit_count == 0


async def test_assign_case_missing_case_raises_not_found() -> None:
    _, deps = dependencies()

    with pytest.raises(VerificationCaseNotFoundError):
        await AssignVerificationCaseHandler(deps)(
            AssignVerificationCase(
                actor=MODERATOR, case_id=new_id(), reviewer_id=REVIEWER_ID
            )
        )


async def test_assign_case_closed_case_raises_invalid_transition() -> None:
    case = case_in(S.REJECTED)
    uow, deps = dependencies(case)

    with pytest.raises(InvalidTransitionError):
        await AssignVerificationCaseHandler(deps)(
            AssignVerificationCase(
                actor=actor_with({Role.ADMIN}),
                case_id=case.id,
                reviewer_id=REVIEWER_ID,
            )
        )

    assert uow.commit_count == 0
