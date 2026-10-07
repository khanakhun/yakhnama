"""Unit tests for the guest submission use cases (ADR 0020), with fakes only."""

from collections.abc import Callable
from datetime import timedelta

import pytest

from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.provenance import (
    FakePlatformSourceReferenceMarker,
    FakePlatformSourceRegistrar,
)
from tests.fakes.reports import (
    FakeGuestMediaGateway,
    FakeMediaOwnershipChecker,
    InMemoryGuestSubmissionRepository,
    InMemoryReportRepository,
    InMemoryReportsUnitOfWork,
    SequentialGuestSecretGenerator,
)
from tests.fakes.tasks import RecordingTaskQueue
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.unit.modules.reports.application.support import NOW, content
from yakhnama.modules.media.public import UploadGrant
from yakhnama.modules.provenance.domain.factories import SourceFactory
from yakhnama.modules.provenance.domain.value_objects import SYSTEM_OWNER
from yakhnama.modules.provenance.public import (
    RegisterPlatformSource,
    SourceDetail,
    SourceDetails,
    SourceType,
)
from yakhnama.modules.reports.application.commands import (
    CompleteGuestMediaUpload,
    IssueGuestChallenge,
    OpenGuestSubmission,
    PurgeGuestRecords,
    RequestGuestMediaUpload,
    SubmitGuestReport,
)
from yakhnama.modules.reports.application.dto import (
    GuestChallengeGrant,
    GuestSubmissionGrant,
)
from yakhnama.modules.reports.application.guest_handlers import (
    CHALLENGE_PURGE_GRACE,
    GUEST_SOURCE_CITATION,
    GUEST_SOURCE_TITLE,
    CompleteGuestMediaUploadHandler,
    GuestHandlerDependencies,
    IssueGuestChallengeHandler,
    OpenGuestSubmissionHandler,
    PurgeGuestRecordsHandler,
    RequestGuestMediaUploadHandler,
    SubmitGuestReportHandler,
    content_fingerprint,
)
from yakhnama.modules.reports.application.ports import RUN_TRIAGE_TASK
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.errors import (
    GuestCapabilityExpiredError,
    GuestCapabilityInvalidError,
    GuestChallengeExpiredError,
    GuestChallengeInvalidError,
    GuestChallengeSpentError,
    GuestMediaLimitError,
    GuestMediaNotFoundError,
    GuestProofInvalidError,
    GuestSubmissionClosedError,
    GuestSubmissionLimitError,
)
from yakhnama.modules.reports.domain.events import ReportSubmitted
from yakhnama.modules.reports.domain.guest_submissions import (
    GUEST_MEDIA_MAX,
    GuestCap,
    GuestChallenge,
    GuestSubmission,
    GuestSubmissionLimits,
    is_proof_of_work_valid,
)
from yakhnama.modules.reports.domain.value_objects import (
    GuestImageType,
    ReportChannel,
    ReportStatus,
)
from yakhnama.modules.reports.infrastructure.adapters.guest import (
    HmacGuestChallengeSigner,
)
from yakhnama.shared_kernel.errors import (
    ConflictError,
    PermissionDeniedError,
    YakhnamaError,
)
from yakhnama.shared_kernel.ids import EntityId

SECRET = b"unit-test-guest-challenge-key-000000000000"
DIFFICULTY = 4
ASSET_IDS = SequentialIdGenerator(seed=951)
PHOTO_BYTES = 345_678


def solve(grant: GuestChallengeGrant) -> str:
    """Return the smallest nonce solving the granted challenge."""
    nonce = 0
    while not is_proof_of_work_valid(grant.salt, str(nonce), grant.difficulty_bits):
        nonce += 1
    return str(nonce)


def wrong_nonce(grant: GuestChallengeGrant) -> str:
    """Return a nonce that does not solve the granted challenge."""
    return next(
        str(candidate)
        for candidate in range(1000)
        if not is_proof_of_work_valid(grant.salt, str(candidate), DIFFICULTY)
    )


