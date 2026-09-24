"""Unit tests for the Phase 3 bindings of ``yakhnama.platform.container``.

``build_container`` does no I/O, so the production bindings are inspected directly;
the two module task handlers run over a container whose stores are Fakes.
"""

import dataclasses
from collections.abc import AsyncIterator
from typing import Final

import pytest

from tests.factories.media import MediaAssetTestFactory, synthetic_sha256
from tests.factories.reports import ReportTestFactory
from tests.fakes.media import InMemoryMediaUnitOfWork
from tests.fakes.reports import InMemoryReportsUnitOfWork
from tests.unit.platform.events import FixedClock
from tests.unit.platform.outbox_store import InMemoryOutboxStore
from tests.unit.platform.tasks_wiring import build_faked_container
from yakhnama.modules.audit.infrastructure.uow import SqlAlchemyAuditUnitOfWork
from yakhnama.modules.events.infrastructure.queries import SqlAlchemyEventQueryService
from yakhnama.modules.events.infrastructure.uow import SqlAlchemyEventsUnitOfWork
from yakhnama.modules.impacts.infrastructure.claims_queries import (
    SqlAlchemyImpactQueryService,
)
from yakhnama.modules.impacts.infrastructure.claims_uow import (
    SqlAlchemyImpactClaimsUnitOfWork,
)
from yakhnama.modules.media.infrastructure.adapters.exif import PillowExifReader
from yakhnama.modules.media.infrastructure.adapters.mime import FiletypeMimeSniffer
from yakhnama.modules.media.infrastructure.adapters.s3_storage import S3StoragePort
from yakhnama.modules.media.infrastructure.adapters.scanner import (
    ClamAvScanner,
    NoOpMalwareScanner,
)
from yakhnama.modules.media.infrastructure.queries import SqlAlchemyMediaQueryService
from yakhnama.modules.media.infrastructure.uow import SqlAlchemyMediaUnitOfWork
from yakhnama.modules.media.public import ScanStatus, UploadStatus
from yakhnama.modules.provenance.infrastructure.queries import (
    SqlAlchemySourceQueryService,
)
from yakhnama.modules.provenance.infrastructure.uow import (
    SqlAlchemyProvenanceUnitOfWork,
)
from yakhnama.modules.reports.infrastructure.queries import (
    SqlAlchemyNearbyReportsFinder,
    SqlAlchemyReportQueryService,
)
from yakhnama.modules.reports.infrastructure.uow import SqlAlchemyReportsUnitOfWork
from yakhnama.modules.verification.infrastructure.queries import (
    SqlAlchemyVerificationQueryService,
)
from yakhnama.modules.verification.infrastructure.uow import (
    SqlAlchemyVerificationUnitOfWork,
)
from yakhnama.platform.container import Container, build_container, build_task_handlers
from yakhnama.platform.settings import Settings
from yakhnama.platform.tasks.handlers import MEDIA_SCAN_TASK, REPORTS_TRIAGE_TASK
from yakhnama.platform.wiring.events import (
    EventSourceMarkerAdapter,
    EventTimelineAdapter,
    PlaceDirectoryAdapter,
    ReportFactsAdapter,
    VerificationCaseOpenerAdapter,
)
from yakhnama.platform.wiring.impacts import (
    HazardEventDirectoryAdapter,
    ImpactSourceMarkerAdapter,
)
from yakhnama.platform.wiring.provenance import SourceCitationCheckerAdapter
from yakhnama.platform.wiring.verification import (
    ReportOwnerAdapter,
    ReviewerEligibilityAdapter,
)
from yakhnama.shared_kernel.tasks import ScheduledTask, TaskId

CLAMD_HOST: Final = "clamd.invalid"


@pytest.fixture
async def container(settings: Settings) -> AsyncIterator[Container]:
    container = build_container(settings)

    yield container

    await container.aclose()


def test_build_container_binds_sqlalchemy_units_of_work_per_recording_module(
    container: Container,
) -> None:
    opened = (
        container.provenance_uow_factory(),
        container.reports_uow_factory(),
        container.media_uow_factory(),
        container.events_uow_factory(),
        container.verification_uow_factory(),
        container.impact_claims_uow_factory(),
        container.audit_uow_factory(),
    )

    expected = (
        SqlAlchemyProvenanceUnitOfWork,
        SqlAlchemyReportsUnitOfWork,
        SqlAlchemyMediaUnitOfWork,
        SqlAlchemyEventsUnitOfWork,
        SqlAlchemyVerificationUnitOfWork,
        SqlAlchemyImpactClaimsUnitOfWork,
        SqlAlchemyAuditUnitOfWork,
    )
    # Names, not classes: each port type is a Protocol, so mypy sees the class
    # lists as unrelated.
    assert [type(uow).__name__ for uow in opened] == [
        uow_class.__name__ for uow_class in expected
    ]


def test_build_container_binds_sql_read_ports_of_the_recording_modules(
    container: Container,
) -> None:
    reads = (
        container.source_query_service,
        container.report_query_service,
        container.nearby_reports_finder,
        container.media_query_service,
        container.event_query_service,
        container.verification_query_service,
        container.impact_query_service,
    )

    assert [type(read) for read in reads] == [
        SqlAlchemySourceQueryService,
        SqlAlchemyReportQueryService,
        SqlAlchemyNearbyReportsFinder,
        SqlAlchemyMediaQueryService,
        SqlAlchemyEventQueryService,
        SqlAlchemyVerificationQueryService,
        SqlAlchemyImpactQueryService,
    ]


