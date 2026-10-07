"""Unit tests for assisted submission and channel-aware reads (ADR 0019, 0020)."""

import pytest

from tests.fakes.identity import actor_with
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.reports import InMemoryReportQueryService, InMemoryReportsUnitOfWork
from tests.unit.modules.reports.application.support import (
    MEMBER,
    MODERATOR,
    ORGANIZATION_ID,
    OTHER_ID,
    PUBLIC_COORDINATES,
    REPORTER,
    REPORTER_ID,
    Harness,
    content,
    stored_report,
)
from yakhnama.modules.identity.public import Actor, OrganizationRole, Role
from yakhnama.modules.provenance.public import SourceType
from yakhnama.modules.reports.application.commands import SubmitReport
from yakhnama.modules.reports.application.handlers import (
    ASSISTED_CITIZEN_SOURCE_CITATION,
    ASSISTED_CITIZEN_SOURCE_TITLE,
    ASSISTED_ORGANISATION_SOURCE_TITLE,
)
from yakhnama.modules.reports.application.queries import GetReport, ListReports
from yakhnama.modules.reports.application.query_services import (
    AuthorisedReportQueryService,
)
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.value_objects import (
    AssistedSubmission,
    ConsentMethod,
    ReportChannel,
)
from yakhnama.shared_kernel.errors import ConflictError, PermissionDeniedError

CLIENT_IDS = SequentialIdGenerator(seed=941)
ASSISTED = AssistedSubmission(
    consent_method=ConsentMethod.WRITTEN,
    consent_statement_version="2026-10-05",
    note="Private note for moderators.",
)
TRUSTED = actor_with({Role.TRUSTED_REPORTER}, user_id=REPORTER_ID)
ORG_MEMBER = actor_with(
    {Role.ORG_MEMBER},
    user_id=REPORTER_ID,
    memberships={(ORGANIZATION_ID, OrganizationRole.MEMBER)},
)


def command(actor: Actor, **overrides: object) -> SubmitReport:
    """Return an assisted submission by ``actor`` with a fresh client id."""
    fields: dict[str, object] = {
        "actor": actor,
        "client_report_id": CLIENT_IDS.new_id(),
        "content": content(),
        "assisted": ASSISTED,
        **overrides,
    }
    return SubmitReport.model_validate(fields)


async def test_submit_assisted_by_trusted_reporter_stores_assisted_channel() -> None:
    harness = Harness()

    detail = await harness.submit()(command(TRUSTED))

    stored = harness.uow.reports.committed[detail.id]
    [registration] = harness.registrar.commands
    assert stored.channel is ReportChannel.ASSISTED
    assert stored.assisted == ASSISTED
    assert stored.reporter_id == REPORTER_ID
    assert detail.assisted == ASSISTED
    assert registration.source_type is SourceType.CITIZEN
    assert registration.details.title == ASSISTED_CITIZEN_SOURCE_TITLE
    assert registration.details.citation == ASSISTED_CITIZEN_SOURCE_CITATION
    assert registration.actor == TRUSTED


async def test_submit_assisted_by_moderator_is_allowed() -> None:
    harness = Harness()

    detail = await harness.submit()(
        command(actor_with({Role.MODERATOR}, user_id=REPORTER_ID))
    )

    assert detail.channel is ReportChannel.ASSISTED


async def test_submit_assisted_by_org_member_for_own_org_uses_org_source() -> None:
    harness = Harness()

    detail = await harness.submit()(
        command(ORG_MEMBER, organization_id=ORGANIZATION_ID)
    )

    [registration] = harness.registrar.commands
    assert detail.organization_id == ORGANIZATION_ID
    assert registration.source_type is SourceType.ORGANISATION
    assert registration.details.title == ASSISTED_ORGANISATION_SOURCE_TITLE


@pytest.mark.parametrize(
    ("actor", "organization_id"),
    [
        (REPORTER, None),
        (ORG_MEMBER, None),
        (MEMBER, ORGANIZATION_ID),
    ],
)
async def test_submit_assisted_without_the_right_is_denied_and_stores_nothing(
    actor: Actor, organization_id: object
) -> None:
    harness = Harness()

    with pytest.raises(PermissionDeniedError):
        await harness.submit()(command(actor, organization_id=organization_id))

    assert harness.uow.reports.committed == {}
    assert harness.registrar.commands == []


async def test_submit_assisted_retry_with_same_consent_returns_same_report() -> None:
    harness = Harness()
    first = command(TRUSTED)
    await harness.submit()(first)

    again = await harness.submit()(first)

    assert again.id == first.client_report_id
    assert len(harness.registrar.commands) == 1


async def test_submit_same_client_id_without_assistance_is_a_conflict() -> None:
    harness = Harness()
    first = command(TRUSTED)
    await harness.submit()(first)

    with pytest.raises(ConflictError):
        await harness.submit()(first.model_copy(update={"assisted": None}))


def service(*reports: Report) -> AuthorisedReportQueryService:
    """Return the authorised service over a fake holding ``reports``."""
    return AuthorisedReportQueryService(
        InMemoryReportQueryService(InMemoryReportsUnitOfWork(reports=reports)),
        PUBLIC_COORDINATES,
    )


@pytest.mark.parametrize("actor", [REPORTER, MODERATOR])
async def test_get_assisted_report_shows_consent_to_reporter_and_moderators(
    actor: Actor,
) -> None:
    stored = stored_report(channel=ReportChannel.ASSISTED, assisted=ASSISTED)

    detail = await service(stored).get_report(
        GetReport(actor=actor, report_id=stored.id)
    )

    assert detail.assisted == ASSISTED
    assert detail.channel is ReportChannel.ASSISTED


async def test_get_assisted_org_report_hides_consent_from_other_members() -> None:
    stored = stored_report(
        channel=ReportChannel.ASSISTED,
        assisted=ASSISTED,
        organization_id=ORGANIZATION_ID,
    )
    colleague = actor_with(
        user_id=OTHER_ID,
        memberships={(ORGANIZATION_ID, OrganizationRole.MEMBER)},
    )

    detail = await service(stored).get_report(
        GetReport(actor=colleague, report_id=stored.id)
    )

    assert detail.coordinates_are_exact is False
    assert detail.assisted is None


async def test_get_guest_report_never_shows_a_reporter() -> None:
    stored = stored_report(channel=ReportChannel.GUEST)

    detail = await service(stored).get_report(
        GetReport(actor=MODERATOR, report_id=stored.id)
    )

    assert detail.reporter_id is None
    assert detail.channel is ReportChannel.GUEST


async def test_list_reports_by_channel_returns_the_guest_queue() -> None:
    guest = stored_report(channel=ReportChannel.GUEST)
    account = stored_report(id=CLIENT_IDS.new_id())

    page = await service(guest, account).list_reports(
        ListReports(actor=MODERATOR, channel=ReportChannel.GUEST)
    )

    assert [summary.id for summary in page.items] == [guest.id]
    assert page.items[0].channel is ReportChannel.GUEST