class GuestHarness:
    """The guest handlers over fakes sharing one reports unit of work."""

    def __init__(
        self,
        *,
        media: FakeGuestMediaGateway | None = None,
        registrar: FakePlatformSourceRegistrar | None = None,
        **limits: object,
    ) -> None:
        """Arrange the fakes; ``limits`` override ``GuestSubmissionLimits``."""
        self.clock = FrozenClock(NOW)
        self.database_clock = FrozenClock(NOW)
        self.uow = InMemoryReportsUnitOfWork(database_clock=self.database_clock)
        self.media = FakeGuestMediaGateway() if media is None else media
        self.checker = FakeMediaOwnershipChecker()
        self.registrar = (
            FakePlatformSourceRegistrar() if registrar is None else registrar
        )
        self.marker = FakePlatformSourceReferenceMarker(self.registrar)
        self.tasks = RecordingTaskQueue()
        self.secrets = SequentialGuestSecretGenerator()
        self.limits = GuestSubmissionLimits.model_validate(
            {"difficulty_bits": DIFFICULTY, **limits}
        )
        self.dependencies = GuestHandlerDependencies(
            uow_factory=InMemoryUnitOfWorkFactory(self.uow),
            signer=HmacGuestChallengeSigner(SECRET),
            secrets=self.secrets,
            media=self.media,
            media_checker=self.checker,
            source_registrar=self.registrar,
            source_marker=self.marker,
            task_queue=self.tasks,
            limits=self.limits,
            clock=self.clock,
            ids=SequentialIdGenerator(seed=952),
        )

    def advance(self, delta: timedelta) -> None:
        """Move the application's and the database's clocks together."""
        self.clock.advance(delta)
        self.database_clock.advance(delta)

    async def challenge(self) -> GuestChallengeGrant:
        """Issue a challenge."""
        return await IssueGuestChallengeHandler(self.dependencies)(
            IssueGuestChallenge()
        )

    async def open(self) -> GuestSubmissionGrant:
        """Issue, solve and redeem a challenge."""
        grant = await self.challenge()
        return await OpenGuestSubmissionHandler(self.dependencies)(
            OpenGuestSubmission(challenge=grant.challenge, nonce=solve(grant))
        )

    async def upload(
        self, submission: GuestSubmissionGrant, capability: str | None = None
    ) -> UploadGrant:
        """Request one photo upload for ``submission``."""
        grant = await RequestGuestMediaUploadHandler(self.dependencies)(
            RequestGuestMediaUpload(
                submission_id=submission.submission_id,
                capability=submission.capability if capability is None else capability,
                mime_type="image/jpeg",
                byte_size=PHOTO_BYTES,
            )
        )
        self.checker.owners[grant.asset_id] = submission.submission_id
        return grant

    def submit_command(
        self, submission: GuestSubmissionGrant, **overrides: object
    ) -> SubmitGuestReport:
        """Return a guest report command for ``submission``."""
        return SubmitGuestReport.model_validate(
            {
                "submission_id": submission.submission_id,
                "capability": submission.capability,
                "content": content(),
                **overrides,
            }
        )

    def submit(self) -> SubmitGuestReportHandler:
        """Return the submit handler."""
        return SubmitGuestReportHandler(self.dependencies)

    def stored(self, submission: GuestSubmissionGrant) -> GuestSubmission:
        """Return the committed state of ``submission``."""
        return self.uow.guest_submissions.committed[submission.submission_id]


# --------------------------------------------------------------------------- #
# Challenges and opening                                                      #
# --------------------------------------------------------------------------- #


async def test_issue_challenge_signs_salt_difficulty_and_whole_second_expiry() -> None:
    harness = GuestHarness()

    grant = await harness.challenge()

    verified = HmacGuestChallengeSigner(SECRET).verify(grant.challenge)
    assert grant.algorithm == "SHA-256"
    assert grant.difficulty_bits == DIFFICULTY
    assert grant.expires_at == (NOW + timedelta(minutes=10)).replace(microsecond=0)
    assert verified == GuestChallenge(
        salt=grant.salt, difficulty_bits=DIFFICULTY, expires_at=grant.expires_at
    )


async def test_issue_challenge_difficulty_rises_with_submissions_opened() -> None:
    harness = GuestHarness(difficulty_step=2, difficulty_max_bits=DIFFICULTY + 2)

    difficulties = []
    for _ in range(6):
        difficulties.append((await harness.challenge()).difficulty_bits)
        await harness.open()

    assert difficulties == [4, 4, 5, 5, 6, 6]


