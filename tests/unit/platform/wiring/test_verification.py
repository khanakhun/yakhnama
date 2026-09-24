"""Unit tests for ``yakhnama.platform.wiring.verification`` over the fakes."""

from typing import Final

import pytest

from tests.factories.identity import UserTestFactory
from tests.factories.reports import ReportTestFactory
from tests.fakes.identity import InMemoryIdentityUnitOfWork
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.reports import InMemoryReportQueryService, InMemoryReportsUnitOfWork
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.modules.identity.public import Role, UserStatus
from yakhnama.platform.wiring.verification import (
    ReportOwnerAdapter,
    ReviewerEligibilityAdapter,
)

IDS: Final = SequentialIdGenerator(seed=701)


async def test_report_owner_adapter_returns_the_reporter() -> None:
    report = ReportTestFactory.build()
    adapter = ReportOwnerAdapter(
        InMemoryReportQueryService(InMemoryReportsUnitOfWork(reports=[report]))
    )

    reporter = await adapter.reporter_of(report.id)

    assert reporter == report.reporter_id


async def test_report_owner_adapter_missing_report_returns_none() -> None:
    adapter = ReportOwnerAdapter(
        InMemoryReportQueryService(InMemoryReportsUnitOfWork())
    )

    reporter = await adapter.reporter_of(IDS.new_id())

    assert reporter is None


@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        (frozenset({Role.CITIZEN, Role.MODERATOR}), True),
        (frozenset({Role.CITIZEN, Role.ADMIN}), True),
        (frozenset({Role.CITIZEN}), False),
    ],
)
async def test_reviewer_eligibility_adapter_active_user_may_review_if_moderating(
    roles: frozenset[Role], *, expected: bool
) -> None:
    user = UserTestFactory.build(roles=roles)
    adapter = ReviewerEligibilityAdapter(
        InMemoryUnitOfWorkFactory(InMemoryIdentityUnitOfWork(users=[user]))
    )

    result = await adapter.can_review(user.id)

    assert result is expected


async def test_reviewer_eligibility_adapter_suspended_moderator_may_not_review() -> (
    None
):
    user = UserTestFactory.build(
        roles=frozenset({Role.CITIZEN, Role.MODERATOR}),
        status=UserStatus.SUSPENDED,
        status_reason="Test reason",
    )
    adapter = ReviewerEligibilityAdapter(
        InMemoryUnitOfWorkFactory(InMemoryIdentityUnitOfWork(users=[user]))
    )

    result = await adapter.can_review(user.id)

    assert result is False


async def test_reviewer_eligibility_adapter_unknown_user_may_not_review() -> None:
    adapter = ReviewerEligibilityAdapter(
        InMemoryUnitOfWorkFactory(InMemoryIdentityUnitOfWork())
    )

    result = await adapter.can_review(IDS.new_id())

    assert result is False
