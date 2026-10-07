"""Use cases of guest reporting: a report from a person without an account (ADR 0020).

The guest drives five steps, each its own handler:

1. ``IssueGuestChallengeHandler`` signs a proof-of-work challenge whose difficulty
   rises with the submissions opened in the last hour; nothing is stored.
2. ``OpenGuestSubmissionHandler`` checks the signature, the expiry and the answer,
   checks both hourly caps, spends the challenge and opens a submission with a
   fresh capability, in one unit of work, so a challenge opens at most one
   submission even when two requests race.
3. ``RequestGuestMediaUploadHandler`` first **reserves a photo slot** for a new
   asset id (a version-checked save, retried when parallel uploads of the same
   guest collide), then has the media module create exactly that asset and
   presign its upload. A fourth slot is refused before any asset or source
   exists; a failed grant gives its slot back.
4. ``CompleteGuestMediaUploadHandler`` completes a granted photo, also after the
   report was submitted, until the capability expires.
5. ``SubmitGuestReportHandler`` first **reserves the report**: under the reports
   cap's lock it records the content fingerprint and the id the report's source
   will have (a version-checked save, so of two concurrent requests exactly one
   reserves). Only then is the platform source registered (idempotently, under
   the reserved id), and the report stored and the submission filed in one unit
   of work; the source is marked referenced after that commit, and triage is
   enqueued as for every report. A retry of the same content, or a request that
   lost a race, reloads and returns the same receipt, also up to
   ``GuestSubmissionLimits.receipt_grace`` after the capability expired.

Steps 3 to 5 first check the capability: an unknown submission, a missing or wrong
capability are one and the same error, so submission ids cannot be probed; an
expired capability has its own error, so the client can tell the guest to start
again.

The two hourly caps (``GuestCap``) are counted under a transaction-scoped database
lock per cap (``GuestSubmissionRepository.lock_cap``), so concurrent requests cannot
overshoot them together. ``PurgeGuestRecordsHandler`` (a periodic system task)
forgets spent challenges and unfiled submissions past their retention.

Patterns: Command Handler, Unit of Work, Dependency Injection.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Final

from yakhnama.modules.media.public import UploadGrant
from yakhnama.modules.provenance.public import (
    MarkPlatformSourceReferenced,
    PlatformSourceReferenceMarker,
    PlatformSourceRegistrar,
    RegisterPlatformSource,
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
    GuestMediaAsset,
    GuestPurgeOutcome,
    GuestReportReceipt,
    GuestSubmissionGrant,
    GuestSubmissionWindow,
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
from yakhnama.modules.reports.domain.factories import ReportFactory
from yakhnama.modules.reports.domain.guest_submissions import (
    GUEST_MEDIA_MAX,
    PROOF_OF_WORK_ALGORITHM,
    GuestCap,
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
"""The rolling window of both hourly caps (``GuestCap``)."""

CHALLENGE_PURGE_GRACE: Final = timedelta(minutes=5)
"""How long a spent challenge is kept after it expired, by the database's clock.

A redemption that began just before the expiry may still be running; the margin
keeps its record until it has certainly finished.
"""

SAVE_RETRIES: Final = GUEST_MEDIA_MAX
"""Extra attempts of a version-checked change that lost a race.

Enough for every photo of one guest uploading in parallel; each attempt reloads.
"""

REFERENCE_ATTEMPTS: Final = 5
"""Fresh references drawn before giving up; one collision is already rare."""

# Conflicts the domain decided; a retry would decide the same, so they propagate.
_DECIDED_CONFLICTS: Final = (
    GuestMediaLimitError,
    GuestSubmissionClosedError,
    GuestChallengeSpentError,
)


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
        source_marker: Freezes that source once the report is stored.
        task_queue: Schedules triage of the guest report.
        limits: Difficulty, lifetimes and the hourly caps, from settings.
        clock: Source of every timestamp.
        ids: Source of submission, asset, source, report and event ids.
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
        source_marker: PlatformSourceReferenceMarker,
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
            source_marker: Freezes that source once the report is stored.
            task_queue: Schedules triage of the guest report.
            limits: Difficulty, lifetimes and the hourly caps.
            clock: Source of every timestamp.
            ids: Source of submission, asset, source, report and event ids.
        """
        self.uow_factory = uow_factory
        self.signer = signer
        self.secrets = secrets
        self.media = media
        self.media_checker = media_checker
        self.source_registrar = source_registrar
        self.source_marker = source_marker
        self.task_queue = task_queue
        self.limits = limits
        self.clock = clock
        self.ids = ids