async def test_issue_challenge_difficulty_falls_back_after_the_hour() -> None:
    harness = GuestHarness(difficulty_step=1)
    await harness.open()
    harness.advance(timedelta(hours=1, seconds=1))

    grant = await harness.challenge()

    assert grant.difficulty_bits == DIFFICULTY


async def test_open_submission_with_solved_challenge_returns_capability_once() -> None:
    harness = GuestHarness()

    opened = await harness.open()

    stored = harness.stored(opened)
    assert opened.max_media == GUEST_MEDIA_MAX
    assert opened.expires_at == NOW + timedelta(minutes=30)
    assert stored.is_capability(opened.capability)
    assert opened.capability not in stored.model_dump_json()
    assert len(harness.uow.spent_challenges.committed) == 1
    assert harness.uow.guest_submissions.locked_caps == [GuestCap.OPENED]


async def test_open_submission_replayed_challenge_is_refused() -> None:
    harness = GuestHarness()
    grant = await harness.challenge()
    redeem = OpenGuestSubmission(challenge=grant.challenge, nonce=solve(grant))
    handler = OpenGuestSubmissionHandler(harness.dependencies)
    await handler(redeem)

    with pytest.raises(GuestChallengeSpentError):
        await handler(redeem)

    assert len(harness.uow.guest_submissions.committed) == 1


async def test_open_submission_forged_challenge_is_invalid() -> None:
    harness = GuestHarness()
    grant = await harness.challenge()
    forged = grant.challenge.replace(f".{DIFFICULTY}.", ".1.", 1)

    with pytest.raises(GuestChallengeInvalidError):
        await OpenGuestSubmissionHandler(harness.dependencies)(
            OpenGuestSubmission(challenge=forged, nonce="0")
        )


async def test_open_submission_expired_challenge_is_refused() -> None:
    harness = GuestHarness()
    grant = await harness.challenge()
    harness.advance(timedelta(minutes=10))

    with pytest.raises(GuestChallengeExpiredError):
        await OpenGuestSubmissionHandler(harness.dependencies)(
            OpenGuestSubmission(challenge=grant.challenge, nonce=solve(grant))
        )


async def test_open_submission_expired_by_the_database_clock_is_refused() -> None:
    harness = GuestHarness()
    grant = await harness.challenge()
    harness.database_clock.advance(timedelta(minutes=10))

    with pytest.raises(GuestChallengeExpiredError):
        await OpenGuestSubmissionHandler(harness.dependencies)(
            OpenGuestSubmission(challenge=grant.challenge, nonce=solve(grant))
        )

    assert harness.uow.guest_submissions.committed == {}


async def test_open_submission_wrong_nonce_is_refused() -> None:
    harness = GuestHarness()
    grant = await harness.challenge()

    with pytest.raises(GuestProofInvalidError):
        await OpenGuestSubmissionHandler(harness.dependencies)(
            OpenGuestSubmission(challenge=grant.challenge, nonce=wrong_nonce(grant))
        )

    assert harness.uow.spent_challenges.committed == {}


async def test_open_submission_over_the_opened_cap_is_refused_with_retry() -> None:
    harness = GuestHarness(opened_per_hour=1)
    await harness.open()
    harness.advance(timedelta(minutes=20))

    with pytest.raises(GuestSubmissionLimitError) as raised:
        await harness.open()

    assert raised.value.retry_after_seconds == 40 * 60 + 1
    assert len(harness.uow.guest_submissions.committed) == 1


async def test_open_submission_after_the_window_passes_is_allowed() -> None:
    harness = GuestHarness(opened_per_hour=1)
    await harness.open()
    harness.advance(timedelta(hours=1, seconds=1))

    opened = await harness.open()

    assert opened.submission_id in harness.uow.guest_submissions.committed


async def test_open_submission_when_the_reports_cap_is_reached_is_refused() -> None:
    harness = GuestHarness(reports_per_hour=1)
    first = await harness.open()
    await harness.submit()(harness.submit_command(first))
    harness.advance(timedelta(minutes=45))

    with pytest.raises(GuestSubmissionLimitError) as raised:
        await harness.open()

    assert raised.value.retry_after_seconds == 15 * 60 + 1
    assert len(harness.uow.guest_submissions.committed) == 1


