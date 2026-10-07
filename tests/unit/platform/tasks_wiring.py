"""A container with its database-backed task dependencies swapped for Fakes.

Used by the container and worker tests to run the bound task handlers without a
database: the outbox and idempotency stores, and the reports triage, guest purge,
media scan and stale-upload sweep use cases over in-memory units of work.
"""

import dataclasses
from datetime import timedelta

from tests.fakes.auth import InMemoryIdempotencyStore
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.media import (
    FakeStoragePort,
    InMemoryMediaQueryService,
    InMemoryMediaUnitOfWork,
)
from tests.fakes.provenance import (
    FakePlatformSourceReferenceMarker,
    FakePlatformSourceRegistrar,
)
from tests.fakes.reports import (
    FakeGuestMediaGateway,
    FakeMediaOwnershipChecker,
    FakeNearbyReportsFinder,
    FakePhotoEvidenceProvider,
    InMemoryReportsUnitOfWork,
    SequentialGuestSecretGenerator,
)
from tests.fakes.tasks import RecordingTaskQueue
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.unit.platform.events import FixedClock
from tests.unit.platform.outbox_store import InMemoryOutboxStore
from yakhnama.modules.media.public import (
    RecordScanResultHandler,
    SweepStaleUploadsHandler,
)
from yakhnama.modules.reports.infrastructure.adapters.guest import (
    HmacGuestChallengeSigner,
)
from yakhnama.modules.reports.public import (
    GuestHandlerDependencies,
    GuestSubmissionLimits,
    PurgeGuestRecordsHandler,
    RunTriageHandler,
)
from yakhnama.platform.container import Container, build_container
from yakhnama.platform.outbox.relay import OutboxRelay
from yakhnama.platform.settings import Settings


def build_faked_container(
    settings: Settings,
    store: InMemoryOutboxStore,
    clock: FixedClock,
    *,
    reports: InMemoryReportsUnitOfWork | None = None,
    media: InMemoryMediaUnitOfWork | None = None,
) -> Container:
    """Return the production container with in-memory stores behind its tasks.

    Args:
        settings: The settings to build from.
        store: The outbox store the relay uses.
        clock: The container's and the relay's clock.
        reports: The reports store triage reads; empty when ``None``.
        media: The media store the scan reads and writes; empty when ``None``.

    Returns:
        The container; its engine is never connected.
    """
    container = build_container(settings)
    relay = OutboxRelay(
        container.subscriber_registry,
        clock,
        store,
        max_attempts=settings.outbox_max_attempts,
        lease_seconds=settings.outbox_lease_seconds,
        subscriber_timeout_seconds=settings.outbox_subscriber_timeout_seconds,
    )
    reports_store = reports if reports is not None else InMemoryReportsUnitOfWork()
    media_store = media if media is not None else InMemoryMediaUnitOfWork()
    ids = SequentialIdGenerator(seed=91)
    registrar = FakePlatformSourceRegistrar()
    guest = GuestHandlerDependencies(
        uow_factory=InMemoryUnitOfWorkFactory(reports_store),
        signer=HmacGuestChallengeSigner(b"task-wiring-test-key-".ljust(32, b"x")),
        secrets=SequentialGuestSecretGenerator(),
        media=FakeGuestMediaGateway(),
        media_checker=FakeMediaOwnershipChecker(),
        source_registrar=registrar,
        source_marker=FakePlatformSourceReferenceMarker(registrar),
        task_queue=RecordingTaskQueue(),
        limits=GuestSubmissionLimits(),
        clock=clock,
        ids=ids,
    )
    return dataclasses.replace(
        container,
        clock=clock,
        outbox_store=store,
        outbox_relay=relay,
        idempotency_store=InMemoryIdempotencyStore(),
        run_triage_handler=RunTriageHandler(
            uow_factory=InMemoryUnitOfWorkFactory(reports_store),
            nearby_reports=FakeNearbyReportsFinder(),
            photos=FakePhotoEvidenceProvider(),
            clock=clock,
            ids=ids,
        ),
        media_query_service=InMemoryMediaQueryService(media_store),
        record_scan_result_handler=RecordScanResultHandler(
            InMemoryUnitOfWorkFactory(media_store), clock, ids
        ),
        purge_guest_records_handler=PurgeGuestRecordsHandler(guest),
        sweep_stale_uploads_handler=SweepStaleUploadsHandler(
            uow_factory=InMemoryUnitOfWorkFactory(media_store),
            storage=FakeStoragePort(),
            stale_after=timedelta(hours=2),
            clock=clock,
            ids=ids,
        ),
    )
