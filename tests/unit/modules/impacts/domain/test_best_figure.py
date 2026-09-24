"""Unit tests for ``yakhnama.modules.impacts.domain.best_figure``."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from tests.factories.impacts import ImpactClaimTestFactory, ImpactMetricTestFactory
from tests.fakes.clock import FrozenClock
from yakhnama.modules.impacts.domain.best_figure import (
    CONFIDENCE_RANK,
    BestFigure,
    BestFigurePolicy,
    magnitude,
)
from yakhnama.modules.impacts.domain.claims import ImpactClaim
from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.errors import (
    ClaimMetricMismatchError,
    ClaimValueKindMismatchError,
)
from yakhnama.modules.impacts.domain.value_objects import (
    SOURCE_TYPE_NAMES,
    Aggregation,
    ClaimScope,
    ClaimValue,
    CountValue,
    MeasurementValue,
    MonetaryValue,
    SourceTypeName,
    ValueKind,
)
from yakhnama.shared_kernel.ids import Uuid7Generator
from yakhnama.shared_kernel.value_objects import (
    Confidence,
    DatePrecision,
    DateWithPrecision,
    Measurement,
)

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
IDS = Uuid7Generator()
EVENT_ID = IDS.new_id()
SOURCES = tuple(IDS.new_id() for _ in range(3))
SCOPES = (ClaimScope(), ClaimScope(place_code="pk.gb.hunza"))
DAY_ONE = datetime(2022, 8, 1, tzinfo=UTC)
# Later than every recorded time, so a retraction never predates its claim.
POLICY_CLOCK = FrozenClock(NOW + timedelta(days=1))
POLICY = BestFigurePolicy(clock=FrozenClock(NOW))


def _metric(
    aggregation: Aggregation, kind: ValueKind = ValueKind.COUNT
) -> ImpactMetric:
    if kind is ValueKind.MEASUREMENT:
        return ImpactMetricTestFactory.build(
            value_kind=kind, unit="cubic_metre", aggregation=aggregation
        )
    return ImpactMetricTestFactory.build(value_kind=kind, aggregation=aggregation)


def _claim(  # noqa: PLR0913  # reason: one keyword per varied claim field
    metric: ImpactMetric,
    value: ClaimValue,
    *,
    day: int = 0,
    source: int = 0,
    scope: ClaimScope | None = None,
    source_type: SourceTypeName = "news",
    confidence: Confidence = Confidence.HIGH,
    is_retracted: bool = False,
    recorded_offset: int = 0,
    precision: DatePrecision = DatePrecision.DAY,
) -> ImpactClaim:
    recorded = NOW + timedelta(seconds=recorded_offset)
    claim = ImpactClaimTestFactory.build(
        event_id=EVENT_ID,
        metric=metric.ref,
        value=value,
        source_id=SOURCES[source],
        source_type=source_type,
        scope=scope or ClaimScope(),
        confidence=confidence,
        claimed_at=DateWithPrecision(
            value=DAY_ONE + timedelta(days=day), precision=precision
        ),
        created_at=recorded,
        updated_at=recorded,
    )
    if not is_retracted:
        return claim
    return claim.retract(
        "withdrawn", retracted_by=IDS.new_id(), clock=POLICY_CLOCK, id_generator=IDS
    ).state


@st.composite
def _count_claims(draw: st.DrawFn, metric: ImpactMetric) -> list[ImpactClaim]:
    size = draw(st.integers(min_value=0, max_value=12))
    return [
        _claim(
            metric,
            CountValue(count=draw(st.integers(min_value=0, max_value=10_000))),
            day=draw(st.integers(min_value=0, max_value=5)),
            source=draw(st.integers(min_value=0, max_value=len(SOURCES) - 1)),
            scope=draw(st.sampled_from(SCOPES)),
            source_type=draw(st.sampled_from(SOURCE_TYPE_NAMES)),
            confidence=draw(st.sampled_from(list(Confidence))),
            is_retracted=draw(st.booleans()),
            recorded_offset=index,
        )
        for index in range(size)
    ]


SUM_METRIC = _metric("sum")
MAX_METRIC = _metric("max")
LATEST_METRIC = _metric("latest")


def _by_id(claims: Sequence[ImpactClaim]) -> dict[object, ImpactClaim]:
    return {claim.id: claim for claim in claims}


# --------------------------------------------------------------------------- #
# Properties                                                                  #
# --------------------------------------------------------------------------- #


@given(claims=_count_claims(SUM_METRIC))
def test_best_figure_sum_counts_each_source_and_scope_once(
    claims: list[ImpactClaim],
) -> None:
    active_keys = {
        (claim.source_id, claim.scope) for claim in claims if claim.is_active
    }

    figure = POLICY.compute(SUM_METRIC, claims)

    contributing = [_by_id(claims)[i] for i in figure.contributing_claim_ids]
    keys = [(claim.source_id, claim.scope) for claim in contributing]
    assert len(keys) == len(set(keys)) == len(active_keys)
    assert set(keys) == active_keys
    if contributing:
        assert figure.value == CountValue(
            count=sum(int(magnitude(claim.value)) for claim in contributing)
        )


@given(claims=_count_claims(SUM_METRIC))
def test_best_figure_sum_keeps_the_newest_claim_of_each_source_and_scope(
    claims: list[ImpactClaim],
) -> None:
    figure = POLICY.compute(SUM_METRIC, claims)

    for claim in (_by_id(claims)[i] for i in figure.contributing_claim_ids):
        rivals = [
            other
            for other in claims
            if other.is_active
            and other.source_id == claim.source_id
            and other.scope == claim.scope
        ]
        assert all(other.claimed_at.value <= claim.claimed_at.value for other in rivals)


@given(claims=_count_claims(MAX_METRIC))
def test_best_figure_max_is_at_least_every_active_value(
    claims: list[ImpactClaim],
) -> None:
    active = [claim for claim in claims if claim.is_active]

    figure = POLICY.compute(MAX_METRIC, claims)

    if active:
        assert figure.value is not None
        assert all(
            magnitude(figure.value) >= magnitude(other.value) for other in active
        )
        assert figure.value in [claim.value for claim in active]
    else:
        assert (figure.basis, figure.value) == ("none", None)


@given(claims=_count_claims(LATEST_METRIC))
def test_best_figure_latest_picks_the_newest_active_claim(
    claims: list[ImpactClaim],
) -> None:
    active = [claim for claim in claims if claim.is_active]

    figure = POLICY.compute(LATEST_METRIC, claims)

    if active:
        (chosen_id,) = figure.contributing_claim_ids
        chosen = _by_id(claims)[chosen_id]
        assert all(
            chosen.claimed_at.value >= other.claimed_at.value for other in active
        )
        assert figure.value == chosen.value


@given(
    claims=_count_claims(SUM_METRIC),
    aggregation=st.sampled_from(["sum", "max", "latest"]),
)
def test_best_figure_retracted_claims_never_contribute(
    claims: list[ImpactClaim], aggregation: Aggregation
) -> None:
    metric = SUM_METRIC.model_copy(update={"aggregation": aggregation})
    retracted = {claim.id for claim in claims if not claim.is_active}

    figure = POLICY.compute(metric, claims)

    assert retracted.isdisjoint(figure.contributing_claim_ids)


@given(
    claims=_count_claims(SUM_METRIC),
    aggregation=st.sampled_from(["sum", "max", "latest"]),
)
def test_best_figure_confidence_is_the_minimum_of_contributing_claims(
    claims: list[ImpactClaim], aggregation: Aggregation
) -> None:
    metric = SUM_METRIC.model_copy(update={"aggregation": aggregation})

    figure = POLICY.compute(metric, claims)

    contributing = [_by_id(claims)[i] for i in figure.contributing_claim_ids]
    expected = min(
        (claim.confidence for claim in contributing),
        key=CONFIDENCE_RANK.__getitem__,
        default=None,
    )
    assert figure.confidence == expected


# --------------------------------------------------------------------------- #
# Worked examples (docs/architecture/best-figure.md)                          #
# --------------------------------------------------------------------------- #


def test_best_figure_no_claims_has_basis_none() -> None:
    figure = POLICY.compute(SUM_METRIC, [])

    assert figure == BestFigure(
        metric=SUM_METRIC.ref,
        value=None,
        basis="none",
        contributing_claim_ids=(),
        confidence=None,
        computed_at=NOW,
    )


def test_best_figure_sum_same_source_update_replaces_its_earlier_figure() -> None:
    first = _claim(SUM_METRIC, CountValue(count=10), day=0, source=0)
    update = _claim(SUM_METRIC, CountValue(count=14), day=2, source=0)
    other = _claim(
        SUM_METRIC,
        CountValue(count=3),
        source=1,
        scope=SCOPES[1],
        confidence=Confidence.LOW,
    )

    figure = POLICY.compute(SUM_METRIC, [first, update, other])

    assert (figure.value, figure.basis, figure.confidence) == (
        CountValue(count=17),
        "sum",
        Confidence.LOW,
    )
    assert set(figure.contributing_claim_ids) == {update.id, other.id}


def test_best_figure_sum_measurements_adds_quantities_in_the_metric_unit() -> None:
    metric = _metric("sum", ValueKind.MEASUREMENT)
    claims = [
        _claim(
            metric,
            MeasurementValue(measurement=Measurement(value=1.5, unit="cubic_metre")),
            source=index,
        )
        for index in range(2)
    ]

    figure = POLICY.compute(metric, claims)

    assert figure.value == MeasurementValue(
        measurement=Measurement(value=3.0, unit="cubic_metre")
    )


def test_best_figure_sum_money_adds_nominal_amounts_with_latest_price_year() -> None:
    metric = _metric("sum", ValueKind.MONETARY)
    claims = [
        _claim(
            metric,
            MonetaryValue(amount=Decimal("100.25"), currency="PKR", price_year=year),
            source=index,
        )
        for index, year in enumerate((2020, 2022))
    ]

    figure = POLICY.compute(metric, claims)

    assert figure.value == MonetaryValue(
        amount=Decimal("200.50"), currency="PKR", price_year=2022
    )


def test_best_figure_max_equal_values_prefer_higher_source_rank() -> None:
    citizen = _claim(MAX_METRIC, CountValue(count=5), source_type="citizen", day=3)
    government = _claim(MAX_METRIC, CountValue(count=5), source_type="government")

    figure = POLICY.compute(MAX_METRIC, [citizen, government])

    assert figure.contributing_claim_ids == (government.id,)


def test_best_figure_latest_same_day_prefers_higher_source_rank() -> None:
    news = _claim(LATEST_METRIC, CountValue(count=8), source_type="news", source=0)
    research = _claim(
        LATEST_METRIC, CountValue(count=6), source_type="research", source=1
    )

    figure = POLICY.compute(LATEST_METRIC, [research, news])

    assert (figure.value, figure.contributing_claim_ids) == (
        CountValue(count=6),
        (research.id,),
    )


def test_best_figure_latest_full_tie_prefers_the_later_recorded_claim() -> None:
    earlier = _claim(LATEST_METRIC, CountValue(count=1), recorded_offset=0)
    later = _claim(LATEST_METRIC, CountValue(count=2), recorded_offset=5)

    figure = POLICY.compute(LATEST_METRIC, [later, earlier])

    assert figure.contributing_claim_ids == (later.id,)


def test_best_figure_latest_month_precision_is_not_assumed_later_than_a_day() -> None:
    in_august = _claim(
        LATEST_METRIC, CountValue(count=1), day=20, precision=DatePrecision.MONTH
    )
    on_second = _claim(LATEST_METRIC, CountValue(count=2), day=1)

    figure = POLICY.compute(LATEST_METRIC, [in_august, on_second])

    assert figure.contributing_claim_ids == (on_second.id,)


def test_best_figure_contributing_ids_are_in_recording_order() -> None:
    claims = [
        _claim(SUM_METRIC, CountValue(count=1), source=index, recorded_offset=-index)
        for index in range(3)
    ]

    figure = POLICY.compute(SUM_METRIC, claims)

    assert figure.contributing_claim_ids == tuple(
        claim.id for claim in reversed(claims)
    )


# --------------------------------------------------------------------------- #
# Rejections                                                                  #
# --------------------------------------------------------------------------- #


def test_best_figure_claim_of_another_metric_raises_mismatch() -> None:
    claim = _claim(MAX_METRIC, CountValue(count=1))

    with pytest.raises(ClaimMetricMismatchError, match="cannot count"):
        POLICY.compute(SUM_METRIC, [claim])


def test_best_figure_claims_of_two_events_raise_mismatch() -> None:
    claim = _claim(SUM_METRIC, CountValue(count=1))
    elsewhere = claim.model_copy(update={"event_id": IDS.new_id()})

    with pytest.raises(ClaimMetricMismatchError, match="one event"):
        POLICY.compute(SUM_METRIC, [claim, elsewhere])


def test_best_figure_claim_value_of_another_kind_raises_kind_mismatch() -> None:
    money = _metric("sum", ValueKind.MONETARY)
    claim = _claim(money, CountValue(count=1))

    with pytest.raises(ClaimValueKindMismatchError):
        POLICY.compute(money, [claim])


@pytest.mark.parametrize(
    "overrides",
    [
        {"basis": "sum"},
        {"value": CountValue(count=1)},
        {"contributing_claim_ids": (IDS.new_id(),)},
        {"confidence": Confidence.LOW},
    ],
)
def test_best_figure_partial_empty_state_is_rejected(
    overrides: dict[str, object],
) -> None:
    fields: dict[str, object] = {
        "metric": SUM_METRIC.ref,
        "value": None,
        "basis": "none",
        "contributing_claim_ids": (),
        "confidence": None,
        "computed_at": NOW,
    }
    fields.update(overrides)

    with pytest.raises(PydanticValidationError, match="absent exactly"):
        BestFigure.model_validate(fields)


def test_best_figure_computed_at_is_normalised_to_utc() -> None:
    local = NOW.astimezone(timezone(timedelta(hours=5)))

    figure = BestFigure(
        metric=SUM_METRIC.ref,
        value=None,
        basis="none",
        contributing_claim_ids=(),
        confidence=None,
        computed_at=local,
    )

    assert figure.computed_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (CountValue(count=7), Decimal(7)),
        (
            MeasurementValue(measurement=Measurement(value=0.5, unit="metre")),
            Decimal("0.5"),
        ),
        (
            MonetaryValue(amount=Decimal("9.99"), currency="PKR", price_year=2020),
            Decimal("9.99"),
        ),
    ],
)
def test_magnitude_returns_exact_decimal_of_each_kind(
    value: ClaimValue, expected: Decimal
) -> None:
    assert magnitude(value) == expected


def test_best_figure_sum_after_correction_and_retraction_counts_the_correction() -> (
    None
):
    original = _claim(SUM_METRIC, CountValue(count=10), source=0)
    correction = original.correct(
        CountValue(count=12),
        metric=SUM_METRIC,
        reason="district office revised its count",
        recorded_by=IDS.new_id(),
        clock=POLICY_CLOCK,
        id_generator=IDS,
    )
    duplicate = _claim(
        SUM_METRIC, CountValue(count=5), day=1, source=1, is_retracted=True
    )
    claims = [
        correction.superseded.state,
        correction.replacement.state,
        duplicate,
    ]

    figure = POLICY.compute(SUM_METRIC, claims)

    assert (figure.value, figure.contributing_claim_ids) == (
        CountValue(count=12),
        (correction.replacement.state.id,),
    )