# --------------------------------------------------------------------------- #
# Capability checks                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("capability", [None, "w" * 43, ""])
async def test_upload_with_missing_or_wrong_capability_is_invalid(
    capability: str | None,
) -> None:
    harness = GuestHarness()
    opened = await harness.open()

    with pytest.raises(GuestCapabilityInvalidError):
        await RequestGuestMediaUploadHandler(harness.dependencies)(
            RequestGuestMediaUpload(
                submission_id=opened.submission_id,
                capability=capability,
                mime_type="image/png",
                byte_size=PHOTO_BYTES,
            )
        )

    assert harness.media.owners == {}


async def test_upload_for_unknown_submission_is_the_same_invalid_error() -> None:
    harness = GuestHarness()
    opened = await harness.open()

    with pytest.raises(GuestCapabilityInvalidError):
        await RequestGuestMediaUploadHandler(harness.dependencies)(
            RequestGuestMediaUpload(
                submission_id=ASSET_IDS.new_id(),
                capability=opened.capability,
                mime_type="image/png",
                byte_size=PHOTO_BYTES,
            )
        )


async def test_upload_after_capability_expired_is_refused() -> None:
    harness = GuestHarness()
    opened = await harness.open()
    harness.advance(timedelta(minutes=30))

    with pytest.raises(GuestCapabilityExpiredError):
        await harness.upload(opened)


# --------------------------------------------------------------------------- #
# Media                                                                       #
# --------------------------------------------------------------------------- #


async def test_upload_reserves_the_slot_then_creates_that_asset() -> None:
    harness = GuestHarness()
    opened = await harness.open()

    grant = await harness.upload(opened)

    assert harness.stored(opened).media_ids == (grant.asset_id,)
    assert harness.media.owners == {grant.asset_id: opened.submission_id}
    assert harness.media.sizes == {grant.asset_id: PHOTO_BYTES}


async def test_upload_three_photos_then_the_fourth_creates_nothing() -> None:
    harness = GuestHarness()
    opened = await harness.open()
    for _ in range(GUEST_MEDIA_MAX):
        await harness.upload(opened)

    with pytest.raises(GuestMediaLimitError):
        await harness.upload(opened)

    assert len(harness.stored(opened).media_ids) == GUEST_MEDIA_MAX
    assert len(harness.media.owners) == GUEST_MEDIA_MAX


class ConcurrentSaveRepository(InMemoryGuestSubmissionRepository):
    """Lets other requests commit a change just before this request's saves.

    Implements: Fake (of Repository).
    """

    def __init__(
        self,
        inner: InMemoryGuestSubmissionRepository,
        races: list[Callable[[GuestSubmission], GuestSubmission]],
    ) -> None:
        """Share ``inner``'s committed rows; run one race per save, in order."""
        super().__init__()
        self.committed = inner.committed
        self.races = races

    async def save(self, submission: GuestSubmission) -> None:
        """Commit the next concurrent change, then save as the real one would."""
        if self.races:
            race = self.races.pop(0)
            stored = self.committed[submission.id]
            self.committed[submission.id] = race(stored)
        await super().save(submission)


def concurrent(
    harness: GuestHarness, *races: Callable[[GuestSubmission], GuestSubmission]
) -> ConcurrentSaveRepository:
    """Lay ``races`` over the harness's guest submission repository."""
    repository = ConcurrentSaveRepository(harness.uow.guest_submissions, list(races))
    harness.uow.guest_submissions = repository
    return repository


def other_photo(harness: GuestHarness) -> Callable[[GuestSubmission], GuestSubmission]:
    """Return a race in which another request reserves a photo slot."""
    return lambda stored: (
        stored.attach_media(ASSET_IDS.new_id(), clock=harness.clock).state
    )


async def test_parallel_uploads_retry_the_reservation_and_both_succeed() -> None:
    harness = GuestHarness()
    opened = await harness.open()
    concurrent(harness, other_photo(harness))

    grant = await harness.upload(opened)

    media_ids = harness.stored(opened).media_ids
    assert len(media_ids) == 2
    assert media_ids[-1] == grant.asset_id
    assert list(harness.media.owners) == [grant.asset_id]


