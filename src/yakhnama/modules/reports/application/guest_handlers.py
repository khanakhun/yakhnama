"""Use cases of guest reporting: a report from a person without an account (ADR 0020).

The guest drives five steps, each its own handler:

1. ``IssueGuestChallengeHandler`` signs a proof-of-work challenge; nothing is stored.
2. ``OpenGuestSubmissionHandler`` checks the signature, the expiry and the answer,
   checks the global hourly cap, spends the challenge and opens a submission with a
   fresh capability, in one unit of work, so a challenge opens at most one
   submission even when two requests race.
3. ``RequestGuestMediaUploadHandler`` grants up to ``GUEST_MEDIA_MAX`` photo
   uploads, owned by the submission, through the media module.
4. ``CompleteGuestMediaUploadHandler`` completes one of them.
5. ``SubmitGuestReportHandler`` registers a platform-owned source, then stores the
   report (channel ``guest``, reporter = the submission) and closes the submission
   in one unit of work, and enqueues triage as for every report. A retry of the
   same content returns the same receipt; different content is refused.

Steps 3 to 5 first check the capability (``_authorise``): an unknown submission, a
missing or wrong capability are one and the same error, so submission ids cannot be
probed; an expired capability has its own error, so the client can tell the guest
to start again.

The cross-module calls (media, provenance) run outside the reports unit of work,
like ``SubmitReportHandler``'s; an interruption between them leaves an unused asset
or source that nothing cites, which is harmless.

Patterns: Command Handler, Unit of Work, Dependency Injection.
"""

from datetime import timedelta
from typing import Final

