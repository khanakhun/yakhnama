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
- triage flags appear in a detail only when the caller asks for them (moderators).

Patterns: DTO.
"""

from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.value_objects import (
    MEDIA_PER_REPORT_MAX,
    Description,
    GpsAccuracy,
    GuessedHazardCode,
    HazardGuess,
    ObservationPoint,
    PlaceHint,
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
        )


class ReportDetail(BaseModel):
    """One report with its content, exact or rounded as the caller may see it.

    Implements: DTO.

    Attributes:
        id: The report's id.
        reporter_id: The reporting user.
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
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    reporter_id: EntityId
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
            public_coordinates: ``None`` to show the exact position and accuracy;
                otherwise the policy that rounds the position (accuracy hidden).
            is_triage_visible: Whether to include the triage result.

        Returns:
            Its detail view.
        """
        observation = record.observation
        is_exact = public_coordinates is None
        return cls(
            id=record.id,
            reporter_id=record.reporter_id,
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
