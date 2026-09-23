"""The ``ImpactMetric`` aggregate root.

A metric is a registry entry, not a figure: it says what a claim's value means. Its
code, category, value kind, unit, currency and aggregation never change once created,
because stored claims would silently change meaning; a different definition is a new
code and the old one is retired. Only labels change in place (``relabel``).

Patterns: Entity, Aggregate Root.
"""

from datetime import UTC, datetime
from typing import Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from yakhnama.modules.impacts.domain.errors import ImpactMetricRetiredError
from yakhnama.modules.impacts.domain.events import (
    ImpactMetricRelabelled,
    ImpactMetricRetired,
)
from yakhnama.modules.impacts.domain.value_objects import (
    REQUIRED_LABEL_LANGUAGE,
    Aggregation,
    CurrencyCode,
    DesInventarField,
    ImpactMetricRef,
    MetricCategory,
    MetricCode,
    MetricStatus,
    RetirementReason,
    SendaiIndicator,
    ValueKind,
    definition_problems,
    is_valid_metric_value,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import EntityId, IdGenerator
from yakhnama.shared_kernel.value_objects import LocalizedText, Unit


class ImpactMetric(BaseModel):
    """One entry of the impact metric registry.

    Implements: Aggregate Root.

    Attributes:
        id: Database identity (UUIDv7).
        code: Stable public code, never reused.
        labels: Display names; an English label is required.
        description: Exact meaning (what is counted, what is excluded), if written.
        category: The metric's group.
        value_kind: Count, SI measurement or money.
        unit: ``count`` for counts, a ``KNOWN_UNITS`` unit for measurements,
            ``None`` for money.
        currency: ISO 4217 code for monetary metrics, otherwise ``None``.
        sendai: Proposed Sendai Framework indicator, if any.
        desinventar: Proposed DesInventar effect field, if any.
        aggregation: How the Phase 3 best-figure policy combines claims.
        status: ``active`` or ``retired``.
        retirement: Why the metric was retired; set exactly when retired.
        version: Optimistic-concurrency version, 1 on creation, +1 per change.
        created_at: When the metric was created, UTC.
        updated_at: When it last changed, UTC, never before ``created_at``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    code: MetricCode
    labels: LocalizedText
    description: LocalizedText | None = None
    category: MetricCategory
    value_kind: ValueKind
    unit: Unit | None
    currency: CurrencyCode | None = None
    sendai: SendaiIndicator | None = None
    desinventar: DesInventarField | None = None
    aggregation: Aggregation
    status: MetricStatus = MetricStatus.ACTIVE
    retirement: RetirementReason | None = None
    version: int = Field(default=1, ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def _normalise_to_utc(cls, moment: datetime) -> datetime:
        return moment.astimezone(UTC)

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        problems = list(definition_problems(self.value_kind, self.unit, self.currency))
        if REQUIRED_LABEL_LANGUAGE not in self.labels.texts:
            problems.append("labels need an English ('en') entry")
        if (self.status is MetricStatus.RETIRED) != (self.retirement is not None):
            problems.append("a retirement reason is required exactly when retired")
        if self.retirement is not None and self.retirement.replaced_by == self.code:
            problems.append("a metric cannot be replaced by itself")
        if self.updated_at < self.created_at:
            problems.append("updated_at must not be earlier than created_at")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @property
    def ref(self) -> ImpactMetricRef:
        """Return a code reference to this metric.

        Returns:
            The ``ImpactMetricRef`` claims hold.
        """
        return ImpactMetricRef(code=self.code)

    @property
    def is_active(self) -> bool:
        """Tell whether new claims may use this metric.

        Returns:
            ``True`` unless the metric is retired.
        """
        return self.status is MetricStatus.ACTIVE

    def is_valid_value(self, value: float) -> bool:
        """Tell whether ``value`` is acceptable for this metric.

        Args:
            value: A candidate claim value in the metric's unit or currency.

        Returns:
            ``True`` if it is finite and non-negative and, for counts, whole.
        """
        return is_valid_metric_value(self.value_kind, value)

    def retire(
        self, reason: RetirementReason, *, clock: Clock, id_generator: IdGenerator
    ) -> "AggregateChange[ImpactMetric]":
        """Retire the metric; its code stays taken for ever.

        Args:
            reason: Why, and which metric replaces it.
            clock: Source of the change time.
            id_generator: Source of the event id.

        Returns:
            The retired metric and one ``ImpactMetricRetired`` event.

        Raises:
            ImpactMetricRetiredError: If the metric is already retired.
        """
        self._require_active("retire")
        now = clock.now()
        state = self._changed(
            now, status=MetricStatus.RETIRED, retirement=reason.model_dump()
        )
        event = ImpactMetricRetired(
            event_id=id_generator.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            code=self.code,
            reason=reason,
        )
        return AggregateChange[ImpactMetric](state=state, events=(event,))

    def relabel(
        self, labels: LocalizedText, *, clock: Clock, id_generator: IdGenerator
    ) -> "AggregateChange[ImpactMetric]":
        """Replace the labels; identical labels are a no-op without an event.

        Args:
            labels: The new labels, with an English entry.
            clock: Source of the change time.
            id_generator: Source of the event id.

        Returns:
            The relabelled metric and one ``ImpactMetricRelabelled`` event, or the
            unchanged metric and no event if the labels are the same.

        Raises:
            ImpactMetricRetiredError: If the metric is retired (proposed: retired
                entries are frozen so exports of old claims stay stable).
            pydantic.ValidationError: If ``labels`` has no English entry.
        """
        self._require_active("relabel")
        if labels == self.labels:
            return AggregateChange[ImpactMetric](state=self)
        now = clock.now()
        state = self._changed(now, labels=labels.model_dump())
        event = ImpactMetricRelabelled(
            event_id=id_generator.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            code=self.code,
            labels=labels,
        )
        return AggregateChange[ImpactMetric](state=state, events=(event,))

    def _require_active(self, action: str) -> None:
        if not self.is_active:
            message = f"cannot {action} retired impact metric {self.code!r}"
            raise ImpactMetricRetiredError(message, details={"code": self.code})

    def _changed(self, now: datetime, **changes: object) -> Self:
        # Re-validate rather than model_copy(update=...): model_copy skips validators,
        # and every new state must satisfy the invariants.
        data = self.model_dump()
        data.update(changes, version=self.version + 1, updated_at=now)
        return self.model_validate(data)
