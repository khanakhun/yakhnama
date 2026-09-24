"""Unit tests for ``ReviseReportHandler`` and ``WithdrawReportHandler``."""

import pytest

from tests.unit.modules.reports.application.support import (
    MEDIA_ID,
    MISSING_ID,
    MODERATOR,
    NOW,
    OTHER_CITIZEN,
    OTHER_ID,
    REPORTER,
    Harness,
    content,
    stored_report,
)
from yakhnama.modules.reports.application.commands import ReviseReport, WithdrawReport
from yakhnama.modules.reports.application.ports import RUN_TRIAGE_TASK
from yakhnama.modules.reports.domain.errors import (
    ReportImmutableError,
    ReportNotFoundError,
)
from yakhnama.modules.reports.domain.events import (
    ReportRevised,
    ReportSuperseded,
    ReportWithdrawn,
)
from yakhnama.modules.reports.domain.value_objects import ReportStatus
from yakhnama.shared_kernel.errors import (
    PermissionDeniedError,
    PreconditionFailedError,
)

CORRECTED = content(description="Correction: the water was rising in the next valley.")

# --------------------------------------------------------------------------- #
# ReviseReport                                                                #
# --------------------------------------------------------------------------- #


async def test_revise_report_by_reporter_persists_both_revisions() -> None:
    original = stored_report()
    harness = Harness(original)

    result = await harness.revise()(
        ReviseReport(
            actor=REPORTER,
            report_id=original.id,
            content=CORRECTED,
            expected_version=original.version,
        )
    )

    stored = harness.uow.reports.committed
    new, old = stored[result.id], stored[original.id]
    assert new.revision == original.revision + 1
    assert new.supersedes_id == original.id
    assert new.status is ReportStatus.SUBMITTED
    assert new.content == CORRECTED
    assert old.status is ReportStatus.SUPERSEDED
    assert old.superseded_by_id == new.id
    assert old.content == original.content
    assert [type(event) for event in harness.uow.committed_events] == [
        ReportRevised,
        ReportSuperseded,
    ]


async def test_revise_report_enqueues_triage_of_new_revision() -> None:
    original = stored_report()
    harness = Harness(original)

    result = await harness.revise()(
        ReviseReport(actor=REPORTER, report_id=original.id, content=CORRECTED)
    )

    [task] = harness.tasks.of(RUN_TRIAGE_TASK)
    assert dict(task.payload) == {"report_id": result.id}


async def test_revise_report_by_moderator_raises_permission_denied() -> None:
    original = stored_report()
    harness = Harness(original)

    with pytest.raises(PermissionDeniedError):
        await harness.revise()(
            ReviseReport(actor=MODERATOR, report_id=original.id, content=CORRECTED)
        )

    assert harness.uow.reports.committed == {original.id: original}
    assert harness.tasks.enqueued == []


async def test_revise_report_missing_report_raises_not_found() -> None:
    harness = Harness()

    with pytest.raises(ReportNotFoundError):
        await harness.revise()(
            ReviseReport(actor=REPORTER, report_id=MISSING_ID, content=CORRECTED)
        )


async def test_revise_report_stale_version_raises_precondition_failed() -> None:
    original = stored_report()
    harness = Harness(original)

    with pytest.raises(PreconditionFailedError):
        await harness.revise()(
            ReviseReport(
                actor=REPORTER,
                report_id=original.id,
                content=CORRECTED,
                expected_version=original.version + 1,
            )
        )


async def test_revise_report_with_foreign_media_raises_permission_denied() -> None:
    original = stored_report()
    harness = Harness(original)
    harness.media.owners[MEDIA_ID] = OTHER_ID

    with pytest.raises(PermissionDeniedError):
        await harness.revise()(
            ReviseReport(
                actor=REPORTER,
                report_id=original.id,
                content=content(media_ids=(MEDIA_ID,)),
            )
        )

    assert harness.uow.committed_events == ()


async def test_revise_report_superseded_report_raises_immutable() -> None:
    original = stored_report()
    harness = Harness(original)
    await harness.revise()(
        ReviseReport(actor=REPORTER, report_id=original.id, content=CORRECTED)
    )

    with pytest.raises(ReportImmutableError):
        await harness.revise()(
            ReviseReport(
                actor=REPORTER,
                report_id=original.id,
                content=content(description="A third attempt at the words."),
            )
        )


# --------------------------------------------------------------------------- #
# WithdrawReport                                                              #
# --------------------------------------------------------------------------- #


async def test_withdraw_report_by_reporter_commits_withdrawn_report() -> None:
    report = stored_report()
    harness = Harness(report)

    result = await harness.withdraw()(
        WithdrawReport(
            actor=REPORTER,
            report_id=report.id,
            reason="Sent by mistake.",
            expected_version=report.version,
        )
    )

    stored = harness.uow.reports.committed[report.id]
    assert stored.status is ReportStatus.WITHDRAWN
    assert stored.withdrawal_reason == "Sent by mistake."
    assert stored.updated_at == NOW
    assert result.status is ReportStatus.WITHDRAWN
    assert [type(event) for event in harness.uow.committed_events] == [ReportWithdrawn]


async def test_withdraw_report_twice_records_one_event() -> None:
    report = stored_report()
    harness = Harness(report)
    command = WithdrawReport(actor=REPORTER, report_id=report.id, reason="Duplicate.")

    await harness.withdraw()(command)
    await harness.withdraw()(command)

    assert len(harness.uow.committed_events) == 1


async def test_withdraw_report_by_other_citizen_raises_permission_denied() -> None:
    report = stored_report()
    harness = Harness(report)

    with pytest.raises(PermissionDeniedError):
        await harness.withdraw()(
            WithdrawReport(actor=OTHER_CITIZEN, report_id=report.id, reason="Mine.")
        )

    assert harness.uow.reports.committed[report.id] == report


async def test_withdraw_report_missing_report_raises_not_found() -> None:
    harness = Harness()

    with pytest.raises(ReportNotFoundError):
        await harness.withdraw()(
            WithdrawReport(actor=REPORTER, report_id=MISSING_ID, reason="Gone.")
        )


async def test_withdraw_report_stale_version_raises_precondition_failed() -> None:
    report = stored_report()
    harness = Harness(report)

    with pytest.raises(PreconditionFailedError):
        await harness.withdraw()(
            WithdrawReport(
                actor=REPORTER,
                report_id=report.id,
                reason="Old tag.",
                expected_version=report.version + 1,
            )
        )
