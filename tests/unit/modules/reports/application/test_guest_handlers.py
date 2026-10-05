"""Unit tests for the guest submission use cases (ADR 0020), with fakes only."""

from datetime import timedelta

import pytest

from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.provenance import FakePlatformSourceRegistrar
from tests.fakes.reports import (
    FakeGuestMediaGateway,
    FakeMediaOwnershipChecker,
    InMemoryReportsUnitOfWork,
    SequentialGuestSecretGenerator,
)
from tests.fakes.tasks import RecordingTaskQueue
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.unit.modules.reports.application.support import NOW, content
from yakhnama.modules.media.public import UploadGrant
from yakhnama.modules.provenance.public import (
    RegisterPlatformSource,
    SourceDetail,
    SourceType,
)
from yakhnama.modules.reports.application.commands import (
    CompleteGuestMediaUpload,
    IssueGuestChallenge,
    OpenGuestSubmission,
    RequestGuestMediaUpload,
    SubmitGuestReport,
)
from yakhnama.modules.reports.application.dto import (
    GuestChallengeGrant,
    GuestSubmissionGrant,
)
from yakhnama.modules.reports.application.guest_handlers import (
    GUEST_SOURCE_CITATION,
    GUEST_SOURCE_TITLE,
    CompleteGuestMediaUploadHandler,
    GuestHandlerDependencies,
    IssueGuestChallengeHandler,
    OpenGuestSubmissionHandler,
    RequestGuestMediaUploadHandler,
    SubmitGuestReportHandler,
    content_fingerprint,
)
from yakhnama.modules.reports.application.ports import RUN_TRIAGE_TASK
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
    GuestChallenge,
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
from yakhnama.shared_kernel.errors import ConflictError, PermissionDeniedError
from yakhnama.shared_kernel.ids import EntityId

SECRET = b"unit-test-guest-challenge-key-000000000000"
DIFFICULTY = 4
ASSET_IDS = SequentialIdGenerator(seed=951)


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
        submissions_per_hour: int = 200,
        media: FakeGuestMediaGateway | None = None,
        registrar: FakePlatformSourceRegistrar | None = None,
    ) -> None:
        """Arrange the fakes."""
        self.uow = InMemoryReportsUnitOfWork()
        self.clock = FrozenClock(NOW)
        self.media = (
            FakeGuestMediaGateway(ASSET_IDS.new_id() for _ in range(50))
            if media is None
            else media
        )
        self.checker = FakeMediaOwnershipChecker()
        self.registrar = (
            FakePlatformSourceRegistrar() if registrar is None else registrar
        )
        self.tasks = RecordingTaskQueue()
        self.secrets = SequentialGuestSecretGenerator()
        self.limits = GuestSubmissionLimits(
            difficulty_bits=DIFFICULTY, submissions_per_hour=submissions_per_hour
        )
        self.dependencies = GuestHandlerDependencies(
            uow_factory=InMemoryUnitOfWorkFactory(self.uow),
            signer=HmacGuestChallengeSigner(SECRET),
            secrets=self.secrets,
            media=self.media,
            media_checker=self.checker,
            source_registrar=self.registrar,
            task_queue=self.tasks,
            limits=self.limits,
            clock=self.clock,
            ids=SequentialIdGenerator(seed=952),
        )

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


async def test_open_submission_with_solved_challenge_returns_capability_once() -> None:
    harness = GuestHarness()

    opened = await harness.open()

    stored = harness.uow.guest_submissions.committed[opened.submission_id]
    assert opened.max_media == GUEST_MEDIA_MAX
    assert opened.expires_at == NOW + timedelta(minutes=30)
    assert stored.is_capability(opened.capability)
    assert opened.capability not in stored.model_dump_json()
    assert len(harness.uow.spent_challenges.committed) == 1


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
    harness.clock.advance(timedelta(minutes=10))

    with pytest.raises(GuestChallengeExpiredError):
        await OpenGuestSubmissionHandler(harness.dependencies)(
            OpenGuestSubmission(challenge=grant.challenge, nonce=solve(grant))
        )


async def test_open_submission_wrong_nonce_is_refused() -> None:
    harness = GuestHarness()
    grant = await harness.challenge()

    with pytest.raises(GuestProofInvalidError):
        await OpenGuestSubmissionHandler(harness.dependencies)(
            OpenGuestSubmission(challenge=grant.challenge, nonce=wrong_nonce(grant))
        )

    assert harness.uow.spent_challenges.committed == {}


async def test_open_submission_over_the_hourly_cap_is_refused_with_retry() -> None:
    harness = GuestHarness(submissions_per_hour=1)
    await harness.open()
    harness.clock.advance(timedelta(minutes=20))

    with pytest.raises(GuestSubmissionLimitError) as raised:
        await harness.open()

    assert raised.value.retry_after_seconds == 40 * 60 + 1
    assert len(harness.uow.guest_submissions.committed) == 1


