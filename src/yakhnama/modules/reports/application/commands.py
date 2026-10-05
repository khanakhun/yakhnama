"""Write requests accepted by the reports command handlers.

Commands acting on an existing report accept an optional ``expected_version``: the
API fills it from ``If-Match`` and the handler raises ``PreconditionFailedError``
(HTTP 412) when the stored version differs.

Patterns: Command.
"""

from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from yakhnama.modules.identity.public import Actor
from yakhnama.modules.media.public import MAX_MEDIA_BYTES
from yakhnama.modules.reports.domain.guest_submissions import ProofNonce
from yakhnama.modules.reports.domain.value_objects import (
    AssistedSubmission,
    ClientReportId,
    GuestImageType,
    ReportContent,
    ReportVersion,
    WithdrawalReason,
)
from yakhnama.shared_kernel.ids import EntityId

CHALLENGE_TEXT_MAX_LENGTH: Final = 512
CAPABILITY_TEXT_MAX_LENGTH: Final = 256

ChallengeText = Annotated[
    str, StringConstraints(min_length=1, max_length=CHALLENGE_TEXT_MAX_LENGTH)
]
"""A signed challenge as the client returns it; verified by ``GuestChallengeSigner``."""

CapabilityText = Annotated[
    str, StringConstraints(max_length=CAPABILITY_TEXT_MAX_LENGTH)
]
"""A capability as presented; only its digest is ever compared, never its format."""

PhotoByteSize = Annotated[int, Field(ge=1, le=MAX_MEDIA_BYTES)]
"""A guest photo's exact size in bytes, signed into its upload URL."""


class SubmitReport(BaseModel):
    """Submit a new report; idempotent on ``client_report_id``.

    Implements: Command.

    Attributes:
        actor: The reporter.
        client_report_id: The UUIDv7 the reporting client generated; it becomes the
            report's id, so a retried submission is recognised.
        content: What the reporter observed.
        organization_id: The organisation the reporter reports for, if any; the
            reporter must belong to it.
        assisted: Set when the actor enters the report for a person without an
            account, with that person's consent (ADR 0019).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    client_report_id: ClientReportId
    content: ReportContent
    organization_id: EntityId | None = None
    assisted: AssistedSubmission | None = None


class ReviseReport(BaseModel):
    """Correct a submitted report by submitting its next revision.

    Implements: Command.

    Attributes:
        actor: The reporter.
        report_id: The current revision being corrected.
        content: The complete corrected content.
        expected_version: The version the client last saw, from ``If-Match``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    report_id: EntityId
    content: ReportContent
    expected_version: ReportVersion | None = None


class WithdrawReport(BaseModel):
    """Take a report back; it stays on record with the reason.

    Implements: Command.

    Attributes:
        actor: The reporter.
        report_id: The report.
        reason: Why.
        expected_version: The version the client last saw, from ``If-Match``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    report_id: EntityId
    reason: WithdrawalReason
    expected_version: ReportVersion | None = None


class RunTriage(BaseModel):
    """Run the triage chain over one report and store its suggestions.

    Internal: enqueued as the ``reports.run_triage`` task after a submission or a
    revision; no endpoint accepts it and it carries no actor (see
    ``RunTriageHandler``).

    Implements: Command.

    Attributes:
        report_id: The report to triage.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: EntityId


class IssueGuestChallenge(BaseModel):
    """Issue a signed proof-of-work challenge to an anonymous caller (ADR 0020).

    Implements: Command.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class OpenGuestSubmission(BaseModel):
    """Redeem a solved challenge for one guest submission and its capability.

    Implements: Command.

    Attributes:
        challenge: The signed challenge, exactly as issued.
        nonce: The guest's answer to it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    challenge: ChallengeText
    nonce: ProofNonce


class RequestGuestMediaUpload(BaseModel):
    """Ask for a presigned upload of one photo for a guest submission.

    Implements: Command.

    Attributes:
        submission_id: The guest submission.
        capability: The capability the guest presented, or ``None``.
        mime_type: The declared image type.
        byte_size: The photo's exact size; storage refuses any other length.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    submission_id: EntityId
    capability: CapabilityText | None
    mime_type: GuestImageType
    byte_size: PhotoByteSize


class CompleteGuestMediaUpload(BaseModel):
    """Tell the platform a guest's photo was uploaded.

    Implements: Command.

    Attributes:
        submission_id: The guest submission.
        capability: The capability the guest presented, or ``None``.
        asset_id: The asset the upload was granted for.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    submission_id: EntityId
    capability: CapabilityText | None
    asset_id: EntityId


class SubmitGuestReport(BaseModel):
    """Submit the one report of a guest submission; a retry returns its receipt.

    Implements: Command.

    Attributes:
        submission_id: The guest submission.
        capability: The capability the guest presented, or ``None``.
        content: What the guest observed; ``media_ids`` must be photos granted
            to this submission.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    submission_id: EntityId
    capability: CapabilityText | None
    content: ReportContent


class PurgeGuestRecords(BaseModel):
    """Forget spent challenges and unfiled guest submissions past their retention.

    A system command, sent by the periodic ``reports.purge_guest_records`` task
    (ADR 0020, Q226).

    Implements: Command.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
