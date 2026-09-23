"""Read models returned by the impacts query services and load handlers.

DTOs are frozen and carry only values readers need. ``from_entity`` builds them from
the aggregate for in-memory implementations; the SQL query service builds them from
selected columns instead, with the same field meanings.

Patterns: DTO.
"""

from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.value_objects import (
    Aggregation,
    CurrencyCode,
    DesInventarField,
    MetricCategory,
    MetricCode,
    MetricStatus,
    RetirementReason,
    SendaiIndicator,
    ValueKind,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.value_objects import LocalizedText, Unit

SKIP_REASON_MAX_LENGTH = 500
LOAD_REPORT_MAX_ENTRIES = 10_000


class ImpactMetricSummary(BaseModel):
    """One impact metric in a listing.

    Implements: DTO.

    Attributes:
        code: Stable metric code.
        labels: Display names per language.
        category: The metric's group.
        value_kind: Count, SI measurement or money.
        unit: Unit of the values, ``None`` for money.
        currency: ISO 4217 code for monetary metrics.
        status: ``active`` or ``retired``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: MetricCode
    labels: LocalizedText
    category: MetricCategory
    value_kind: ValueKind
    unit: Unit | None
    currency: CurrencyCode | None
    status: MetricStatus

    @classmethod
    def from_entity(cls, metric: ImpactMetric) -> Self:
        """Build the summary of a metric.

        Args:
            metric: The aggregate.

        Returns:
            Its summary.
        """
        return cls(
            code=metric.code,
            labels=metric.labels,
            category=metric.category,
            value_kind=metric.value_kind,
            unit=metric.unit,
            currency=metric.currency,
            status=metric.status,
        )


class ImpactMetricDetail(BaseModel):
    """One impact metric with its full definition.

    Implements: DTO.

    Attributes:
        id: Database identity (UUIDv7).
        code: Stable metric code.
        labels: Display names per language.
        description: Exact meaning, if written.
        category: The metric's group.
        value_kind: Count, SI measurement or money.
        unit: Unit of the values, ``None`` for money.
        currency: ISO 4217 code for monetary metrics.
        sendai: Proposed Sendai indicator, if any.
        desinventar: Proposed DesInventar effect field, if any.
        aggregation: How the best-figure policy combines claims.
        status: ``active`` or ``retired``.
        retirement: Why it was retired and what replaces it; ``None`` while active.
        version: Optimistic-concurrency version.
        created_at: When the metric was created, UTC.
        updated_at: When it last changed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    code: MetricCode
    labels: LocalizedText
    description: LocalizedText | None
    category: MetricCategory
    value_kind: ValueKind
    unit: Unit | None
    currency: CurrencyCode | None
    sendai: SendaiIndicator | None
    desinventar: DesInventarField | None
    aggregation: Aggregation
    status: MetricStatus
    retirement: RetirementReason | None
    version: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @classmethod
    def from_entity(cls, metric: ImpactMetric) -> Self:
        """Build the detail view of a metric.

        Args:
            metric: The aggregate.

        Returns:
            Its detail view.
        """
        return cls(
            id=metric.id,
            code=metric.code,
            labels=metric.labels,
            description=metric.description,
            category=metric.category,
            value_kind=metric.value_kind,
            unit=metric.unit,
            currency=metric.currency,
            sendai=metric.sendai,
            desinventar=metric.desinventar,
            aggregation=metric.aggregation,
            status=metric.status,
            retirement=metric.retirement,
            version=metric.version,
            created_at=metric.created_at,
            updated_at=metric.updated_at,
        )


class SkippedChange(BaseModel):
    """A difference between the reference file and the stored state left unapplied.

    Implements: DTO.

    Attributes:
        code: The metric code the difference concerns.
        reason: Why the loader did not apply it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: MetricCode
    reason: str = Field(min_length=1, max_length=SKIP_REASON_MAX_LENGTH)


class LoadReport(BaseModel):
    """What loading the impact metric reference file did.

    Every code of the file appears in exactly one of ``created``, ``updated`` and
    ``unchanged``. ``skipped_with_reason`` lists differences the loader refused to
    apply; their codes also appear in ``updated`` or ``unchanged``.

    Implements: DTO.

    Attributes:
        data_version: The ``data_version`` of the loaded file.
        dry_run: Whether the changes were rolled back instead of committed.
        created: Codes created by this load, in file order.
        updated: Codes changed by this load.
        unchanged: Codes already matching the file.
        skipped_with_reason: Differences left unapplied, with the reason.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    data_version: str = Field(min_length=1, max_length=32)
    dry_run: bool
    created: tuple[MetricCode, ...] = Field(max_length=LOAD_REPORT_MAX_ENTRIES)
    updated: tuple[MetricCode, ...] = Field(max_length=LOAD_REPORT_MAX_ENTRIES)
    unchanged: tuple[MetricCode, ...] = Field(max_length=LOAD_REPORT_MAX_ENTRIES)
    skipped_with_reason: tuple[SkippedChange, ...] = Field(
        max_length=LOAD_REPORT_MAX_ENTRIES
    )

    @property
    def is_unchanged(self) -> bool:
        """Return ``True`` if the load created and updated nothing."""
        return not self.created and not self.updated