def test_build_container_binds_storage_and_file_inspectors(
    container: Container,
) -> None:
    media = (
        container.storage,
        container.exif_reader,
        container.mime_sniffer,
        container.malware_scanner,
    )

    assert isinstance(media[0], S3StoragePort)
    assert isinstance(media[1], PillowExifReader)
    assert isinstance(media[2], FiletypeMimeSniffer)
    assert isinstance(media[3], NoOpMalwareScanner)


async def test_build_container_default_noop_scanner_answers_unavailable(
    container: Container,
) -> None:
    verdict = await container.malware_scanner.scan("media/original/any")

    assert verdict is ScanStatus.UNAVAILABLE


async def test_build_container_clamav_setting_binds_the_clamav_scanner(
    settings: Settings,
) -> None:
    container = build_container(
        settings.model_copy(
            update={"malware_scanner": "clamav", "clamav_host": CLAMD_HOST}
        )
    )
    scanner = container.malware_scanner
    await container.aclose()

    assert isinstance(scanner, ClamAvScanner)


def test_build_container_public_coordinates_follow_the_settings(
    settings: Settings,
) -> None:
    container = build_container(
        settings.model_copy(update={"public_coordinate_decimals": 3})
    )

    assert container.public_coordinates.decimals == 3


def test_build_container_binds_every_cross_module_port_to_its_adapter(
    container: Container,
) -> None:
    events = container.event_handler_dependencies
    verification = container.verification_handler_dependencies
    claims = container.impact_claim_handler_dependencies

    assert isinstance(events.reports, ReportFactsAdapter)
    assert isinstance(events.sources, EventSourceMarkerAdapter)
    assert isinstance(events.cases, VerificationCaseOpenerAdapter)
    assert isinstance(events.places, PlaceDirectoryAdapter)
    assert events.hazard_types is container.hazard_type_query_service
    assert events.coordinates is container.public_coordinates
    assert isinstance(verification.report_owners, ReportOwnerAdapter)
    assert isinstance(verification.reviewers, ReviewerEligibilityAdapter)
    assert isinstance(claims.events, HazardEventDirectoryAdapter)
    assert isinstance(claims.sources, ImpactSourceMarkerAdapter)
    assert claims.uow_factory is container.impact_claims_uow_factory


def test_build_container_source_reads_check_citations_through_the_events_read(
    container: Container,
) -> None:
    checker = container.source_queries._citation_checker

    assert isinstance(checker, SourceCitationCheckerAdapter)
    # Widened to object: the two read ports are unrelated Protocols to mypy, yet
    # one SQL service answers both.
    citations: object = checker._citations
    assert citations is container.event_query_service


def test_build_container_timeline_reads_verification_and_impacts(
    container: Container,
) -> None:
    timeline = container.event_queries._timeline_sources

    assert isinstance(timeline, EventTimelineAdapter)


def test_build_container_subscribes_audit_to_every_domain_event_type(
    container: Container,
) -> None:
    event_types = container.event_types.event_types()

    assert "reports.report_submitted" in event_types
    assert "verification.verification_transitioned" in event_types
    assert all(
        container.subscriber_registry.subscribers_for(event_type)
        == (container.audit_subscriber,)
        for event_type in event_types
    )


def _task(task_name: str, payload: dict[str, object]) -> ScheduledTask:
    return ScheduledTask.model_validate(
        {"task_id": TaskId(value="t-9"), "task_name": task_name, "payload": payload}
    )


async def test_build_task_handlers_triage_task_attaches_triage(
    settings: Settings,
) -> None:
    report = ReportTestFactory.build()
    reports = InMemoryReportsUnitOfWork(reports=[report])
    container = build_faked_container(
        settings, InMemoryOutboxStore(), FixedClock(), reports=reports
    )

    await build_task_handlers(container)[REPORTS_TRIAGE_TASK](
        _task(REPORTS_TRIAGE_TASK, {"report_id": str(report.id)})
    )
    await container.aclose()

    assert reports.reports.committed[report.id].triage is not None


async def test_build_task_handlers_scan_task_uses_the_container_scanner(
    settings: Settings,
) -> None:
    asset = MediaAssetTestFactory.build(
        upload_status=UploadStatus.COMPLETED, sha256=synthetic_sha256(), byte_size=10
    )
    media = InMemoryMediaUnitOfWork(assets=[asset])
    faked = build_faked_container(
        settings, InMemoryOutboxStore(), FixedClock(), media=media
    )
    # The documented test override: replace the scanner, then bind the handlers.
    container = dataclasses.replace(
        faked, malware_scanner=NoOpMalwareScanner(verdict=ScanStatus.CLEAN)
    )

    await build_task_handlers(container)[MEDIA_SCAN_TASK](
        _task(MEDIA_SCAN_TASK, {"asset_id": str(asset.id)})
    )
    await container.aclose()

    assert media.media_assets.committed[asset.id].scan_status is ScanStatus.CLEAN
