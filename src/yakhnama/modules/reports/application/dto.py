"""Read models of the reports module.

``ReportRecord`` is the **internal** read model the query service returns: it holds
the reporter's exact position and every lifecycle field, and it never leaves the
application layer as it is. The API returns only ``ReportSummary`` and
``ReportDetail``, which the application builds from a record with the privacy rules
applied (Phase 3 plan §2):

- a summary always shows the position rounded by ``PublicCoordinatePolicy`` and
  never the GPS accuracy, because an accuracy radius next to a rounded point helps
  recover the exact one;
- a detail shows the exact position and accuracy only when built without a policy
  (for the reporter and moderators) and says so in ``coordinates_are_exact``; built
  with a policy it is rounded and hides the accuracy like a summary;
- triage flags appear in a detail only when the caller asks for them (moderators);
- an assisted report's consent record and private note follow the exact position:
  only the person who entered it and moderators see them (ADR 0019);
- a guest report never shows a reporter: its ``reporter_id`` is the guest
  submission, an internal access record (ADR 0020).

The guest DTOs at the end are what the guest submission use cases return.

Patterns: DTO.
"""

from typing import Final, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from yakhnama.modules.media.public import UploadStatus
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.guest_submissions import (
    DifficultyBits,
    GuestReference,
)
from yakhnama.modules.reports.domain.value_objects import (
    MEDIA_PER_REPORT_MAX,
    AssistedSubmission,
    Description,
    GpsAccuracy,
    GuessedHazardCode,
    GuestImageType,
    HazardGuess,
    ObservationPoint,
    PlaceHint,
    ReportChannel,
    ReportStatus,
    ReportVersion,
    RevisionNumber,
    TriageResult,
    WithdrawalReason,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.privacy import PublicCoordinatePolicy
from yakhnama.shared_kernel.value_objects import (
    Coordinates,
    DateWithPrecision,
    LanguageCode,
)


class ReportRecord(BaseModel):
    """One report as the read side stores it, exact position included.

    Internal to the reports module and the adapters that implement its ports; never
    returned to an API client.

    Implements: DTO.

    Attributes:
        id: The report's id.
        reporter_id: The reporting user.
        organization_id: The organisation reported for, or ``None``.
        source_id: The provenance source.
        observed_at: When it was observed.
        observation: The exact position and accuracy.
        description: The reporter's words.
        original_language: The language of ``description``.
        hazard_guess: The reporter's guess, if any.
        place_hint: The picked place, if any.
        media_ids: Attached media assets.
        status: Lifecycle status.
        revision: Position in the revision chain.
        supersedes_id: The revision this one replaces, if any.
        superseded_by_id: The revision that replaced this one, if any.
        withdrawal_reason: Why it was withdrawn, if it was.
        triage: The latest triage result, if any.
        submitted_at: When it was submitted, UTC.
        version: Optimistic-concurrency version.
        created_at: When the record was created, UTC.
        channel: How the report reached the platform.
        assisted: The assistance and consent record of an assisted report.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    reporter_id: EntityId
    organization_id: EntityId | None
    source_id: EntityId
    observed_at: DateWithPrecision
    observation: ObservationPoint
    description: Description
    original_language: LanguageCode
    hazard_guess: HazardGuess | None
    place_hint: PlaceHint | None
    media_ids: tuple[EntityId, ...] = Field(max_length=MEDIA_PER_REPORT_MAX)
    status: ReportStatus
    revision: RevisionNumber
    supersedes_id: EntityId | None
    superseded_by_id: EntityId | None
    withdrawal_reason: WithdrawalReason | None
    triage: TriageResult | None
    submitted_at: AwareDatetime | None
    version: ReportVersion
    created_at: AwareDatetime
    channel: ReportChannel = ReportChannel.ACCOUNT
    assisted: AssistedSubmission | None = None

    @classmethod
    def from_entity(cls, report: Report) -> Self:
        """Build the record of a report.

        Args:
            report: The aggregate.

        Returns:
            Its record.
        """
        return cls(
            id=report.id,
            reporter_id=report.reporter_id,
            organization_id=report.organization_id,
            source_id=report.source_id,
            observed_at=report.observed_at,
            observation=report.observation,
            description=report.description,
            original_language=report.original_language,
            hazard_guess=report.hazard_guess,
            place_hint=report.place_hint,
            media_ids=report.media_ids,
            status=report.status,
            revision=report.revision,
            supersedes_id=report.supersedes_id,
            superseded_by_id=report.superseded_by_id,
            withdrawal_reason=report.withdrawal_reason,
            triage=report.triage,
            submitted_at=report.submitted_at,
            version=report.version,
            created_at=report.created_at,
            channel=report.channel,
            assisted=report.assisted,
        )


class ReportSummary(BaseModel):
    """One report in a listing, with its position rounded.

    Implements: DTO.

    Attributes:
        id: The report's id.
        status: Lifecycle status.
        revision: Position in the revision chain.
        observed_at: When it was observed.
        coordinates: The position rounded by ``PublicCoordinatePolicy``.
        hazard_code: The reporter's hazard guess, if any.
        place_hint: The picked place, if any.
        media_count: How many media assets are attached.
        submitted_at: When it was submitted, UTC.
        channel: How the report reached the platform.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    status: ReportStatus
    revision: RevisionNumber
    observed_at: DateWithPrecision
    coordinates: Coordinates
    hazard_code: GuessedHazardCode | None
    place_hint: PlaceHint | None
    media_count: int = Field(ge=0, le=MEDIA_PER_REPORT_MAX)
    submitted_at: AwareDatetime | None
    channel: ReportChannel

    @classmethod
    def from_record(
        cls, record: ReportRecord, public_coordinates: PublicCoordinatePolicy
    ) -> Self:
        """Build the summary of a report, rounding its position.

        Args:
            record: The internal record.
            public_coordinates: How far to round the position.

        Returns:
            Its summary.
        """
        return cls(
            id=record.id,
            status=record.status,
            revision=record.revision,
            observed_at=record.observed_at,
            coordinates=public_coordinates.apply(record.observation.coordinates),
            hazard_code=(
                None if record.hazard_guess is None else record.hazard_guess.hazard_code
            ),
            place_hint=record.place_hint,
            media_count=len(record.media_ids),
            submitted_at=record.submitted_at,
            channel=record.channel,
        )


class ReportDetail(BaseModel):
    """One report with its content, exact or rounded as the caller may see it.

    Implements: DTO.

    Attributes:
        id: The report's id.
        reporter_id: The reporting user; ``None`` for a guest report, which never
            shows a reporter.
        organization_id: The organisation reported for, or ``None``.
        source_id: The provenance source.
        status: Lifecycle status.
        revision: Position in the revision chain.
        supersedes_id: The revision this one replaces, if any.
        superseded_by_id: The revision that replaced this one, if any.
        observed_at: When it was observed.
        coordinates: The exact position, or the rounded one.
        accuracy: The GPS accuracy, only alongside exact coordinates.
        coordinates_are_exact: Whether ``coordinates`` is the exact position.
        description: The reporter's words.
        original_language: The language of ``description``.
        hazard_guess: The reporter's guess, if any.
        place_hint: The picked place, if any.
        media_ids: Attached media assets.
        withdrawal_reason: Why it was withdrawn, if it was.
        triage: The latest triage result, only for callers who may see it.
        submitted_at: When it was submitted, UTC.
        version: Optimistic-concurrency version, for ``ETag``.
        channel: How the report reached the platform.
        assisted: The consent record and private note of an assisted report,
            only alongside exact coordinates (the person who entered it and
            moderators); ``None`` otherwise.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    reporter_id: EntityId | None
    organization_id: EntityId | None
    source_id: EntityId
    status: ReportStatus
    revision: RevisionNumber
    supersedes_id: EntityId | None
    superseded_by_id: EntityId | None
    observed_at: DateWithPrecision
    coordinates: Coordinates
    accuracy: GpsAccuracy | None
    coordinates_are_exact: bool
    description: Description
    original_language: LanguageCode
    hazard_guess: HazardGuess | None
    place_hint: PlaceHint | None
    media_ids: tuple[EntityId, ...] = Field(max_length=MEDIA_PER_REPORT_MAX)
    withdrawal_reason: WithdrawalReason | None
    triage: TriageResult | None
    submitted_at: AwareDatetime | None
    version: ReportVersion
    channel: ReportChannel
    assisted: AssistedSubmission | None

    @classmethod
    def from_record(
        cls,
        record: ReportRecord,
        *,
        public_coordinates: PublicCoordinatePolicy | None,
        is_triage_visible: bool,
    ) -> Self:
        """Build the detail view of a report.

        Args:
            record: The internal record.
            public_coordinates: ``None`` to show the exact position, accuracy and
                assistance record; otherwise the policy that rounds the position
                (accuracy and assistance record hidden).
            is_triage_visible: Whether to include the triage result.

        Returns:
            Its detail view.
        """
        observation = record.observation
        is_exact = public_coordinates is None
        return cls(
            id=record.id,
            reporter_id=(
                None if record.channel is ReportChannel.GUEST else record.reporter_id
            ),
            organization_id=record.organization_id,
            source_id=record.source_id,
            status=record.status,
            revision=record.revision,
            supersedes_id=record.supersedes_id,
            superseded_by_id=record.superseded_by_id,
            observed_at=record.observed_at,
            coordinates=(
                observation.coordinates
                if public_coordinates is None
                else public_coordinates.apply(observation.coordinates)
            ),
            accuracy=observation.accuracy if is_exact else None,
            coordinates_are_exact=is_exact,
            description=record.description,
            original_language=record.original_language,
            hazard_guess=record.hazard_guess,
            place_hint=record.place_hint,
            media_ids=record.media_ids,
            withdrawal_reason=record.withdrawal_reason,
            triage=record.triage if is_triage_visible else None,
            submitted_at=record.submitted_at,
            version=record.version,
            channel=record.channel,
            assisted=record.assisted if is_exact else None,
        )

    @classmethod
    def for_reporter(cls, report: Report) -> Self:
        """Build the reporter's own, exact view of a report they just changed.

        Args:
            report: The aggregate.

        Returns:
            The exact detail view without triage.
        """
        return cls.from_record(
            ReportRecord.from_entity(report),
            public_coordinates=None,
            is_triage_visible=False,
        )


GUEST_MEDIA_RESPONSE_MAX: Final = 3


class GuestChallengeGrant(BaseModel):
    """A proof-of-work challenge as the guest receives it (ADR 0020).

    Implements: DTO.

    Attributes:
        challenge: The opaque, signed challenge to send back with the answer.
        algorithm: The hash to use, always ``SHA-256``.
        salt: The text the answer is appended to before hashing.
        difficulty_bits: Leading zero bits ``SHA-256(salt + nonce)`` needs.
        expires_at: When the challenge stops working, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    challenge: str = Field(min_length=1, max_length=512)
    algorithm: Literal["SHA-256"]
    salt: str = Field(min_length=16, max_length=64)
    difficulty_bits: DifficultyBits
    expires_at: AwareDatetime


class GuestSubmissionGrant(BaseModel):
    """An open guest submission and its capability, returned exactly once.

    Implements: DTO.

    Attributes:
        submission_id: The submission; part of every later guest URL.
        capability: The secret to send as ``Guest-Capability``; only its digest
            is stored, so it cannot be shown again.
        expires_at: When the capability stops working, UTC.
        max_media: How many photos the submission may upload.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    submission_id: EntityId
    capability: str = Field(min_length=43, max_length=128)
    expires_at: AwareDatetime
    max_media: int = Field(ge=0, le=GUEST_MEDIA_RESPONSE_MAX)


class GuestMediaAsset(BaseModel):
    """A guest's photo after its upload was completed; no links, no keys, no EXIF.

    Implements: DTO.

    Attributes:
        id: The asset.
        mime_type: The image type detected from the file.
        byte_size: The file's size, once completed.
        upload_status: Whether the file arrived.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    mime_type: GuestImageType
    byte_size: int | None = Field(ge=0)
    upload_status: UploadStatus


class GuestReportReceipt(BaseModel):
    """What a guest keeps after submitting: a reference to quote, and when.

    Implements: DTO.

    Attributes:
        reference: The receipt code, such as ``YK-7KQM-3HXA``.
        submitted_at: When the report was submitted, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference: GuestReference
    submitted_at: AwareDatetime


class GuestSubmissionWindow(BaseModel):
    """How many guest submissions were opened since an instant, and the oldest.

    Implements: DTO.

    Attributes:
        count: Submissions opened since the instant.
        oldest_opened_at: When the oldest of them was opened, or ``None``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    count: int = Field(ge=0)
    oldest_opened_at: AwareDatetime | None = None
