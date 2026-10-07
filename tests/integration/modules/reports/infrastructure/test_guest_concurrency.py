"""Guest submissions under real PostgreSQL concurrency (ADR 0020).

Each handler call below opens its own sessions, so the requests that
``asyncio.gather`` runs together are separate transactions on separate connections,
exactly as parallel HTTP requests would be: the caps are counted under the cap's
advisory lock, and the version-checked reservations decide which request creates a
photo's asset or a report's source.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.media import FakeExifReader, FakeMimeSniffer, FakeStoragePort
from tests.fakes.reports import (
    FakeGuestMediaGateway,
    FakeMediaOwnershipChecker,
    SequentialGuestSecretGenerator,
)
from tests.fakes.tasks import RecordingTaskQueue
from tests.unit.modules.reports.application.support import content
from yakhnama.modules.media.infrastructure.uow import SqlAlchemyMediaUnitOfWork
from yakhnama.modules.media.public import (
    CompleteUploadHandler,
    RequestGuestUploadHandler,
)
from yakhnama.modules.provenance.infrastructure.uow import (
    SqlAlchemyProvenanceUnitOfWork,
)
from yakhnama.modules.provenance.public import (
    MarkPlatformSourceReferencedHandler,
    RegisterPlatformSourceHandler,
)
from yakhnama.modules.reports.application.guest_handlers import (
    GuestHandlerDependencies,
    IssueGuestChallengeHandler,
    OpenGuestSubmissionHandler,
    RequestGuestMediaUploadHandler,
    SubmitGuestReportHandler,
)
from yakhnama.modules.reports.domain.errors import (
    GuestMediaLimitError,
    GuestSubmissionLimitError,
)
from yakhnama.modules.reports.domain.guest_submissions import (
    GUEST_MEDIA_MAX,
    GuestSubmissionLimits,
    is_proof_of_work_valid,
)
from yakhnama.modules.reports.infrastructure.adapters.guest import (
    HmacGuestChallengeSigner,
)
from yakhnama.modules.reports.infrastructure.uow import SqlAlchemyReportsUnitOfWork
from yakhnama.modules.reports.public import (
    GuestChallengeGrant,
    GuestMediaGateway,
    GuestSubmissionGrant,
    IssueGuestChallenge,
    OpenGuestSubmission,
    RequestGuestMediaUpload,
    SubmitGuestReport,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.platform.wiring.reports import GuestMediaGatewayAdapter

pytestmark = pytest.mark.integration

type ReportsFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyReportsUnitOfWork]
type ProvenanceFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyProvenanceUnitOfWork]
type MediaFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyMediaUnitOfWork]

PARALLEL: Final = 6
SECRET: Final = b"integration-guest-challenge-key-".ljust(32, b"0")


def _dependencies(
    reports: ReportsFactory,
    provenance: ProvenanceFactory,
    *,
    media: GuestMediaGateway | None = None,
    **limits: object,
) -> GuestHandlerDependencies:
    # The application clock is the real time, so the database's now() agrees.
    clock = FrozenClock(datetime.now(UTC))
    ids = SequentialIdGenerator(seed=2301)
    return GuestHandlerDependencies(
        uow_factory=reports,
        signer=HmacGuestChallengeSigner(SECRET),
        secrets=SequentialGuestSecretGenerator(),
        media=FakeGuestMediaGateway() if media is None else media,
        media_checker=FakeMediaOwnershipChecker(),
        source_registrar=RegisterPlatformSourceHandler(provenance, clock, ids),
        source_marker=MarkPlatformSourceReferencedHandler(provenance, clock, ids),
        task_queue=RecordingTaskQueue(),
        limits=GuestSubmissionLimits.model_validate({"difficulty_bits": 1, **limits}),
        clock=clock,
        ids=ids,
    )


def _solve(grant: GuestChallengeGrant) -> str:
    nonce = 0
    while not is_proof_of_work_valid(grant.salt, str(nonce), grant.difficulty_bits):
        nonce += 1
    return str(nonce)


async def _redeem(dependencies: GuestHandlerDependencies) -> OpenGuestSubmission:
    grant = await IssueGuestChallengeHandler(dependencies)(IssueGuestChallenge())
    return OpenGuestSubmission(challenge=grant.challenge, nonce=_solve(grant))


async def _count(
    session_factory: async_sessionmaker[AsyncSession], statement: str
) -> int:
    async with session_factory() as session:
        return int(await session.scalar(text(statement)) or 0)


async def test_concurrent_opens_never_overshoot_the_hourly_cap(
    reports_uow_factory: ReportsFactory,
    provenance_uow_factory: ProvenanceFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    dependencies = _dependencies(
        reports_uow_factory, provenance_uow_factory, opened_per_hour=2
    )
    redemptions = [await _redeem(dependencies) for _ in range(PARALLEL)]
    handler = OpenGuestSubmissionHandler(dependencies)

    outcomes = await asyncio.gather(
        *(handler(redemption) for redemption in redemptions), return_exceptions=True
    )

    opened = [item for item in outcomes if isinstance(item, GuestSubmissionGrant)]
    refused = [item for item in outcomes if isinstance(item, GuestSubmissionLimitError)]
    assert (len(opened), len(refused)) == (2, PARALLEL - 2)
    assert await _count(session_factory, "SELECT count(*) FROM guest_submissions") == 2


async def test_concurrent_identical_reports_store_one_report_and_one_source(
    reports_uow_factory: ReportsFactory,
    provenance_uow_factory: ProvenanceFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    dependencies = _dependencies(reports_uow_factory, provenance_uow_factory)
    opened = await OpenGuestSubmissionHandler(dependencies)(await _redeem(dependencies))
    command = SubmitGuestReport(
        submission_id=opened.submission_id,
        capability=opened.capability,
        content=content(),
    )
    handler = SubmitGuestReportHandler(dependencies)

    receipts = await asyncio.gather(*(handler(command) for _ in range(PARALLEL)))

    assert len(set(receipts)) == 1
    assert await _count(session_factory, "SELECT count(*) FROM reports") == 1
    assert await _count(session_factory, "SELECT count(*) FROM sources") == 1
    assert (
        await _count(
            session_factory,
            "SELECT count(*) FROM sources s JOIN reports r ON r.source_id = s.id "
            "WHERE s.is_referenced",
        )
        == 1
    )


async def test_concurrent_photo_grants_create_three_assets_and_no_orphans(
    reports_uow_factory: ReportsFactory,
    provenance_uow_factory: ProvenanceFactory,
    media_uow_factory: MediaFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = FrozenClock(datetime.now(UTC))
    ids = SequentialIdGenerator(seed=2401)
    storage = FakeStoragePort()
    registrar = RegisterPlatformSourceHandler(provenance_uow_factory, clock, ids)
    gateway = GuestMediaGatewayAdapter(
        request_upload=RequestGuestUploadHandler(
            uow_factory=media_uow_factory,
            storage=storage,
            source_registrar=registrar,
            source_marker=MarkPlatformSourceReferencedHandler(
                provenance_uow_factory, clock, ids
            ),
            upload_ttl=timedelta(minutes=5),
            clock=clock,
            ids=ids,
        ),
        complete_upload=CompleteUploadHandler(
            uow_factory=media_uow_factory,
            storage=storage,
            exif_reader=FakeExifReader(),
            mime_sniffer=FakeMimeSniffer(),
            task_queue=RecordingTaskQueue(),
            clock=clock,
            ids=ids,
        ),
    )
    dependencies = _dependencies(
        reports_uow_factory, provenance_uow_factory, media=gateway
    )
    opened = await OpenGuestSubmissionHandler(dependencies)(await _redeem(dependencies))
    command = RequestGuestMediaUpload(
        submission_id=opened.submission_id,
        capability=opened.capability,
        mime_type="image/jpeg",
        byte_size=2048,
    )
    handler = RequestGuestMediaUploadHandler(dependencies)

    outcomes = await asyncio.gather(
        *(handler(command) for _ in range(PARALLEL)), return_exceptions=True
    )

    refused = [item for item in outcomes if isinstance(item, GuestMediaLimitError)]
    assert len(refused) == PARALLEL - GUEST_MEDIA_MAX
    assert (
        await _count(session_factory, "SELECT count(*) FROM media_assets")
        == GUEST_MEDIA_MAX
    )
    assert (
        await _count(
            session_factory, "SELECT count(*) FROM sources WHERE is_referenced"
        )
        == GUEST_MEDIA_MAX
    )
    assert await _count(session_factory, "SELECT count(*) FROM sources") == (
        GUEST_MEDIA_MAX
    )