async def test_parallel_uploads_taking_the_last_slots_refuse_before_any_asset() -> None:
    harness = GuestHarness()
    opened = await harness.open()
    await harness.upload(opened)
    concurrent(
        harness, lambda stored: other_photo(harness)(other_photo(harness)(stored))
    )

    with pytest.raises(GuestMediaLimitError):
        await harness.upload(opened)

    assert len(harness.stored(opened).media_ids) == GUEST_MEDIA_MAX
    assert len(harness.media.owners) == 1


async def test_upload_reservation_conflicting_every_time_gives_up() -> None:
    harness = GuestHarness()
    opened = await harness.open()
    concurrent(
        harness,
        *(
            lambda stored: stored.model_copy(update={"version": stored.version + 1})
            for _ in range(GUEST_MEDIA_MAX + 1)
        ),
    )

    with pytest.raises(ConflictError):
        await harness.upload(opened)

    assert harness.media.owners == {}


class FailingGateway(FakeGuestMediaGateway):
    """Refuses every grant, as storage does when it is down.

    Implements: Fake (of Adapter).
    """

    async def request_upload(
        self,
        owner_id: EntityId,
        mime_type: GuestImageType,
        *,
        asset_id: EntityId,
        byte_size: int,
    ) -> UploadGrant:
        """Fail."""
        del owner_id, mime_type, asset_id, byte_size
        message = "storage is unavailable"
        raise YakhnamaError(message)


async def test_upload_whose_grant_fails_gives_the_slot_back() -> None:
    harness = GuestHarness(media=FailingGateway())
    opened = await harness.open()

    with pytest.raises(YakhnamaError, match="storage is unavailable"):
        await harness.upload(opened)

    assert harness.stored(opened).media_ids == ()
    assert harness.stored(opened).version == 3


async def test_upload_failure_after_the_submission_vanished_still_raises() -> None:
    harness = GuestHarness(media=FailingGateway())
    opened = await harness.open()
    concurrent(harness)

    class Vanishing(FailingGateway):
        async def request_upload(
            self,
            owner_id: EntityId,
            mime_type: GuestImageType,
            *,
            asset_id: EntityId,
            byte_size: int,
        ) -> UploadGrant:
            harness.uow.guest_submissions.committed.pop(owner_id)
            return await super().request_upload(
                owner_id, mime_type, asset_id=asset_id, byte_size=byte_size
            )

    harness.dependencies.media = Vanishing()

    with pytest.raises(YakhnamaError, match="storage is unavailable"):
        await harness.upload(opened)

    assert opened.submission_id not in harness.uow.guest_submissions.committed


async def test_complete_granted_photo_returns_it() -> None:
    harness = GuestHarness()
    opened = await harness.open()
    asset_id = (await harness.upload(opened)).asset_id

    completed = await CompleteGuestMediaUploadHandler(harness.dependencies)(
        CompleteGuestMediaUpload(
            submission_id=opened.submission_id,
            capability=opened.capability,
            asset_id=asset_id,
        )
    )

    assert completed.id == asset_id
    assert harness.media.completed == [asset_id]


async def test_complete_photo_after_the_report_until_expiry_is_allowed() -> None:
    harness = GuestHarness()
    opened = await harness.open()
    asset_id = (await harness.upload(opened)).asset_id
    await harness.submit()(harness.submit_command(opened))
    harness.advance(timedelta(minutes=29))

    completed = await CompleteGuestMediaUploadHandler(harness.dependencies)(
        CompleteGuestMediaUpload(
            submission_id=opened.submission_id,
            capability=opened.capability,
            asset_id=asset_id,
        )
    )

    [report] = harness.uow.reports.committed.values()
    assert completed.id == asset_id
    assert report.media_ids == ()


async def test_complete_photo_after_expiry_is_refused() -> None:
    harness = GuestHarness()
    opened = await harness.open()
    asset_id = (await harness.upload(opened)).asset_id
    harness.advance(timedelta(minutes=30))

    with pytest.raises(GuestCapabilityExpiredError):
        await CompleteGuestMediaUploadHandler(harness.dependencies)(
            CompleteGuestMediaUpload(
                submission_id=opened.submission_id,
                capability=opened.capability,
                asset_id=asset_id,
            )
        )


