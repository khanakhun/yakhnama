"""Unit tests for the verification read side: the guarded service and the fakes."""

import pytest

from tests.fakes.verification import (
    InMemoryVerificationCaseRepository,
    InMemoryVerificationQueryService,
)
from tests.unit.modules.verification.application.support import (
    MODERATOR,
    REPORTER,
    REVIEWER_ID,
    case_in,
    new_id,
    target,
)
from yakhnama.modules.verification.application.authorisation import (
    moderation_policy,
)
from yakhnama.modules.verification.application.queries import (
    GetVerificationCase,
    ListVerificationCases,
)
from yakhnama.modules.verification.application.query_services import (
    VerificationCaseQueryService,
)
from yakhnama.modules.verification.domain.entities import VerificationCase
from yakhnama.modules.verification.domain.errors import VerificationCaseNotFoundError
from yakhnama.modules.verification.domain.value_objects import (
    TargetKind,
    VerificationState,
)
from yakhnama.shared_kernel.errors import PermissionDeniedError
from yakhnama.shared_kernel.pagination import PageRequest

S = VerificationState


def service(
    *cases: VerificationCase,
) -> tuple[VerificationCaseQueryService, InMemoryVerificationQueryService]:
    reads = InMemoryVerificationQueryService(InMemoryVerificationCaseRepository(cases))
    return VerificationCaseQueryService(reads, moderation_policy()), reads


async def test_get_case_moderator_receives_history() -> None:
    case = case_in(S.VERIFIED)
    guarded, _ = service(case)

    detail = await guarded.get_case(
        GetVerificationCase(actor=MODERATOR, case_id=case.id)
    )

    assert detail.id == case.id
    assert [step.to_state for step in detail.history] == [S.UNDER_REVIEW, S.VERIFIED]


async def test_get_case_citizen_is_denied() -> None:
    case = case_in(S.VERIFIED)
    guarded, _ = service(case)

    with pytest.raises(PermissionDeniedError):
        await guarded.get_case(GetVerificationCase(actor=REPORTER, case_id=case.id))


async def test_get_case_missing_raises_not_found() -> None:
    guarded, _ = service()

    with pytest.raises(VerificationCaseNotFoundError):
        await guarded.get_case(GetVerificationCase(actor=MODERATOR, case_id=new_id()))


async def test_list_cases_citizen_is_denied() -> None:
    guarded, _ = service()

    with pytest.raises(PermissionDeniedError):
        await guarded.list_cases(ListVerificationCases(actor=REPORTER))


async def test_list_cases_filters_by_state_kind_and_reviewer() -> None:
    wanted = VerificationCase.model_validate(
        {
            **dict(case_in(S.UNDER_REVIEW, kind=TargetKind.EVENT)),
            "assigned_to": REVIEWER_ID,
        }
    )
    other_state = case_in(S.SUBMITTED, kind=TargetKind.EVENT)
    other_kind = case_in(S.UNDER_REVIEW, kind=TargetKind.REPORT)
    unassigned = case_in(S.UNDER_REVIEW, kind=TargetKind.EVENT)
    guarded, _ = service(wanted, other_state, other_kind, unassigned)

    page = await guarded.list_cases(
        ListVerificationCases(
            actor=MODERATOR,
            state=S.UNDER_REVIEW,
            target_kind=TargetKind.EVENT,
            assigned_to=REVIEWER_ID,
        )
    )

    assert [item.id for item in page.items] == [wanted.id]
    assert page.next_cursor is None


async def test_list_cases_pages_by_keyset_without_repeats() -> None:
    cases = [case_in(S.SUBMITTED) for _ in range(3)]
    guarded, _ = service(*cases)

    first = await guarded.list_cases(
        ListVerificationCases(actor=MODERATOR, page=PageRequest(limit=2))
    )
    second = await guarded.list_cases(
        ListVerificationCases(
            actor=MODERATOR, page=PageRequest(limit=2, cursor=first.next_cursor)
        )
    )

    seen = [item.id for item in (*first.items, *second.items)]
    assert sorted(seen) == sorted(case.id for case in cases)
    assert second.next_cursor is None


async def test_fake_state_read_model_and_target_lookup() -> None:
    case = case_in(S.VERIFIED, kind=TargetKind.EVENT)
    _, reads = service(case)

    state = await reads.state_for(case.target)
    missing = await reads.state_for(target(TargetKind.EVENT))
    detail = await reads.get_for_target(case.target)
    absent = await reads.get_for_target(target())

    assert state is S.VERIFIED
    assert missing is None
    assert detail is not None
    assert detail.id == case.id
    assert absent is None
