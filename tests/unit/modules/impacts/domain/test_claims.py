"""Unit tests for ``yakhnama.modules.impacts.domain.claims`` and its factory."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from tests.factories.impacts import ImpactClaimTestFactory, ImpactMetricTestFactory
from tests.fakes.clock import SteppingClock
from tests.fakes.uow import InMemoryUnitOfWork
from yakhnama.modules.impacts.domain.claims import (
    ClaimCorrection,
    ImpactClaim,
    ensure_value_fits_metric,
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
    ImpactClaimRecorded,
    ImpactClaimRetracted,
)
from yakhnama.modules.impacts.domain.factories import ImpactClaimFactory
from yakhnama.modules.impacts.domain.value_objects import (
    ClaimScope,
    ClaimStatus,
    ClaimValue,
    CountValue,
    MeasurementValue,
    MonetaryValue,
    RetirementReason,
    ValueKind,
)
from yakhnama.shared_kernel.ids import Uuid7Generator
from yakhnama.shared_kernel.value_objects import (
    Confidence,
    DatePrecision,
    DateWithPrecision,
    Measurement,
)

START = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
ONE_MINUTE = timedelta(minutes=1)
IDS = Uuid7Generator()
CLAIMED_AT = DateWithPrecision(
    value=datetime(2022, 8, 20, tzinfo=UTC), precision=DatePrecision.DAY
)
VALUES_BY_KIND: dict[ValueKind, ClaimValue] = {
    ValueKind.COUNT: CountValue(count=12),
    ValueKind.MEASUREMENT: MeasurementValue(
        measurement=Measurement(value=4.5, unit="square_metre")
    ),
    ValueKind.MONETARY: MonetaryValue(
        amount=Decimal("1000.00"), currency="PKR", price_year=2022
    ),
}


def _metric(kind: ValueKind = ValueKind.COUNT) -> ImpactMetric:
    if kind is ValueKind.MEASUREMENT:
        return ImpactMetricTestFactory.build(value_kind=kind, unit="square_metre")
    return ImpactMetricTestFactory.build(value_kind=kind)


def _clock() -> SteppingClock:
    return SteppingClock(START, ONE_MINUTE)


def _factory() -> ImpactClaimFactory:
    return ImpactClaimFactory(clock=_clock(), id_generator=Uuid7Generator(_clock()))


def _record(
    metric: ImpactMetric, value: ClaimValue, **overrides: object
) -> ImpactClaim:
    fields: dict[str, object] = {
        "event_id": IDS.new_id(),
        "confidence": Confidence.MEDIUM,
        "source_id": IDS.new_id(),
        "source_type": "government",
        "claimed_at": CLAIMED_AT,
        "recorded_by": IDS.new_id(),
    }
    fields.update(overrides)
    return _factory().record(metric=metric, value=value, **fields).state  # type: ignore[arg-type]  # reason: keyword overrides are typed by the factory signature at runtime


# --------------------------------------------------------------------------- #
# Factory                                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", list(ValueKind))
def test_impact_claim_factory_matching_value_records_active_claim_and_event(
    kind: ValueKind,
) -> None:
    metric = _metric(kind)
    event_id, source_id, recorder = IDS.new_id(), IDS.new_id(), IDS.new_id()

    change = _factory().record(
        metric=metric,
        event_id=event_id,
        value=VALUES_BY_KIND[kind],
        confidence=Confidence.HIGH,
        source_id=source_id,
        source_type="news",
        claimed_at=CLAIMED_AT,
        recorded_by=recorder,
    )

    claim, (event,) = change.state, change.events
    assert isinstance(event, ImpactClaimRecorded)
    assert (claim.status, claim.version, claim.scope, claim.metric) == (
        ClaimStatus.ACTIVE,
        1,
        ClaimScope(),
        metric.ref,
    )
    assert (
        event.aggregate_id,
        event.hazard_event_id,
        event.metric_code,
        event.value,
        event.source_id,
        event.source_type,
        event.recorded_by,
        event.occurred_at,
    ) == (
        claim.id,
        event_id,
        metric.code,
        VALUES_BY_KIND[kind],
        source_id,
        "news",
        recorder,
        claim.created_at,
    )


def test_impact_claim_factory_given_scope_and_note_are_kept() -> None:
    scope = ClaimScope(place_code="pk.gb.hunza")

    claim = _record(
        _metric(), CountValue(count=3), scope=scope, note="  from district office "
    )

    assert (claim.scope, claim.note) == (scope, "from district office")


@given(
    metric_kind=st.sampled_from(list(ValueKind)),
    value_kind=st.sampled_from(list(ValueKind)),
)
def test_impact_claim_factory_value_kind_mismatch_is_rejected(
    metric_kind: ValueKind, value_kind: ValueKind
) -> None:
    metric = _metric(metric_kind)
    value = VALUES_BY_KIND[value_kind]

    if metric_kind is value_kind:
        claim = _record(metric, value)
        assert claim.value == value
    else:
        with pytest.raises(ClaimValueKindMismatchError):
            _record(metric, value)


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        (
            ValueKind.MEASUREMENT,
            MeasurementValue(measurement=Measurement(value=1, unit="metre")),
        ),
        (
            ValueKind.MONETARY,
            MonetaryValue(amount=Decimal(1), currency="USD", price_year=2022),
        ),
    ],
)
def test_impact_claim_factory_unit_or_currency_mismatch_is_rejected(
    kind: ValueKind, value: ClaimValue
) -> None:
    metric = _metric(kind)

    with pytest.raises(ClaimValueUnitMismatchError):
        _record(metric, value)


def test_impact_claim_factory_retired_metric_is_rejected() -> None:
    metric = _metric().retire(
        RetirementReason(explanation="split"),
        clock=_clock(),
        id_generator=IDS,
    )

    with pytest.raises(ImpactMetricRetiredError):
        _record(metric.state, CountValue(count=1))


def test_ensure_value_fits_metric_matching_value_raises_nothing() -> None:
    metric = _metric(ValueKind.MONETARY)

    ensure_value_fits_metric(metric, VALUES_BY_KIND[ValueKind.MONETARY])

    assert metric.value_kind is ValueKind.MONETARY


# --------------------------------------------------------------------------- #
# Invariants                                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"status": ClaimStatus.RETRACTED}, "retraction reason"),
        ({"retraction_reason": "wrong"}, "retraction reason"),
        ({"retracted_by": IDS.new_id()}, "retracted_by"),
        (
            {
                "value": MonetaryValue(
                    amount=Decimal(1), currency="PKR", price_year=2023
                )
            },
            "price_year",
        ),
        ({"updated_at": START - ONE_MINUTE}, "updated_at"),
    ],
)
def test_impact_claim_broken_invariant_is_rejected(
    overrides: dict[str, object], fragment: str
) -> None:
    fields = ImpactClaimTestFactory.build(
        claimed_at=CLAIMED_AT, created_at=START
    ).model_dump()
    fields.update(overrides)

    with pytest.raises(PydanticValidationError, match=fragment):
        ImpactClaim.model_validate(fields)


def test_impact_claim_superseding_itself_is_rejected() -> None:
    fields = ImpactClaimTestFactory.build().model_dump()
    fields["supersedes_id"] = fields["id"]

    with pytest.raises(PydanticValidationError, match="supersede itself"):
        ImpactClaim.model_validate(fields)


def test_impact_claim_offset_timestamps_are_normalised_to_utc() -> None:
    local = START.astimezone(timezone(timedelta(hours=5)))

    claim = ImpactClaimTestFactory.build(created_at=local, updated_at=local)

    assert claim.created_at.utcoffset() == timedelta(0)


def test_impact_claim_value_is_frozen() -> None:
    claim = ImpactClaimTestFactory.build()

    with pytest.raises(PydanticValidationError):
        claim.value = CountValue(count=1)  # type: ignore[misc]  # reason: asserting the frozen model refuses assignment


# --------------------------------------------------------------------------- #
# Retraction                                                                  #
# --------------------------------------------------------------------------- #


def test_impact_claim_retract_active_claim_returns_retracted_state_and_event() -> None:
    claim = ImpactClaimTestFactory.build(created_at=START)
    moderator = IDS.new_id()

    change = claim.retract(
        "duplicate", retracted_by=moderator, clock=_clock(), id_generator=IDS
    )

    state, (event,) = change.state, change.events
    assert isinstance(event, ImpactClaimRetracted)
    assert (
        state.status,
        state.retraction_reason,
        state.retracted_by,
        state.version,
        state.value,
        state.is_active,
    ) == (ClaimStatus.RETRACTED, "duplicate", moderator, 2, claim.value, False)
    assert (event.aggregate_id, event.retracted_by, event.superseded_by_id) == (
        claim.id,
        moderator,
        None,
    )


def test_impact_claim_retract_retracted_claim_raises_claim_immutable() -> None:
    retracted = (
        ImpactClaimTestFactory.build(created_at=START)
        .retract("first", retracted_by=IDS.new_id(), clock=_clock(), id_generator=IDS)
        .state
    )

    with pytest.raises(ClaimImmutableError):
        retracted.retract(
            "second", retracted_by=IDS.new_id(), clock=_clock(), id_generator=IDS
        )


def test_impact_claim_retract_empty_reason_is_rejected() -> None:
    claim = ImpactClaimTestFactory.build(created_at=START)

    with pytest.raises(PydanticValidationError):
        claim.retract("  ", retracted_by=IDS.new_id(), clock=_clock(), id_generator=IDS)


# --------------------------------------------------------------------------- #
# Correction                                                                  #
# --------------------------------------------------------------------------- #


def _correct(
    claim: ImpactClaim, metric: ImpactMetric, count: int, **overrides: object
) -> ClaimCorrection:
    return claim.correct(
        CountValue(count=count),
        metric=metric,
        reason="source revised its figure",
        recorded_by=IDS.new_id(),
        clock=_clock(),
        id_generator=IDS,
        **overrides,  # type: ignore[arg-type]  # reason: optional keywords typed by the method signature at runtime
    )


def test_impact_claim_correct_returns_new_claim_and_retracts_old_one() -> None:
    metric = _metric()
    claim = _record(metric, CountValue(count=10), scope=ClaimScope(place_code="pk.gb"))

    correction = _correct(claim, metric, 14)

    old, new = correction.superseded.state, correction.replacement.state
    retracted, corrected = correction.events
    assert isinstance(retracted, ImpactClaimRetracted)
    assert isinstance(corrected, ImpactClaimCorrected)
    assert (old.status, old.value, old.retracted_by) == (
        ClaimStatus.RETRACTED,
        CountValue(count=10),
        new.recorded_by,
    )
    assert (
        new.status,
        new.value,
        new.supersedes_id,
        new.source_id,
        new.source_type,
        new.scope,
        new.event_id,
        new.confidence,
        new.claimed_at,
        new.version,
    ) == (
        ClaimStatus.ACTIVE,
        CountValue(count=14),
        claim.id,
        claim.source_id,
        claim.source_type,
        claim.scope,
        claim.event_id,
        claim.confidence,
        claim.claimed_at,
        1,
    )
    assert (retracted.superseded_by_id, corrected.aggregate_id) == (new.id, new.id)
    assert (corrected.supersedes_id, corrected.value) == (claim.id, new.value)


def test_impact_claim_correct_overrides_confidence_time_and_note() -> None:
    metric = _metric()
    claim = _record(metric, CountValue(count=10))
    later = DateWithPrecision(
        value=datetime(2022, 9, 1, tzinfo=UTC), precision=DatePrecision.EXACT
    )

    new = _correct(
        claim, metric, 9, confidence=Confidence.HIGH, claimed_at=later, note="final"
    ).replacement.state

    assert (new.confidence, new.claimed_at, new.note) == (
        Confidence.HIGH,
        later,
        "final",
    )


@given(counts=st.lists(st.integers(min_value=0, max_value=10**6), max_size=6))
def test_impact_claim_corrections_chain_with_only_the_last_claim_active(
    counts: list[int],
) -> None:
    metric = _metric()
    claims = [_record(metric, CountValue(count=1))]

    for count in counts:
        correction = _correct(claims[-1], metric, count)
        claims[-1] = correction.superseded.state
        claims.append(correction.replacement.state)

    assert [claim.is_active for claim in claims] == [False] * len(counts) + [True]
    assert [claim.supersedes_id for claim in claims[1:]] == [
        claim.id for claim in claims[:-1]
    ]


def test_impact_claim_correct_retracted_claim_raises_claim_immutable() -> None:
    metric = _metric()
    claim = _record(metric, CountValue(count=1))
    retracted = claim.retract(
        "gone", retracted_by=IDS.new_id(), clock=_clock(), id_generator=IDS
    ).state

    with pytest.raises(ClaimImmutableError):
        _correct(retracted, metric, 2)


def test_impact_claim_correct_under_another_metric_raises_mismatch() -> None:
    claim = _record(_metric(), CountValue(count=1))

    with pytest.raises(ClaimMetricMismatchError):
        _correct(claim, _metric(), 2)


def test_impact_claim_correct_under_retired_metric_raises_retired() -> None:
    metric = _metric()
    claim = _record(metric, CountValue(count=1))
    retired = metric.retire(
        RetirementReason(explanation="split"), clock=_clock(), id_generator=IDS
    ).state

    with pytest.raises(ImpactMetricRetiredError):
        _correct(claim, retired, 2)


def test_impact_claim_correct_value_of_another_kind_raises_kind_mismatch() -> None:
    metric = _metric()
    claim = _record(metric, CountValue(count=1))

    with pytest.raises(ClaimValueKindMismatchError):
        claim.correct(
            VALUES_BY_KIND[ValueKind.MONETARY],
            metric=metric,
            reason="wrong kind",
            recorded_by=IDS.new_id(),
            clock=_clock(),
            id_generator=IDS,
        )


def test_claim_correction_record_into_records_both_events_in_order() -> None:
    metric = _metric()
    correction = _correct(_record(metric, CountValue(count=1)), metric, 2)
    unit_of_work = InMemoryUnitOfWork()

    old, new = correction.record_into(unit_of_work)

    assert (old, new) == (
        correction.superseded.state,
        correction.replacement.state,
    )
    assert tuple(unit_of_work.collected_events) == correction.events


def test_claim_correction_replacement_not_superseding_old_claim_is_rejected() -> None:
    metric = _metric()
    correction = _correct(_record(metric, CountValue(count=1)), metric, 2)
    unrelated = _record(metric, CountValue(count=5))

    with pytest.raises(PydanticValidationError, match="supersede"):
        ClaimCorrection(
            superseded=correction.superseded,
            replacement=correction.replacement.model_copy(update={"state": unrelated}),
        )