async def test_complete_photo_not_granted_to_submission_is_not_found() -> None:
    harness = GuestHarness()
    opened = await harness.open()

    with pytest.raises(GuestMediaNotFoundError):
        await CompleteGuestMediaUploadHandler(harness.dependencies)(
            CompleteGuestMediaUpload(
                submission_id=opened.submission_id,
                capability=opened.capability,
                asset_id=ASSET_IDS.new_id(),
            )
        )


# --------------------------------------------------------------------------- #
# The report                                                                  #
# --------------------------------------------------------------------------- #


async def test_submit_guest_report_stores_guest_channel_and_files_submission() -> None:
    harness = GuestHarness()
    opened = await harness.open()
    asset_id = (await harness.upload(opened)).asset_id
    command = harness.submit_command(opened, content=content(media_ids=(asset_id,)))

    receipt = await harness.submit()(command)

    submission = harness.stored(opened)
    [report] = harness.uow.reports.committed.values()
    [registration] = harness.registrar.commands
    assert receipt.reference == submission.reference
    assert receipt.submitted_at == NOW
    assert submission.report_id == report.id
    assert submission.content_fingerprint == content_fingerprint(command.content)
    assert report.channel is ReportChannel.GUEST
    assert report.reporter_id == opened.submission_id
    assert report.status is ReportStatus.SUBMITTED
    assert report.organization_id is None
    assert report.source_id == submission.source_id == registration.source_id
    assert registration.source_type is SourceType.CITIZEN
    assert registration.details.title == GUEST_SOURCE_TITLE
    assert registration.details.citation == GUEST_SOURCE_CITATION
    assert harness.marker.marked_ids == (report.source_id,)
    assert harness.uow.guest_submissions.locked_caps[-1] is GuestCap.REPORTS
    assert any(
        isinstance(event, ReportSubmitted) and event.channel is ReportChannel.GUEST
        for event in harness.uow.committed_events
    )
    assert len(harness.tasks.of(RUN_TRIAGE_TASK)) == 1


async def test_submit_guest_report_retry_with_same_content_returns_same_receipt() -> (
    None
):
    harness = GuestHarness()
    opened = await harness.open()
    command = harness.submit_command(opened)
    first = await harness.submit()(command)

    again = await harness.submit()(command)

    assert again == first
    assert len(harness.uow.reports.committed) == 1
    assert len(harness.registrar.registered) == 1
    assert len(harness.tasks.of(RUN_TRIAGE_TASK)) == 1


async def test_submit_guest_report_retry_after_expiry_within_grace_replays() -> None:
    harness = GuestHarness()
    opened = await harness.open()
    command = harness.submit_command(opened)
    first = await harness.submit()(command)
    harness.advance(timedelta(minutes=30) + timedelta(hours=23))

    again = await harness.submit()(command)

    assert again == first


async def test_submit_guest_report_retry_after_the_grace_is_expired() -> None:
    harness = GuestHarness()
    opened = await harness.open()
    command = harness.submit_command(opened)
    await harness.submit()(command)
    harness.advance(timedelta(minutes=30) + timedelta(hours=24))

    with pytest.raises(GuestCapabilityExpiredError):
        await harness.submit()(command)


async def test_submit_guest_report_first_attempt_after_expiry_is_refused() -> None:
    harness = GuestHarness()
    opened = await harness.open()
    harness.advance(timedelta(minutes=30))

    with pytest.raises(GuestCapabilityExpiredError):
        await harness.submit()(harness.submit_command(opened))

    assert harness.registrar.commands == []


async def test_submit_guest_report_with_different_content_is_closed() -> None:
    harness = GuestHarness()
    opened = await harness.open()
    await harness.submit()(harness.submit_command(opened))

    with pytest.raises(GuestSubmissionClosedError):
        await harness.submit()(
            harness.submit_command(
                opened, content=content(description="A different report entirely.")
            )
        )


async def test_upload_after_report_is_refused() -> None:
    harness = GuestHarness()
    opened = await harness.open()
    await harness.submit()(harness.submit_command(opened))

    with pytest.raises(GuestSubmissionClosedError):
        await harness.upload(opened)