from yakhnama.modules.media.public import UploadGrant
from yakhnama.modules.provenance.public import (
    PlatformSourceRegistrar,
    RegisterPlatformSource,
    SourceDetails,
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
    GuestMediaAsset,
    GuestReportReceipt,
    GuestSubmissionGrant,
)
from yakhnama.modules.reports.application.handlers import (
    enqueue_triage,
    require_own_media,
)
from yakhnama.modules.reports.application.ports import (
    GuestChallengeSigner,
    GuestMediaGateway,
    GuestSecretGenerator,
    MediaOwnershipChecker,
    ReportsUnitOfWork,
    ReportsUnitOfWorkFactory,
)
from yakhnama.modules.reports.domain.errors import (
    GuestCapabilityExpiredError,
    GuestCapabilityInvalidError,
    GuestChallengeExpiredError,
    GuestChallengeInvalidError,
    GuestMediaLimitError,
    GuestMediaNotFoundError,
    GuestProofInvalidError,
    GuestSubmissionClosedError,
    GuestSubmissionLimitError,
)
from yakhnama.modules.reports.domain.factories import ReportFactory
from yakhnama.modules.reports.domain.guest_submissions import (
    GUEST_MEDIA_MAX,
    PROOF_OF_WORK_ALGORITHM,
    GuestChallenge,
    GuestSubmission,
    GuestSubmissionLimits,
    sha256_hex,
)
from yakhnama.modules.reports.domain.value_objects import (
    ReportAttribution,
    ReportChannel,
    ReportContent,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import ConflictError
from yakhnama.shared_kernel.ids import EntityId, IdGenerator
from yakhnama.shared_kernel.tasks import TaskQueue

# Platform-written title and citation of a guest report's source (**proposed**,
# ADR 0020); like every report source they name neither the guest nor the report.
GUEST_SOURCE_TITLE: Final = "Guest community report"
GUEST_SOURCE_CITATION: Final = "Yakhnama guest community report"

CAP_WINDOW: Final = timedelta(hours=1)
"""The rolling window of ``GuestSubmissionLimits.submissions_per_hour``."""

REFERENCE_ATTEMPTS: Final = 5
"""Fresh references drawn before giving up; one collision is already rare."""


def content_fingerprint(content: ReportContent) -> str:
    """Return the SHA-256 of a report content's canonical JSON.

    Two submissions of the same content give the same fingerprint, so a retried
    guest report is recognised without storing a second copy of what it says.

    Args:
        content: The report content.

    Returns:
        64 lower-case hexadecimal characters.
    """
    return sha256_hex(content.model_dump_json())


class GuestHandlerDependencies:
    """What every guest submission handler is built from.

    Implements: Dependency Injection.

    Attributes:
        uow_factory: Opens a reports unit of work per step.
        signer: Signs and verifies challenges.
        secrets: Draws salts, capabilities and references.
        media: Requests and completes guest uploads (media module).
        media_checker: Confirms a report's media belong to its submission.
        source_registrar: Registers the platform-owned report source.
        task_queue: Schedules triage of the guest report.
        limits: Difficulty, lifetimes and the hourly cap, from settings.
        clock: Source of every timestamp.
        ids: Source of submission, report and event ids.
    """

    def __init__(  # noqa: PLR0913  # reason: one keyword per injected port, all required
        self,
        *,
        uow_factory: ReportsUnitOfWorkFactory,
        signer: GuestChallengeSigner,
        secrets: GuestSecretGenerator,
        media: GuestMediaGateway,
        media_checker: MediaOwnershipChecker,
        source_registrar: PlatformSourceRegistrar,
        task_queue: TaskQueue,
        limits: GuestSubmissionLimits,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Group the dependencies.

        Args:
            uow_factory: Opens a reports unit of work per step.
            signer: Signs and verifies challenges.
            secrets: Draws salts, capabilities and references.
            media: Requests and completes guest uploads.
            media_checker: Confirms a report's media belong to its submission.
            source_registrar: Registers the platform-owned report source.
            task_queue: Schedules triage of the guest report.
            limits: Difficulty, lifetimes and the hourly cap.
            clock: Source of every timestamp.
            ids: Source of submission, report and event ids.
        """
        self.uow_factory = uow_factory
        self.signer = signer
        self.secrets = secrets
        self.media = media
        self.media_checker = media_checker
        self.source_registrar = source_registrar
        self.task_queue = task_queue
        self.limits = limits
        self.clock = clock
        self.ids = ids


async def _authorise(
    uow: ReportsUnitOfWork,
    dependencies: GuestHandlerDependencies,
    submission_id: EntityId,
    capability: str | None,
) -> GuestSubmission:
    submission = await uow.guest_submissions.get(submission_id)
    if (
        submission is None
        or capability is None
        or not submission.is_capability(capability)
    ):
        raise GuestCapabilityInvalidError.create()
    if submission.is_expired(dependencies.clock.now()):
        raise GuestCapabilityExpiredError.for_submission(submission.id)
    return submission


def _require_open(submission: GuestSubmission) -> None:
    if not submission.is_open:
        raise GuestSubmissionClosedError.for_submission(submission.id)


def _receipt(submission: GuestSubmission) -> GuestReportReceipt:
    # Only a closed submission has a receipt; the invariant sets both together.
    if submission.reference is None or submission.submitted_at is None:
        raise GuestSubmissionClosedError.for_submission(submission.id)
    return GuestReportReceipt(
        reference=submission.reference, submitted_at=submission.submitted_at
    )


class IssueGuestChallengeHandler:
    """Hand out a signed proof-of-work challenge; nothing is stored.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: GuestHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The shared guest dependencies.
        """
        self._dependencies = dependencies

    async def __call__(self, command: IssueGuestChallenge) -> GuestChallengeGrant:
        """Issue a challenge at the configured difficulty.

        Args:
            command: The (empty) command.

        Returns:
            The signed challenge with its salt, difficulty and expiry.
        """
        del command
        dependencies = self._dependencies
        challenge = GuestChallenge(
            salt=dependencies.secrets.new_salt(),
            difficulty_bits=dependencies.limits.difficulty_bits,
            # Whole seconds: the signed token carries the expiry in Unix seconds,
            # so the grant states exactly the instant the token enforces.
            expires_at=(
                dependencies.clock.now() + dependencies.limits.challenge_ttl
            ).replace(microsecond=0),
        )
        return GuestChallengeGrant(
            challenge=dependencies.signer.sign(challenge),
            algorithm=PROOF_OF_WORK_ALGORITHM,
            salt=challenge.salt,
            difficulty_bits=challenge.difficulty_bits,
            expires_at=challenge.expires_at,
        )


class OpenGuestSubmissionHandler:
    """Redeem a solved challenge for one submission and its capability.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: GuestHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The shared guest dependencies.
        """
        self._dependencies = dependencies

    async def __call__(self, command: OpenGuestSubmission) -> GuestSubmissionGrant:
        """Verify the proof of work, then open the submission.

        Args:
            command: The signed challenge and the answer.

        Returns:
            The submission id, its capability (shown only now), expiry and photo
            limit.

        Raises:
            GuestChallengeInvalidError: If the challenge was not signed here.
            GuestChallengeExpiredError: If the challenge has expired.
            GuestProofInvalidError: If the nonce does not solve it.
            GuestSubmissionLimitError: If the hourly cap is reached.
            GuestChallengeSpentError: If the challenge opened a submission before.
        """
        dependencies = self._dependencies
        challenge = dependencies.signer.verify(command.challenge)
        if challenge is None:
            raise GuestChallengeInvalidError.create()
        now = dependencies.clock.now()
        if challenge.is_expired(now):
            raise GuestChallengeExpiredError.create()
        if not challenge.is_solved_by(command.nonce):
            raise GuestProofInvalidError.create()
        capability = dependencies.secrets.new_capability()
        async with dependencies.uow_factory() as uow:
            await uow.spent_challenges.purge_expired(now)
            window = await uow.guest_submissions.count_opened_since(now - CAP_WINDOW)
            if window.count >= dependencies.limits.submissions_per_hour:
                oldest = (
                    now if window.oldest_opened_at is None else window.oldest_opened_at
                )
                wait = oldest + CAP_WINDOW - now
                raise GuestSubmissionLimitError.retry_after(
                    int(wait.total_seconds()) + 1
                )
            await uow.spent_challenges.spend(challenge.salt, challenge.expires_at)
            submission = GuestSubmission.open(
                dependencies.ids.new_id(),
                capability,
                limits=dependencies.limits,
                clock=dependencies.clock,
            ).record_into(uow)
            await uow.guest_submissions.add(submission)
            await uow.commit()
        return GuestSubmissionGrant(
            submission_id=submission.id,
            capability=capability,
            expires_at=submission.expires_at,
            max_media=GUEST_MEDIA_MAX,
        )


class RequestGuestMediaUploadHandler:
    """Grant one photo upload to an open guest submission.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: GuestHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The shared guest dependencies.
        """
        self._dependencies = dependencies

    async def __call__(self, command: RequestGuestMediaUpload) -> UploadGrant:
        """Check the capability and the limit, then request the upload.

        Args:
            command: The submission, capability and image type.

        Returns:
            The media module's upload grant for the new asset.

        Raises:
            GuestCapabilityInvalidError: If the capability is missing or wrong.
            GuestCapabilityExpiredError: If it has expired.
            GuestSubmissionClosedError: If the report was already submitted.
            GuestMediaLimitError: If the submission has its three photos.
        """
        dependencies = self._dependencies
        async with dependencies.uow_factory() as uow:
            submission = await _authorise(
                uow, dependencies, command.submission_id, command.capability
            )
            _require_open(submission)
            if len(submission.media_ids) >= GUEST_MEDIA_MAX:
                raise GuestMediaLimitError.for_submission(
                    submission.id, GUEST_MEDIA_MAX
                )
        grant = await dependencies.media.request_upload(
            submission.id, command.mime_type
        )
        async with dependencies.uow_factory() as uow:
            # Reloaded: another request may have used a slot meanwhile; the
            # aggregate refuses a fourth photo whatever the first check saw.
            current = await _authorise(
                uow, dependencies, command.submission_id, command.capability
            )
            change = current.attach_media(grant.asset_id, clock=dependencies.clock)
            await uow.guest_submissions.save(change.record_into(uow))
            await uow.commit()
        return grant


class CompleteGuestMediaUploadHandler:
    """Complete a photo upload of an open guest submission.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: GuestHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The shared guest dependencies.
        """
        self._dependencies = dependencies

    async def __call__(self, command: CompleteGuestMediaUpload) -> GuestMediaAsset:
        """Check the capability, then complete the upload in the media module.

        Args:
            command: The submission, capability and asset.

        Returns:
            The completed photo.

        Raises:
            GuestCapabilityInvalidError: If the capability is missing or wrong.
            GuestCapabilityExpiredError: If it has expired.
            GuestSubmissionClosedError: If the report was already submitted.
            GuestMediaNotFoundError: If the asset was not granted to it.
        """
        dependencies = self._dependencies
        async with dependencies.uow_factory() as uow:
            submission = await _authorise(
                uow, dependencies, command.submission_id, command.capability
            )
        _require_open(submission)
        if command.asset_id not in submission.media_ids:
            raise GuestMediaNotFoundError.for_asset(command.asset_id)
        return await dependencies.media.complete_upload(submission.id, command.asset_id)


class SubmitGuestReportHandler:
    """Submit the one report of a guest submission (steps in the module docs).

    Implements: Command Handler.
    """

    def __init__(self, dependencies: GuestHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The shared guest dependencies.
        """
        self._dependencies = dependencies

    async def __call__(self, command: SubmitGuestReport) -> GuestReportReceipt:
        """Store the guest report, or return the receipt of the same one.

        Args:
            command: The submission, capability and report content.

        Returns:
            The receipt: reference and submission time.

        Raises:
            GuestCapabilityInvalidError: If the capability is missing or wrong.
            GuestCapabilityExpiredError: If it has expired.
            GuestSubmissionClosedError: If a different report was submitted.
            PermissionDeniedError: If the content attaches media this submission
                did not upload.
        """
        dependencies = self._dependencies
        fingerprint = content_fingerprint(command.content)
        async with dependencies.uow_factory() as uow:
            submission = await _authorise(
                uow, dependencies, command.submission_id, command.capability
            )
        if not submission.is_open:
            return self._replay(submission, fingerprint)
        await require_own_media(
            dependencies.media_checker, command.content, submission.id
        )
        source = await dependencies.source_registrar(
            RegisterPlatformSource(
                source_type=SourceType.CITIZEN,
                details=SourceDetails(
                    title=GUEST_SOURCE_TITLE, citation=GUEST_SOURCE_CITATION
                ),
            )
        )
        async with dependencies.uow_factory() as uow:
            current = await _authorise(
                uow, dependencies, command.submission_id, command.capability
            )
            if not current.is_open:
                return self._replay(current, fingerprint)
            report = (
                ReportFactory()
                .submitted(
                    dependencies.ids.new_id(),
                    ReportAttribution(
                        reporter_id=current.id,
                        source_id=source.id,
                        channel=ReportChannel.GUEST,
                    ),
                    command.content,
                    clock=dependencies.clock,
                    ids=dependencies.ids,
                )
                .record_into(uow)
            )
            await uow.reports.add(report)
            closed = current.record_report(
                report.id,
                await self._new_reference(uow),
                fingerprint,
                clock=dependencies.clock,
            ).record_into(uow)
            await uow.guest_submissions.save(closed)
            await uow.commit()
        await enqueue_triage(dependencies.task_queue, report.id)
        return _receipt(closed)

    @staticmethod
    def _replay(submission: GuestSubmission, fingerprint: str) -> GuestReportReceipt:
        # The same content again is a retry after a lost response; anything else
        # is a second report, which a submission never carries.
        if submission.content_fingerprint != fingerprint:
            raise GuestSubmissionClosedError.for_submission(submission.id)
        return _receipt(submission)

    async def _new_reference(self, uow: ReportsUnitOfWork) -> str:
        for _ in range(REFERENCE_ATTEMPTS):
            reference = self._dependencies.secrets.new_reference()
            if not await uow.guest_submissions.is_reference_taken(reference):
                return reference
        message = "no free guest reference could be drawn; retry"
        raise ConflictError(message, details={"reason": "reference_exhausted"})
