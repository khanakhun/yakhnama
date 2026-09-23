"""The best-figure policy: one current value per event and metric, from its claims.

The best figure is a derived read model, never stored as a fact: claims are the
record, and the figure is recomputed from them by the explicit rules below. Every rule
is a **proposed default** documented with worked examples in
``docs/architecture/best-figure.md``.

1. Only ``active`` claims count; retracted claims never contribute.
2. The metric's ``aggregation`` decides how the remaining claims combine:

   - ``sum``: keep **one** claim per ``(source_id, scope)``, the newest, so a source
     that updates its own figure is not counted twice; then add the kept claims.
   - ``max``: take the claim with the largest value.
   - ``latest``: take the claim the source made most recently.

3. "Newest" compares ``claimed_at`` floored to the start of its precision period
   (a claim "in August" is not assumed to be later than one on 15 August). Ties are
   broken by ``SourceRank`` (government > research > satellite > dataset >
   organisation > news > citizen), then by recording order in Yakhnama. For ``max``,
   equal values are broken the same way.
4. The figure's ``confidence`` is the lowest confidence among contributing claims:
   a sum is only as trustworthy as its weakest part.
5. With no active claim the figure has no value, basis ``none`` and no confidence.

Patterns: Policy, Value Object.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    field_validator,
    model_validator,
)

from yakhnama.modules.impacts.domain.claims import (
    ImpactClaim,
    ensure_value_fits_metric,
)
from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.errors import ClaimMetricMismatchError
from yakhnama.modules.impacts.domain.value_objects import (
    ClaimScope,
    ClaimValue,
    CountValue,
    ImpactMetricRef,
    MeasurementValue,
    MonetaryValue,
    SourceRank,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.value_objects import Confidence, Measurement

BestFigureBasis = Literal["sum", "max", "latest", "none"]
"""How a best figure was obtained; ``none`` when no active claim exists."""

CONFIDENCE_RANK: Final = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}
"""Confidence from least to most trusted, for taking the minimum."""

type _RecencyKey = tuple[datetime, int, datetime, EntityId]


class BestFigure(BaseModel):
    """The current best value of one metric for one event.

    Implements: Value Object.

    Attributes:
        metric: The metric, by code.
        value: The best value, ``None`` when no active claim exists.
        basis: The aggregation applied, or ``none``.
        contributing_claim_ids: The claims the value is computed from, in
            recording order; empty exactly when ``basis`` is ``none``.
        confidence: The lowest confidence among contributing claims, ``None``
            exactly when ``basis`` is ``none``.
        computed_at: When the figure was computed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric: ImpactMetricRef
    value: ClaimValue | None
    basis: BestFigureBasis
    contributing_claim_ids: tuple[EntityId, ...]
    confidence: Confidence | None
    computed_at: AwareDatetime

    @field_validator("computed_at", mode="after")
    @classmethod
    def _normalise_to_utc(cls, moment: datetime) -> datetime:
        return moment.astimezone(UTC)

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        is_empty = self.basis == "none"
        if (
            (self.value is None) != is_empty
            or (not self.contributing_claim_ids) != is_empty
            or (self.confidence is None) != is_empty
        ):
            message = (
                "value, contributing claims and confidence are absent exactly when "
                "the basis is 'none'"
            )
            raise ValueError(message)
        return self