async def _run_step[ResultT](
    dependencies: GuestHandlerDependencies,
    step: Callable[[ReportsUnitOfWork], Awaitable[ResultT]],
) -> ResultT:
    async with dependencies.uow_factory() as uow:
        result = await step(uow)
        await uow.commit()
    return result


async def _run_with_retries[ResultT](
    dependencies: GuestHandlerDependencies,
    step: Callable[[ReportsUnitOfWork], Awaitable[ResultT]],
) -> ResultT:
    # A version conflict means another request changed the submission between
    # this step's read and its write; the step reloads and decides again.
    for _ in range(SAVE_RETRIES):
        try:
            return await _run_step(dependencies, step)
        except _DECIDED_CONFLICTS:
            raise
        except ConflictError:
            continue
    return await _run_step(dependencies, step)


async def _load_with_capability(
    uow: ReportsUnitOfWork, submission_id: EntityId, capability: str | None
) -> GuestSubmission:
    submission = await uow.guest_submissions.get(submission_id)
    if (
        submission is None
        or capability is None
        or not submission.is_capability(capability)
    ):
        raise GuestCapabilityInvalidError.create()
    return submission


def _require_unexpired(submission: GuestSubmission, now: datetime) -> None:
    if submission.is_expired(now):
        raise GuestCapabilityExpiredError.for_submission(submission.id)


async def _authorise(
    uow: ReportsUnitOfWork,
    dependencies: GuestHandlerDependencies,
    submission_id: EntityId,
    capability: str | None,
) -> GuestSubmission:
    submission = await _load_with_capability(uow, submission_id, capability)
    _require_unexpired(submission, dependencies.clock.now())
    return submission


async def _require_below_cap(
    uow: ReportsUnitOfWork,
    dependencies: GuestHandlerDependencies,
    cap: GuestCap,
    now: datetime,
) -> None:
    since = now - CAP_WINDOW
    repository = uow.guest_submissions
    window: GuestSubmissionWindow = await (
        repository.count_opened_since(since)
        if cap is GuestCap.OPENED
        else repository.count_submitted_since(since)
    )
    if window.count < dependencies.limits.cap_of(cap):
        return
    oldest = now if window.oldest_at is None else window.oldest_at
    wait = oldest + CAP_WINDOW - now
    raise GuestSubmissionLimitError.retry_after(int(wait.total_seconds()) + 1)


