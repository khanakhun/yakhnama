"""The ``ImpactClaim`` aggregate: one metric value from one source, append-only.

A claim is never edited or deleted (``AGENTS.md`` §5). It has exactly two ways to
change:

- ``retract``: the claim becomes ``retracted`` with a reason and stops counting
  towards the best figure; it stays stored and exported.
- ``correct``: a **new** claim is created whose ``supersedes_id`` names the old one,
  and the old claim is retracted in the same change. Both states and both events are
  returned together (``ClaimCorrection``) so the handler saves them in one unit of
  work, like a report revision: the history keeps the wrong figure, who replaced it,
  when and why.

The value never changes in place, so a figure someone once published can always be
traced back to the exact claim it came from.

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

from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.errors import (
    ClaimImmutableError,
    ClaimMetricMismatchError,
    ClaimValueKindMismatchError,
    ClaimValueUnitMismatchError,
    ImpactMetricRetiredError,
)
from yakhnama.modules.impacts.domain.events import (
    ImpactClaimCorrected,
    ImpactClaimRetracted,
)
from yakhnama.modules.impacts.domain.value_objects import (
    ClaimNote,
    ClaimScope,
    ClaimStatus,
    ClaimValue,
    ImpactMetricRef,
    MonetaryValue,
    RetractionReason,
    SourceTypeName,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange, DomainEvent, EventRecorder
from yakhnama.shared_kernel.ids import EntityId, IdGenerator
from yakhnama.shared_kernel.value_objects import Confidence, DateWithPrecision


def ensure_value_fits_metric(metric: ImpactMetric, value: ClaimValue) -> None:
    """Check that ``value`` has the metric's kind and unit or currency.

    The value objects already apply ``is_valid_metric_value`` (finite, non-negative,
    whole for counts), the same rule as ``ImpactMetric.is_valid_value``, so only the
    kind and the unit or currency are left to compare.

    Args:
        metric: The metric the value is claimed for.
        value: The candidate value.

    Raises:
        ClaimValueKindMismatchError: If the value's kind is not the metric's.
        ClaimValueUnitMismatchError: If its unit or currency is not the metric's.
    """
    details = {"metric_code": metric.code, "value_kind": metric.value_kind.value}
    if value.value_kind is not metric.value_kind:
        message = (
            f"metric {metric.code!r} holds {metric.value_kind.value} values, "
            f"not {value.value_kind.value}"
        )
        raise ClaimValueKindMismatchError(message, details=details)
    expected = metric.currency if isinstance(value, MonetaryValue) else metric.unit
    if value.unit_or_currency != expected:
        message = (
            f"metric {metric.code!r} is expressed in {expected!r}, "
            f"not {value.unit_or_currency!r}"
        )
        raise ClaimValueUnitMismatchError(message, details=details)


def ensure_metric_accepts_claims(metric: ImpactMetric) -> None:
    """Check that new claims may use ``metric``.

    Args:
        metric: The metric of the new claim.

    Raises:
        ImpactMetricRetiredError: If the metric is retired.
    """
    if not metric.is_active:
        message = f"retired impact metric {metric.code!r} accepts no new claims"
        raise ImpactMetricRetiredError(message, details={"code": metric.code})


class ImpactClaim(BaseModel):
    """One metric value for one event from one source, with its confidence.

    Implements: Aggregate Root.

    Attributes:
        id: Identity of the claim (UUIDv7).
        event_id: The hazard event the claim is about.
        metric: The metric, by code.
        value: The claimed value; its kind and unit match the metric.
        confidence: How far the source's figure can be trusted.
        source_id: The provenance source the figure comes from.
        source_type: That source's type, copied so ranking needs no lookup.
        claimed_at: When the source made the claim, with its precision.
        recorded_by: The account that entered the claim.
        scope: The part of the event the figure covers; whole event by default.
        note: A moderator's note, if any.
        status: ``active`` or ``retracted``.
        retraction_reason: Why it was retracted; set exactly when retracted.
        retracted_by: Who retracted it; set exactly when retracted.
        supersedes_id: The claim this one corrects, if any.
        version: Optimistic-concurrency version, 1 on creation, +1 per change.
        created_at: When the claim was recorded in Yakhnama, UTC.
        updated_at: When it last changed, UTC, never before ``created_at``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    event_id: EntityId
    metric: ImpactMetricRef
    value: ClaimValue
    confidence: Confidence
    source_id: EntityId
    source_type: SourceTypeName
    claimed_at: DateWithPrecision
    recorded_by: EntityId
    scope: ClaimScope = ClaimScope()
    note: ClaimNote | None = None
    status: ClaimStatus = ClaimStatus.ACTIVE
    retraction_reason: RetractionReason | None = None
    retracted_by: EntityId | None = None
    supersedes_id: EntityId | None = None
    version: int = Field(default=1, ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def _normalise_to_utc(cls, moment: datetime) -> datetime:
        return moment.astimezone(UTC)

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        problems: list[str] = []
        is_retracted = self.status is ClaimStatus.RETRACTED
        if is_retracted != (self.retraction_reason is not None):
            problems.append("a retraction reason is required exactly when retracted")
        if is_retracted != (self.retracted_by is not None):
            problems.append("retracted_by is required exactly when retracted")
        if self.supersedes_id == self.id:
            problems.append("a claim cannot supersede itself")
        if (
            isinstance(self.value, MonetaryValue)
            and self.value.price_year > self.claimed_at.value.year
        ):
            problems.append("price_year must not be later than the claim's year")
        if self.updated_at < self.created_at:
            problems.append("updated_at must not be earlier than created_at")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @property
    def is_active(self) -> bool:
        """Tell whether the claim still counts towards the best figure.

        Returns:
            ``True`` unless the claim is retracted.
        """
        return self.status is ClaimStatus.ACTIVE

    def retract(
        self,
        reason: str,
        *,
        retracted_by: EntityId,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> "AggregateChange[ImpactClaim]":
        """Retract the claim; it stays stored and stops counting.

        Args:
            reason: Why, 1 to 1000 characters of safe text.
            retracted_by: The account retracting it.
            clock: Source of the change time.
            id_generator: Source of the event id.

        Returns:
            The retracted claim and one ``ImpactClaimRetracted`` event.

        Raises:
            ClaimImmutableError: If the claim is already retracted.
            pydantic.ValidationError: If ``reason`` is empty, too long or unsafe.
        """
        return self._retract(
            reason,
            retracted_by=retracted_by,
            now=clock.now(),
            id_generator=id_generator,
            superseded_by_id=None,
        )

    def correct(  # noqa: PLR0913  # reason: one keyword per corrected field
        self,
        new_value: ClaimValue,
        *,
        metric: ImpactMetric,
        reason: str,
        recorded_by: EntityId,
        clock: Clock,
        id_generator: IdGenerator,
        confidence: Confidence | None = None,
        claimed_at: DateWithPrecision | None = None,
        note: str | None = None,
    ) -> "ClaimCorrection":
        """Replace the claim with a new one and retract this one, in one change.

        The source, source type, event, metric and scope carry over: a figure from a
        different source is a new claim, not a correction.

        Args:
            new_value: The corrected value.
            metric: The claim's metric, to validate the new value against.
            reason: Why the old claim is retracted.
            recorded_by: The account making the correction; also the retractor.
            clock: Source of the change time.
            id_generator: Source of the new claim's id and of event ids.
            confidence: The corrected confidence; the old one if omitted.
            claimed_at: When the source made the corrected claim; the old time if
                omitted.
            note: A note for the new claim, if any.

        Returns:
            The retracted old claim and the new claim, with their events.

        Raises:
            ClaimImmutableError: If this claim is already retracted.
            ClaimMetricMismatchError: If ``metric`` is not this claim's metric.
            ImpactMetricRetiredError: If the metric is retired (proposed: a retired
                metric accepts no new claims, corrections included).
            ClaimValueKindMismatchError: If the value's kind is not the metric's.
            ClaimValueUnitMismatchError: If its unit or currency is not the metric's.
            pydantic.ValidationError: If ``reason`` or ``note`` is invalid.
        """
        self._require_active("correct")
        if metric.code != self.metric.code:
            message = (
                f"claim of metric {self.metric.code!r} cannot be corrected under "
                f"{metric.code!r}"
            )
            raise ClaimMetricMismatchError(message, details={"claim_id": str(self.id)})
        ensure_metric_accepts_claims(metric)
        ensure_value_fits_metric(metric, new_value)
        now = clock.now()
        replacement = ImpactClaim(
            id=id_generator.new_id(),
            event_id=self.event_id,
            metric=self.metric,
            value=new_value,
            confidence=confidence or self.confidence,
            source_id=self.source_id,
            source_type=self.source_type,
            claimed_at=claimed_at or self.claimed_at,
            recorded_by=recorded_by,
            scope=self.scope,
            note=note,
            supersedes_id=self.id,
            created_at=now,
            updated_at=now,
        )
        superseded = self._retract(
            reason,
            retracted_by=recorded_by,
            now=now,
            id_generator=id_generator,
            superseded_by_id=replacement.id,
        )
        corrected = ImpactClaimCorrected(
            event_id=id_generator.new_id(),
            occurred_at=now,
            aggregate_id=replacement.id,
            hazard_event_id=replacement.event_id,
            metric_code=replacement.metric.code,
            supersedes_id=self.id,
            value=replacement.value,
            confidence=replacement.confidence,
            claimed_at=replacement.claimed_at,
            recorded_by=recorded_by,
        )
        return ClaimCorrection(
            superseded=superseded,
            replacement=AggregateChange[ImpactClaim](
                state=replacement, events=(corrected,)
            ),
        )

    def _retract(
        self,
        reason: str,
        *,
        retracted_by: EntityId,
        now: datetime,
        id_generator: IdGenerator,
        superseded_by_id: EntityId | None,
    ) -> "AggregateChange[ImpactClaim]":
        self._require_active("retract")
        state = self._changed(
            now,
            status=ClaimStatus.RETRACTED,
            retraction_reason=reason,
            retracted_by=retracted_by,
        )
        event = ImpactClaimRetracted(
            event_id=id_generator.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            hazard_event_id=self.event_id,
            metric_code=self.metric.code,
            retracted_by=retracted_by,
            superseded_by_id=superseded_by_id,
        )
        return AggregateChange[ImpactClaim](state=state, events=(event,))

    def _require_active(self, action: str) -> None:
        if not self.is_active:
            message = f"cannot {action} a retracted impact claim"
            raise ClaimImmutableError(message, details={"claim_id": str(self.id)})

    def _changed(self, now: datetime, **changes: object) -> Self:
        # Re-validate rather than model_copy(update=...): model_copy skips validators,
        # and every new state must satisfy the invariants.
        data = self.model_dump()
        data.update(changes, version=self.version + 1, updated_at=now)
        return self.model_validate(data)


class ClaimCorrection(BaseModel):
    """The two changes of a correction: the old claim retracted, the new one created.

    Implements: Domain Events (the two-aggregate counterpart of ``AggregateChange``).

    Attributes:
        superseded: The retracted old claim and its ``ImpactClaimRetracted`` event.
        replacement: The new claim and its ``ImpactClaimCorrected`` event.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    superseded: AggregateChange[ImpactClaim]
    replacement: AggregateChange[ImpactClaim]

    @model_validator(mode="after")
    def _check_link(self) -> Self:
        if self.replacement.state.supersedes_id != self.superseded.state.id:
            message = "the replacement must supersede the retracted claim"
            raise ValueError(message)
        return self

    @property
    def events(self) -> tuple[DomainEvent, ...]:
        """Return both changes' events in order: the retraction, then the correction.

        Returns:
            The events to publish.
        """
        return (*self.superseded.events, *self.replacement.events)

    def record_into(self, recorder: EventRecorder) -> tuple[ImpactClaim, ImpactClaim]:
        """Hand every event to ``recorder`` in order and return both states.

        Args:
            recorder: Usually the handler's unit of work.

        Returns:
            The retracted old claim and the new claim, to save in the same unit of
            work.
        """
        return (
            self.superseded.record_into(recorder),
            self.replacement.record_into(recorder),
        )