class BestFigurePolicy:
    """Computes the best figure of one metric for one event from its claims.

    Implements: Policy.
    """

    def __init__(self, *, clock: Clock) -> None:
        """Create the policy.

        Args:
            clock: Source of ``computed_at``.
        """
        self._clock = clock

    def compute(
        self, metric: ImpactMetric, claims: Sequence[ImpactClaim]
    ) -> BestFigure:
        """Apply the metric's aggregation to the active claims.

        Args:
            metric: The metric; its ``aggregation`` chooses the rule. A retired
                metric is accepted, since its old claims still have a best figure.
            claims: Every claim of one event for this metric, active or retracted.

        Returns:
            The best figure, with basis ``none`` if no claim is active.

        Raises:
            ClaimMetricMismatchError: If a claim belongs to another metric, or the
                claims belong to more than one event.
            ClaimValueKindMismatchError: If a claim's kind is not the metric's.
            ClaimValueUnitMismatchError: If a claim's unit or currency is not the
                metric's.
        """
        _check_claims(metric, claims)
        active = [claim for claim in claims if claim.is_active]
        now = self._clock.now()
        if not active:
            return BestFigure(
                metric=metric.ref,
                value=None,
                basis="none",
                contributing_claim_ids=(),
                confidence=None,
                computed_at=now,
            )
        if metric.aggregation == "sum":
            contributing = _newest_per_source_and_scope(active)
            value = _sum_values([claim.value for claim in contributing])
        elif metric.aggregation == "max":
            chosen = max(active, key=_largest_key)
            contributing, value = [chosen], chosen.value
        else:
            chosen = max(active, key=_recency_key)
            contributing, value = [chosen], chosen.value
        return BestFigure(
            metric=metric.ref,
            value=value,
            basis=metric.aggregation,
            contributing_claim_ids=tuple(
                claim.id for claim in sorted(contributing, key=_recording_key)
            ),
            confidence=min(
                (claim.confidence for claim in contributing),
                key=CONFIDENCE_RANK.__getitem__,
            ),
            computed_at=now,
        )


def magnitude(value: ClaimValue) -> Decimal:
    """Return a claim value's number as an exact ``Decimal`` for comparing and adding.

    ``Decimal(float)`` is exact, so a measurement loses nothing in the conversion.

    Args:
        value: Any claim value.

    Returns:
        The count, the measured quantity or the amount.
    """
    if isinstance(value, CountValue):
        return Decimal(value.count)
    if isinstance(value, MeasurementValue):
        return Decimal(value.measurement.value)
    return value.amount


def _check_claims(metric: ImpactMetric, claims: Sequence[ImpactClaim]) -> None:
    event_ids = {claim.event_id for claim in claims}
    if len(event_ids) > 1:
        message = "a best figure combines the claims of exactly one event"
        raise ClaimMetricMismatchError(message, details={"metric_code": metric.code})
    for claim in claims:
        if claim.metric.code != metric.code:
            message = (
                f"claim of metric {claim.metric.code!r} cannot count towards "
                f"{metric.code!r}"
            )
            raise ClaimMetricMismatchError(message, details={"claim_id": str(claim.id)})
        ensure_value_fits_metric(metric, claim.value)


def _recording_key(claim: ImpactClaim) -> tuple[datetime, EntityId]:
    # UUIDv7 ids are time-ordered, so they settle claims recorded in the same instant.
    return (claim.created_at, claim.id)


def _recency_key(claim: ImpactClaim) -> _RecencyKey:
    return (
        claim.claimed_at.truncate().value,
        SourceRank.of(claim.source_type),
        *_recording_key(claim),
    )


def _largest_key(claim: ImpactClaim) -> tuple[Decimal, int, _RecencyKey]:
    return (
        magnitude(claim.value),
        SourceRank.of(claim.source_type),
        _recency_key(claim),
    )


def _newest_per_source_and_scope(claims: Sequence[ImpactClaim]) -> list[ImpactClaim]:
    newest: dict[tuple[EntityId, ClaimScope], ImpactClaim] = {}
    for claim in sorted(claims, key=_recency_key):
        # Sorted oldest first, so the last claim written per key is the newest.
        newest[(claim.source_id, claim.scope)] = claim
    return list(newest.values())


def _sum_values(values: Sequence[ClaimValue]) -> ClaimValue:
    # Every value has the metric's kind and unit (checked in _check_claims), so the
    # first one is a template for the kind, unit and currency of the total.
    total = sum((magnitude(value) for value in values), Decimal(0))
    template = values[0]
    if isinstance(template, CountValue):
        return CountValue(count=int(total))
    if isinstance(template, MeasurementValue):
        return MeasurementValue(
            measurement=Measurement(value=float(total), unit=template.measurement.unit)
        )
    # Amounts are nominal and never converted (proposed); the total is labelled with
    # the latest price year among its parts, an open question for the maintainer.
    price_year = max(
        value.price_year for value in values if isinstance(value, MonetaryValue)
    )
    return MonetaryValue(
        amount=total, currency=template.currency, price_year=price_year
    )