def _receipt(submission: GuestSubmission) -> GuestReportReceipt:
    # Only a filed submission has a receipt; the invariant sets both together.
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
        """Issue a challenge at the difficulty the last hour calls for.

        Args:
            command: The (empty) command.

        Returns:
            The signed challenge with its salt, difficulty and expiry.
        """
        del command
        dependencies = self._dependencies
        now = dependencies.clock.now()
        async with dependencies.uow_factory() as uow:
            opened = await uow.guest_submissions.count_opened_since(now - CAP_WINDOW)
        challenge = GuestChallenge(
            salt=dependencies.secrets.new_salt(),
            difficulty_bits=dependencies.limits.difficulty_for(opened.count),
            # Whole seconds: the signed token carries the expiry in Unix seconds,
            # so the grant states exactly the instant the token enforces.
            expires_at=(now + dependencies.limits.challenge_ttl).replace(microsecond=0),
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

        The reports cap is checked here as well, without its lock: when guests
        may not file any more reports this hour, nobody is asked to write one.

        Args:
            command: The signed challenge and the answer.

        Returns:
            The submission id, its capability (shown only now), expiry and photo
            limit.

        Raises:
            GuestChallengeInvalidError: If the challenge was not signed here.
            GuestChallengeExpiredError: If the challenge has expired, by the
                application's or the database's clock.
            GuestProofInvalidError: If the nonce does not solve it.
            GuestSubmissionLimitError: If an hourly cap is reached.
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
            await uow.guest_submissions.lock_cap(GuestCap.OPENED)
            await _require_below_cap(uow, dependencies, GuestCap.OPENED, now)
            await _require_below_cap(uow, dependencies, GuestCap.REPORTS, now)
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
    """Reserve a photo slot of an open guest submission, then grant its upload.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: GuestHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The shared guest dependencies.
        """
        self._dependencies = dependencies

    async def __call__(self, command: RequestGuestMediaUpload) -> UploadGrant:
        """Reserve the slot, then have the media module create the asset.

        Args:
            command: The submission, capability, image type and size.

        Returns:
            The media module's upload grant for the new asset.

        Raises:
            GuestCapabilityInvalidError: If the capability is missing or wrong.
            GuestCapabilityExpiredError: If it has expired.
            GuestSubmissionClosedError: If the report was already submitted.
            GuestMediaLimitError: If the submission has its three photos.
        """
        dependencies = self._dependencies
        asset_id = dependencies.ids.new_id()

        async def reserve(uow: ReportsUnitOfWork) -> GuestSubmission:
            submission = await _authorise(
                uow, dependencies, command.submission_id, command.capability
            )
            change = submission.attach_media(asset_id, clock=dependencies.clock)
            reserved = change.record_into(uow)
            await uow.guest_submissions.save(reserved)
            return reserved

        submission = await _run_with_retries(dependencies, reserve)
        try:
            return await dependencies.media.request_upload(
                submission.id,
                command.mime_type,
                asset_id=asset_id,
                byte_size=command.byte_size,
            )
        except Exception:
            # The slot names an asset that may not exist; give it back so a
            # storage hiccup does not cost the guest a photo. If the asset was
            # created before the failure, the stale-upload sweep fails it later.
            await self._release(submission.id, asset_id)
            raise

    async def _release(self, submission_id: EntityId, asset_id: EntityId) -> None:
        dependencies = self._dependencies

        async def release(uow: ReportsUnitOfWork) -> None:
            submission = await uow.guest_submissions.get(submission_id)
            if submission is None:
                return
            change = submission.detach_media(asset_id, clock=dependencies.clock)
            if change.state is not submission:
                await uow.guest_submissions.save(change.record_into(uow))

        await _run_with_retries(dependencies, release)


class CompleteGuestMediaUploadHandler:
    """Complete a photo upload granted to a guest submission.

    A photo whose upload finishes just after the report was submitted can still
    be completed until the capability expires, so an honest late completion is
    not left behind. It is not added to the report: a report's photos are the ones
    its content listed when it was submitted.

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
            GuestMediaNotFoundError: If the asset was not granted to it.
        """
        dependencies = self._dependencies
        async with dependencies.uow_factory() as uow:
            submission = await _authorise(
                uow, dependencies, command.submission_id, command.capability
            )
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
            GuestCapabilityExpiredError: If it has expired, and this is not a
                retry of the submitted report within the receipt grace.
            GuestSubmissionClosedError: If a different report was submitted.
            GuestSubmissionLimitError: If the hourly reports cap is reached.
            PermissionDeniedError: If the content attaches media this submission
                did not upload.
        """
        dependencies = self._dependencies
        fingerprint = content_fingerprint(command.content)

        async def reserve(uow: ReportsUnitOfWork) -> GuestSubmission:
            return await self._reserve(uow, command, fingerprint)

        reserved = await _run_with_retries(dependencies, reserve)
        if reserved.is_filed:
            return await self._replay(reserved)
        await dependencies.source_registrar(
            RegisterPlatformSource(
                source_type=SourceType.CITIZEN,
                details=SourceDetails(
                    title=GUEST_SOURCE_TITLE, citation=GUEST_SOURCE_CITATION
                ),
                source_id=reserved.source_id,
            )
        )

        async def file(uow: ReportsUnitOfWork) -> tuple[GuestSubmission, Report | None]:
            return await self._file(uow, reserved.id, command.content)

        try:
            filed, report = await _run_step(dependencies, file)
        except ConflictError:
            # Another request filed the same reservation first; its receipt is
            # this request's receipt.
            winner = await self._reload(reserved.id)
            if winner is None or not winner.is_filed:
                raise
            filed, report = winner, None
        if report is None:
            return await self._replay(filed)
        await self._mark_source(filed)
        await enqueue_triage(dependencies.task_queue, report.id)
        return _receipt(filed)

    async def _reserve(
        self, uow: ReportsUnitOfWork, command: SubmitGuestReport, fingerprint: str
    ) -> GuestSubmission:
        dependencies = self._dependencies
        submission = await _load_with_capability(
            uow, command.submission_id, command.capability
        )
        now = dependencies.clock.now()
        if not submission.is_open:
            # Replay wins over expiry: the same content again is a retry after a
            # lost response, answered for a grace period after the capability
            # expired; anything else is a second report, never accepted.
            if not submission.is_retry_of(fingerprint):
                raise GuestSubmissionClosedError.for_submission(submission.id)
            if not submission.is_within_receipt_grace(
                now, dependencies.limits.receipt_grace
            ):
                raise GuestCapabilityExpiredError.for_submission(submission.id)
            return submission
        _require_unexpired(submission, now)
        await require_own_media(
            dependencies.media_checker, command.content, submission.id
        )
        await uow.guest_submissions.lock_cap(GuestCap.REPORTS)
        await _require_below_cap(uow, dependencies, GuestCap.REPORTS, now)
        reserved = submission.reserve_report(
            fingerprint, dependencies.ids.new_id(), clock=dependencies.clock
        ).record_into(uow)
        await uow.guest_submissions.save(reserved)
        return reserved

    async def _file(
        self, uow: ReportsUnitOfWork, submission_id: EntityId, content: ReportContent
    ) -> tuple[GuestSubmission, Report | None]:
        dependencies = self._dependencies
        current = await uow.guest_submissions.get(submission_id)
        if current is None or current.source_id is None:
            # Purged or never reserved: nothing this request may still file.
            raise GuestSubmissionClosedError.for_submission(submission_id)
        if current.is_filed:
            return current, None
        report = (
            ReportFactory()
            .submitted(
                dependencies.ids.new_id(),
                ReportAttribution(
                    reporter_id=current.id,
                    source_id=current.source_id,
                    channel=ReportChannel.GUEST,
                ),
                content,
                clock=dependencies.clock,
                ids=dependencies.ids,
            )
            .record_into(uow)
        )
        await uow.reports.add(report)
        filed = current.record_report(
            report.id, await self._new_reference(uow), clock=dependencies.clock
        ).record_into(uow)
        await uow.guest_submissions.save(filed)
        return filed, report

    async def _reload(self, submission_id: EntityId) -> GuestSubmission | None:
        async with self._dependencies.uow_factory() as uow:
            return await uow.guest_submissions.get(submission_id)

    async def _replay(self, filed: GuestSubmission) -> GuestReportReceipt:
        # Marking again is idempotent and repairs a filing whose request died
        # between its commit and the mark.
        await self._mark_source(filed)
        return _receipt(filed)

    async def _mark_source(self, filed: GuestSubmission) -> None:
        if filed.source_id is not None:
            await self._dependencies.source_marker(
                MarkPlatformSourceReferenced(source_id=filed.source_id)
            )

    async def _new_reference(self, uow: ReportsUnitOfWork) -> str:
        for _ in range(REFERENCE_ATTEMPTS):
            reference = self._dependencies.secrets.new_reference()
            if not await uow.guest_submissions.is_reference_taken(reference):
                return reference
        message = "no free guest reference could be drawn; retry"
        raise ConflictError(message, details={"reason": "reference_exhausted"})


class PurgeGuestRecordsHandler:
    """Forget what guest reporting no longer needs (ADR 0020, Q226).

    - Spent challenges ``CHALLENGE_PURGE_GRACE`` after they expired, by the
      database's clock: by then they could open nothing even if replayed.
    - Submissions that never filed a report, ``receipt_grace`` after their
      capability expired. Filed submissions are kept: the report names the
      submission as its reporter, and the reference is the guest's only handle.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: GuestHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The shared guest dependencies.
        """
        self._dependencies = dependencies

    async def __call__(self, command: PurgeGuestRecords) -> GuestPurgeOutcome:
        """Purge both kinds of record in one unit of work.

        Args:
            command: The (empty) command.

        Returns:
            How many challenges and submissions were forgotten.
        """
        del command
        dependencies = self._dependencies
        cutoff = dependencies.clock.now() - dependencies.limits.receipt_grace
        async with dependencies.uow_factory() as uow:
            challenges = await uow.spent_challenges.purge_expired(CHALLENGE_PURGE_GRACE)
            submissions = await uow.guest_submissions.purge_unfiled(cutoff)
            await uow.commit()
        return GuestPurgeOutcome(challenges=challenges, submissions=submissions)