async def test_submit_guest_report_with_foreign_media_is_denied() -> None:
    harness = GuestHarness()
    opened = await harness.open()
    foreign = ASSET_IDS.new_id()

    with pytest.raises(PermissionDeniedError):
        await harness.submit()(
            harness.submit_command(opened, content=content(media_ids=(foreign,)))
        )

    assert harness.uow.reports.committed == {}
    assert harness.stored(opened).is_open
    assert harness.registrar.commands == []


async def test_submit_guest_report_over_the_reports_cap_is_refused_with_retry() -> None:
    harness = GuestHarness(reports_per_hour=1)
    first = await harness.open()
    second = await harness.open()
    await harness.submit()(harness.submit_command(first))
    harness.advance(timedelta(minutes=10))

    with pytest.raises(GuestSubmissionLimitError) as raised:
        await harness.submit()(harness.submit_command(second))

    assert raised.value.retry_after_seconds == 50 * 60 + 1
    assert harness.stored(second).is_open
    assert len(harness.registrar.registered) == 1


def other_report(
    harness: GuestHarness, fingerprint: str, *, filed: bool
) -> Callable[[GuestSubmission], GuestSubmission]:
    """Return a race in which another request reserves (and maybe files) a report."""

    def race(stored: GuestSubmission) -> GuestSubmission:
        source_id = ASSET_IDS.new_id()
        reserved = stored.reserve_report(
            fingerprint, source_id, clock=harness.clock
        ).state
        if not filed:
            return reserved
        # The winner registered its source before filing.
        harness.registrar.registered.append(
            SourceFactory()
            .register(
                SourceType.CITIZEN,
                SourceDetails(title=GUEST_SOURCE_TITLE, citation=GUEST_SOURCE_CITATION),
                SYSTEM_OWNER,
                clock=harness.clock,
                ids=ASSET_IDS,
                source_id=source_id,
            )
            .state
        )
        return reserved.record_report(
            ASSET_IDS.new_id(), "YK-RACE-2222", clock=harness.clock
        ).state

    return race


async def test_concurrent_identical_submit_that_lost_returns_the_winners_receipt() -> (
    None
):
    harness = GuestHarness()
    opened = await harness.open()
    command = harness.submit_command(opened)
    concurrent(
        harness, other_report(harness, content_fingerprint(command.content), filed=True)
    )

    receipt = await harness.submit()(command)

    assert receipt.reference == "YK-RACE-2222"
    assert harness.uow.reports.committed == {}
    assert harness.registrar.commands == []
    assert harness.marker.marked_ids == (harness.stored(opened).source_id,)


async def test_concurrent_identical_submit_still_in_flight_is_completed_once() -> None:
    harness = GuestHarness()
    opened = await harness.open()
    command = harness.submit_command(opened)
    concurrent(
        harness,
        other_report(harness, content_fingerprint(command.content), filed=False),
    )

    receipt = await harness.submit()(command)

    stored = harness.stored(opened)
    [report] = harness.uow.reports.committed.values()
    [registration] = harness.registrar.commands
    assert receipt.reference == stored.reference
    assert registration.source_id == stored.source_id == report.source_id


async def test_concurrent_different_submit_that_won_closes_this_one() -> None:
    harness = GuestHarness()
    opened = await harness.open()
    concurrent(harness, other_report(harness, "e" * 64, filed=False))

    with pytest.raises(GuestSubmissionClosedError):
        await harness.submit()(harness.submit_command(opened))

    assert harness.registrar.commands == []


class FilingRaceReports(InMemoryReportRepository):
    """Lets another request file the submission while this request adds its report.

    Implements: Fake (of Repository).
    """

    def __init__(self, harness: GuestHarness, *, files: bool) -> None:
        """Share the harness's reports; ``files`` says whether the race files."""
        super().__init__()
        self.committed = harness.uow.reports.committed
        self.harness = harness
        self.files = files

    async def add(self, report: Report) -> None:
        """Bump the submission concurrently, then add as the real one would."""
        submissions = self.harness.uow.guest_submissions.committed
        stored = submissions[report.reporter_id]
        submissions[report.reporter_id] = (
            stored.record_report(
                ASSET_IDS.new_id(), "YK-RACE-3333", clock=self.harness.clock
            ).state
            if self.files
            else stored.model_copy(update={"version": stored.version + 1})
        )
        await super().add(report)