async def test_open_submission_after_the_window_passes_is_allowed() -> None:
    harness = GuestHarness(submissions_per_hour=1)
    await harness.open()
    harness.clock.advance(timedelta(hours=1, seconds=1))

    opened = await harness.open()

    assert opened.submission_id in harness.uow.guest_submissions.committed


async def test_open_submission_forgets_expired_spent_challenges() -> None:
    harness = GuestHarness()
    await harness.open()
    harness.clock.advance(timedelta(minutes=11))

    await harness.open()

    assert len(harness.uow.spent_challenges.committed) == 1


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
            )
        )


async def test_upload_for_unknown_submission_is_the_same_invalid_error() -> None:
    harness = GuestHarness()
    opened = await harness.open()

    with pytest.raises(GuestCapabilityInvalidError):
        await RequestGuestMediaUploadHandler(harness.dependencies)(
            RequestGuestMediaUpload(
                submission_id=ASSET_IDS.new_id(),
                capability=opened.capability,
                mime_type="image/png",
            )
        )


async def test_upload_after_capability_expired_is_refused() -> None:
    harness = GuestHarness()
    opened = await harness.open()
    harness.clock.advance(timedelta(minutes=30))

    with pytest.raises(GuestCapabilityExpiredError):
        await harness.upload(opened)


# --------------------------------------------------------------------------- #
# Media                                                                       #
# --------------------------------------------------------------------------- #


async def test_upload_three_photos_then_the_fourth_is_refused() -> None:
    harness = GuestHarness()
    opened = await harness.open()
    for _ in range(GUEST_MEDIA_MAX):
        await harness.upload(opened)

    with pytest.raises(GuestMediaLimitError):
        await harness.upload(opened)

    stored = harness.uow.guest_submissions.committed[opened.submission_id]
    assert len(stored.media_ids) == GUEST_MEDIA_MAX
    assert set(harness.media.owners.values()) == {opened.submission_id}


class RacingGateway(FakeGuestMediaGateway):
    """Grants an upload while another request takes the last photo slot.

    Implements: Fake (of Adapter).
    """

    def __init__(self) -> None:
        """Create the gateway."""
        super().__init__(ASSET_IDS.new_id() for _ in range(10))
        self.harness: GuestHarness | None = None

    async def request_upload(
        self, owner_id: EntityId, mime_type: GuestImageType
    ) -> UploadGrant:
        """Grant, then let a concurrent request fill the last slot."""
        grant = await super().request_upload(owner_id, mime_type)
        if self.harness is not None:
            store = self.harness.uow.guest_submissions.committed
            store[owner_id] = (
                store[owner_id]
                .attach_media(ASSET_IDS.new_id(), clock=self.harness.clock)
                .state
            )
        return grant


async def test_concurrent_fourth_grant_is_refused_by_the_aggregate() -> None:
    gateway = RacingGateway()
    harness = GuestHarness(media=gateway)
    opened = await harness.open()
    for _ in range(GUEST_MEDIA_MAX - 1):
        await harness.upload(opened)
    gateway.harness = harness

    with pytest.raises(GuestMediaLimitError):
        await harness.upload(opened)


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


async def test_submit_guest_report_stores_guest_channel_and_closes_submission() -> None:
    harness = GuestHarness()
    opened = await harness.open()
    asset_id = (await harness.upload(opened)).asset_id
    command = harness.submit_command(opened, content=content(media_ids=(asset_id,)))

    receipt = await harness.submit()(command)

    submission = harness.uow.guest_submissions.committed[opened.submission_id]
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
    assert report.source_id == harness.registrar.registered[0].id
    assert registration.source_type is SourceType.CITIZEN
    assert registration.details.title == GUEST_SOURCE_TITLE
    assert registration.details.citation == GUEST_SOURCE_CITATION
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
    assert len(harness.registrar.commands) == 1


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


class RacingRegistrar(FakePlatformSourceRegistrar):
    """Registers a source while another request closes the submission.

    Implements: Fake (of Command Handler).
    """

    def __init__(self) -> None:
        """Create the registrar."""
        super().__init__()
        self.harness: GuestHarness | None = None
        self.submission_id: EntityId | None = None
        self.fingerprint = ""

    async def __call__(self, command: RegisterPlatformSource) -> SourceDetail:
        """Register, then let a concurrent request close the submission."""
        detail = await super().__call__(command)
        if self.harness is not None and self.submission_id is not None:
            store = self.harness.uow.guest_submissions.committed
            store[self.submission_id] = (
                store[self.submission_id]
                .record_report(
                    ASSET_IDS.new_id(),
                    "YK-RACE-2222",
                    self.fingerprint,
                    clock=self.harness.clock,
                )
                .state
            )
        return detail


async def test_submit_guest_report_racing_a_first_submit_returns_its_receipt() -> None:
    registrar = RacingRegistrar()
    harness = GuestHarness(registrar=registrar)
    opened = await harness.open()
    command = harness.submit_command(opened)
    registrar.harness = harness
    registrar.submission_id = opened.submission_id
    registrar.fingerprint = content_fingerprint(command.content)

    receipt = await harness.submit()(command)

    assert receipt.reference == "YK-RACE-2222"
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
