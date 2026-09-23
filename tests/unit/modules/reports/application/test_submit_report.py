"""Unit tests for ``SubmitReportHandler``, with in-memory fakes only."""

import pytest

from tests.fakes.ids import SequentialIdGenerator
from tests.unit.modules.reports.application.support import (
    ACCURACY,
    MEDIA_ID,
    MEMBER,
    ORGANIZATION_ID,
    OTHER_CITIZEN,
    OTHER_ID,
    POINT,
    REPORTER,
    REPORTER_ID,
    Harness,
    content,
)
from yakhnama.modules.identity.public import Actor
from yakhnama.modules.provenance.public import SourceType
from yakhnama.modules.reports.application.commands import SubmitReport
from yakhnama.modules.reports.application.handlers import (
    CITIZEN_SOURCE_CITATION,
    CITIZEN_SOURCE_TITLE,
    ORGANISATION_SOURCE_CITATION,
    ORGANISATION_SOURCE_TITLE,
)
from yakhnama.modules.reports.application.ports import RUN_TRIAGE_TASK
from yakhnama.modules.reports.domain.events import ReportSubmitted
from yakhnama.modules.reports.domain.value_objects import ReportStatus
from yakhnama.shared_kernel.errors import ConflictError, PermissionDeniedError

CLIENT_IDS = SequentialIdGenerator(seed=511)


def command(**overrides: object) -> SubmitReport:
    """Return a submission by ``REPORTER`` with a fresh client id."""
    fields: dict[str, object] = {
        "actor": REPORTER,
        "client_report_id": CLIENT_IDS.new_id(),
        "content": content(),
        **overrides,
    }
    return SubmitReport.model_validate(fields)


async def test_submit_report_new_report_commits_submitted_report() -> None:
    harness = Harness()
    submission = command()

    result = await harness.submit()(submission)

    stored = harness.uow.reports.committed[submission.client_report_id]
    assert stored.status is ReportStatus.SUBMITTED
    assert stored.reporter_id == REPORTER_ID
    assert stored.content == submission.content
    assert [type(event) for event in harness.uow.committed_events] == [ReportSubmitted]
    assert result.id == submission.client_report_id


async def test_submit_report_new_report_registers_and_cites_citizen_source() -> None:
    harness = Harness()
    submission = command()

    result = await harness.submit()(submission)

    [registration] = harness.registrar.commands
    assert registration.source_type is SourceType.CITIZEN
    assert registration.details.title == CITIZEN_SOURCE_TITLE
    assert registration.details.citation == CITIZEN_SOURCE_CITATION
    assert str(submission.client_report_id) not in registration.details.citation
    assert harness.marker.marked_ids == (result.source_id,)
    assert harness.uow.reports.committed[result.id].source_id == result.source_id


async def test_submit_report_new_report_enqueues_triage_not_inline() -> None:
    harness = Harness()
    submission = command()

    await harness.submit()(submission)

    [task] = harness.tasks.of(RUN_TRIAGE_TASK)
    assert dict(task.payload) == {"report_id": submission.client_report_id}
    assert harness.uow.reports.committed[submission.client_report_id].triage is None
    assert harness.nearby.queries == []


async def test_submit_report_result_is_exact_for_the_reporter() -> None:
    harness = Harness()

    result = await harness.submit()(command())

    assert result.coordinates == POINT
    assert result.accuracy == ACCURACY
    assert result.coordinates_are_exact is True
    assert result.triage is None


async def test_submit_report_same_client_id_twice_is_idempotent() -> None:
    harness = Harness()
    handler = harness.submit()
    submission = command()

    first = await handler(submission)
    second = await handler(submission)

    assert second == first
    assert len(harness.uow.reports.committed) == 1
    assert len(harness.registrar.commands) == 1
    assert len(harness.marker.commands) == 1
    assert len(harness.tasks.enqueued) == 1
    assert len(harness.uow.committed_events) == 1


async def test_submit_report_reused_id_with_other_content_raises_conflict() -> None:
    harness = Harness()
    handler = harness.submit()
    submission = command()
    await handler(submission)

    with pytest.raises(ConflictError):
        await handler(
            submission.model_copy(
                update={"content": content(description="Something else entirely.")}
            )
        )

    assert len(harness.registrar.commands) == 1


async def test_submit_report_reused_id_by_other_reporter_raises_conflict() -> None:
    harness = Harness()
    handler = harness.submit()
    submission = command()
    await handler(submission)

    with pytest.raises(ConflictError):
        await handler(submission.model_copy(update={"actor": OTHER_CITIZEN}))


async def test_submit_report_for_organisation_by_member_uses_organisation_source() -> (
    None
):
    harness = Harness()

    result = await harness.submit()(
        command(actor=MEMBER, organization_id=ORGANIZATION_ID)
    )

    [registration] = harness.registrar.commands
    assert registration.source_type is SourceType.ORGANISATION
    assert registration.details.title == ORGANISATION_SOURCE_TITLE
    assert registration.details.citation == ORGANISATION_SOURCE_CITATION
    assert registration.organization_id == ORGANIZATION_ID
    assert result.organization_id == ORGANIZATION_ID


async def test_submit_report_for_organisation_by_non_member_raises_denied() -> None:
    harness = Harness()

    with pytest.raises(PermissionDeniedError):
        await harness.submit()(command(organization_id=ORGANIZATION_ID))

    assert harness.uow.reports.committed == {}
    assert harness.registrar.commands == []


async def test_submit_report_anonymous_raises_permission_denied() -> None:
    harness = Harness()

    with pytest.raises(PermissionDeniedError):
        await harness.submit()(command(actor=Actor.anonymous()))

    assert harness.factory.calls == 0


async def test_submit_report_with_own_media_commits_media_ids() -> None:
    harness = Harness()

    result = await harness.submit()(command(content=content(media_ids=(MEDIA_ID,))))

    assert result.media_ids == (MEDIA_ID,)
    assert harness.media.calls == 1


async def test_submit_report_with_foreign_media_raises_denied_before_source() -> None:
    harness = Harness()
    harness.media.owners[MEDIA_ID] = OTHER_ID

    with pytest.raises(PermissionDeniedError):
        await harness.submit()(command(content=content(media_ids=(MEDIA_ID,))))

    assert harness.registrar.commands == []
    assert harness.uow.reports.committed == {}


async def test_submit_report_broker_down_keeps_report_and_raises() -> None:
    harness = Harness()
    harness.tasks.failure = ConnectionError("broker unavailable")
    submission = command()

    with pytest.raises(ConnectionError):
        await harness.submit()(submission)

    stored = harness.uow.reports.committed[submission.client_report_id]
    assert stored.status is ReportStatus.SUBMITTED
    assert stored.triage is None
    assert harness.tasks.enqueued == []
