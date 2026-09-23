"""Read models returned by the hazards query services and load handlers.

DTOs are frozen and carry only values readers need. ``from_entity`` builds them from
the aggregate for in-memory implementations; the SQL query service builds them from
selected columns instead, with the same field meanings.

Patterns: DTO.
"""

from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from yakhnama.modules.hazards.domain.entities import HazardType
from yakhnama.modules.hazards.domain.value_objects import (
    HazardCode,
    HazardTypeStatus,
    IrdrAlignment,
    RetirementReason,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.value_objects import LocalizedText

SKIP_REASON_MAX_LENGTH = 500
LOAD_REPORT_MAX_ENTRIES = 10_000


class HazardTypeSummary(BaseModel):
    """One hazard type in a listing.

    Implements: DTO.

    Attributes:
        code: Stable hazard code.
        parent_code: Code of the broader type, or ``None`` for a root.
        labels: Display labels per language.
        status: ``active`` or ``retired``.
        attributes_schema: Registry code of the attribute schema, if any.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: HazardCode
    parent_code: HazardCode | None
    labels: LocalizedText
    status: HazardTypeStatus
    attributes_schema: HazardCode | None

    @classmethod
    def from_entity(cls, hazard_type: HazardType) -> Self:
        """Build the summary of a hazard type.

        Args:
            hazard_type: The aggregate.

        Returns:
            Its summary.
        """
        return cls(
            code=hazard_type.code,
            parent_code=hazard_type.parent_code,
            labels=hazard_type.labels,
            status=hazard_type.status,
            attributes_schema=hazard_type.attributes_schema,
        )


class HazardTypeDetail(BaseModel):
    """One hazard type with everything a reader may need.

    Implements: DTO.

    Attributes:
        id: Surrogate identifier (UUIDv7).
        code: Stable hazard code.
        parent_code: Code of the broader type, or ``None`` for a root.
        labels: Display labels per language.
        description: Longer explanation per language, if written.
        alignment: Placement in the IRDR 2014 peril classification.
        attributes_schema: Registry code of the attribute schema, if any.
        status: ``active`` or ``retired``.
        retirement: Why it was retired and what replaces it; ``None`` while active.
        version: Optimistic-concurrency version.
        created_at: When the type was created, UTC.
        updated_at: When it last changed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    code: HazardCode
    parent_code: HazardCode | None
    labels: LocalizedText
    description: LocalizedText | None
    alignment: IrdrAlignment
    attributes_schema: HazardCode | None
    status: HazardTypeStatus
    retirement: RetirementReason | None
    version: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @classmethod
    def from_entity(cls, hazard_type: HazardType) -> Self:
        """Build the detail view of a hazard type.

        Args:
            hazard_type: The aggregate.

        Returns:
            Its detail view.
        """
        return cls(
            id=hazard_type.id,
            code=hazard_type.code,
            parent_code=hazard_type.parent_code,
            labels=hazard_type.labels,
            description=hazard_type.description,
            alignment=hazard_type.alignment,
            attributes_schema=hazard_type.attributes_schema,
            status=hazard_type.status,
            retirement=hazard_type.retirement,
            version=hazard_type.version,
            created_at=hazard_type.created_at,
            updated_at=hazard_type.updated_at,
        )


class SkippedChange(BaseModel):
    """A difference between the reference file and the stored state left unapplied.

    Implements: DTO.

    Attributes:
        code: The hazard code the difference concerns.
        reason: Why the loader did not apply it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: HazardCode
    reason: str = Field(min_length=1, max_length=SKIP_REASON_MAX_LENGTH)


class LoadReport(BaseModel):
    """What loading the hazard taxonomy reference file did.

    Every code of the file appears in exactly one of ``created``, ``updated`` and
    ``unchanged``. ``skipped_with_reason`` lists differences the loader refused to
    apply; their codes also appear in ``updated`` or ``unchanged``.

    Implements: DTO.

    Attributes:
        data_version: The ``data_version`` of the loaded file.
        dry_run: Whether the changes were rolled back instead of committed.
        created: Codes created by this load, in creation order.
        updated: Codes changed by this load.
        unchanged: Codes already matching the file.
        skipped_with_reason: Differences left unapplied, with the reason.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    data_version: str = Field(min_length=1, max_length=32)
    dry_run: bool
    created: tuple[HazardCode, ...] = Field(max_length=LOAD_REPORT_MAX_ENTRIES)
    updated: tuple[HazardCode, ...] = Field(max_length=LOAD_REPORT_MAX_ENTRIES)
    unchanged: tuple[HazardCode, ...] = Field(max_length=LOAD_REPORT_MAX_ENTRIES)
    skipped_with_reason: tuple[SkippedChange, ...] = Field(
        max_length=LOAD_REPORT_MAX_ENTRIES
    )

    @property
    def is_unchanged(self) -> bool:
        """Return ``True`` if the load created and updated nothing."""
        return not self.created and not self.updated