async def test_submit_losing_the_filing_race_returns_the_winners_receipt() -> None:
    harness = GuestHarness()
    opened = await harness.open()
    harness.uow.reports = FilingRaceReports(harness, files=True)

    receipt = await harness.submit()(harness.submit_command(opened))

    assert receipt.reference == "YK-RACE-3333"
    assert harness.uow.reports.committed == {}
    assert harness.tasks.of(RUN_TRIAGE_TASK) == []


async def test_submit_filing_conflict_without_a_winner_propagates() -> None:
    harness = GuestHarness()
    opened = await harness.open()
    harness.uow.reports = FilingRaceReports(harness, files=False)

    with pytest.raises(ConflictError):
        await harness.submit()(harness.submit_command(opened))

    assert harness.uow.reports.committed == {}


class PurgingRegistrar(FakePlatformSourceRegistrar):
    """Registers the source while the retention purge deletes the submission.

    Implements: Fake (of Command Handler).
    """

    harness: GuestHarness | None = None

    async def __call__(self, command: RegisterPlatformSource) -> SourceDetail:
        """Register, then forget every submission."""
        detail = await super().__call__(command)
        if self.harness is not None:
            self.harness.uow.guest_submissions.committed.clear()
        return detail


async def test_submit_whose_submission_vanished_before_filing_is_closed() -> None:
    registrar = PurgingRegistrar()
    harness = GuestHarness(registrar=registrar)
    opened = await harness.open()
    registrar.harness = harness

    with pytest.raises(GuestSubmissionClosedError):
        await harness.submit()(harness.submit_command(opened))

    assert harness.uow.reports.committed == {}


async def test_submit_guest_report_draws_a_new_reference_after_a_collision() -> None:
    harness = GuestHarness()
    first = await harness.open()
    harness.secrets.references = ["YK-AAAA-BBBB"]
    await harness.submit()(harness.submit_command(first))
    second = await harness.open()
    harness.secrets.references = ["YK-AAAA-BBBB", "YK-CCCC-DDDD"]

    receipt = await harness.submit()(harness.submit_command(second))

    assert receipt.reference == "YK-CCCC-DDDD"


async def test_submit_guest_report_without_free_reference_is_a_conflict() -> None:
    harness = GuestHarness()
    opened = await harness.open()
    first = await harness.submit()(harness.submit_command(opened))
    second = await harness.open()
    harness.secrets.references = [first.reference] * 5

    with pytest.raises(ConflictError):
        await harness.submit()(harness.submit_command(second))


# --------------------------------------------------------------------------- #
# Retention                                                                   #
# --------------------------------------------------------------------------- #


async def test_purge_forgets_spent_challenges_only_after_the_grace() -> None:
    harness = GuestHarness()
    await harness.open()
    purge = PurgeGuestRecordsHandler(harness.dependencies)
    harness.advance(timedelta(minutes=10) + CHALLENGE_PURGE_GRACE)

    kept = await purge(PurgeGuestRecords())
    harness.advance(timedelta(seconds=1))
    purged = await purge(PurgeGuestRecords())

    assert kept.challenges == 0
    assert purged.challenges == 1
    assert harness.uow.spent_challenges.committed == {}


async def test_purge_forgets_unfiled_submissions_after_the_grace_keeps_filed() -> None:
    harness = GuestHarness()
    unfiled = await harness.open()
    filed = await harness.open()
    await harness.submit()(harness.submit_command(filed))
    purge = PurgeGuestRecordsHandler(harness.dependencies)
    harness.advance(timedelta(minutes=30) + timedelta(hours=24))

    kept = await purge(PurgeGuestRecords())
    harness.advance(timedelta(seconds=1))
    purged = await purge(PurgeGuestRecords())

    remaining = harness.uow.guest_submissions.committed
    assert kept.submissions == 0
    assert purged.submissions == 1
    assert unfiled.submission_id not in remaining
    assert filed.submission_id in remaining
