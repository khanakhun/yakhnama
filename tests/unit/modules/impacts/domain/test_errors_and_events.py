"""Unit tests for ``yakhnama.modules.impacts.domain.errors`` and ``events``."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from yakhnama.modules.impacts.domain.errors import (
    AssetNotFoundError,
    ClaimImmutableError,
    ClaimMetricMismatchError,
    ClaimValueKindMismatchError,
    ClaimValueUnitMismatchError,
    DamageRecordNotFoundError,
    ImpactClaimNotFoundError,
    ImpactMetricNotFoundError,
    ImpactMetricRetiredError,
    InconsistentMetricDefinitionError,
    MetricCodeAlreadyUsedError,
)
from yakhnama.modules.impacts.domain.events import (
    DamageRecorded,
    DamageRetracted,
    ImpactClaimCorrected,
    ImpactClaimRecorded,
    ImpactClaimRetracted,
    ImpactMetricCreated,
    ImpactMetricRelabelled,
    ImpactMetricRetired,
    InfrastructureAssetRegistered,
    InfrastructureAssetRelocated,
    InfrastructureAssetRenamed,
)
from yakhnama.modules.impacts.domain.value_objects import (
    ClaimScope,
    MetricCategory,
    MonetaryValue,
    ValueKind,
)
from yakhnama.shared_kernel.errors import (
    ConflictError,
    InvalidTransitionError,
    InvariantViolationError,
    NotFoundError,
    ValidationError,
    YakhnamaError,
)
from yakhnama.shared_kernel.events import DomainEvent
from yakhnama.shared_kernel.ids import Uuid7Generator
from yakhnama.shared_kernel.value_objects import (
    Confidence,
    DatePrecision,
    DateWithPrecision,
)

IDS = Uuid7Generator()
START = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("error_type", "family", "code"),
    [
        (ImpactMetricNotFoundError, NotFoundError, "impact_metric_not_found"),
        (MetricCodeAlreadyUsedError, ConflictError, "metric_code_already_used"),
        (ImpactMetricRetiredError, InvalidTransitionError, "impact_metric_retired"),
        (
            InconsistentMetricDefinitionError,
            InvariantViolationError,
            "inconsistent_metric_definition",
        ),
    ],
)
def test_impacts_error_raised_is_caught_as_kernel_family_with_own_code(
    error_type: type[YakhnamaError], family: type[YakhnamaError], code: str
) -> None:
    message = "boom"

    with pytest.raises(family) as caught:
        raise error_type(message)

    assert (caught.value.code, str(caught.value)) == (code, "boom")


@pytest.mark.parametrize(
    ("event_type", "expected"),
    [
        (ImpactMetricCreated, "impacts.impact_metric_created"),
        (ImpactMetricRetired, "impacts.impact_metric_retired"),
        (ImpactMetricRelabelled, "impacts.impact_metric_relabelled"),
    ],
)
def test_impacts_event_types_follow_the_routing_convention(
    event_type: type[ImpactMetricCreated], expected: str
) -> None:
    assert event_type.event_type == expected


def test_impact_metric_created_json_round_trip_keeps_payload() -> None:
    event = ImpactMetricCreated(
        event_id=IDS.new_id(),
        occurred_at=START,
        aggregate_id=IDS.new_id(),
        code="deaths",
        category=MetricCategory.HUMAN,
        value_kind=ValueKind.COUNT,
        unit="count",
        currency=None,
    )

    restored = ImpactMetricCreated.model_validate_json(event.model_dump_json())

    assert restored == event
    assert restored.aggregate_type == "impact_metric"


@pytest.mark.parametrize(
    ("error_type", "family", "code"),
    [
        (ImpactClaimNotFoundError, NotFoundError, "impact_claim_not_found"),
        (ClaimImmutableError, InvalidTransitionError, "claim_immutable"),
        (ClaimValueKindMismatchError, ValidationError, "claim_value_kind_mismatch"),
        (ClaimValueUnitMismatchError, ValidationError, "claim_value_unit_mismatch"),
        (ClaimMetricMismatchError, ValidationError, "claim_metric_mismatch"),
        (AssetNotFoundError, NotFoundError, "asset_not_found"),
        (DamageRecordNotFoundError, NotFoundError, "damage_record_not_found"),
    ],
)
def test_impacts_recording_error_is_caught_as_kernel_family_with_own_code(
    error_type: type[YakhnamaError], family: type[YakhnamaError], code: str
) -> None:
    message = "boom"

    with pytest.raises(family) as caught:
        raise error_type(message)

    assert (caught.value.code, str(caught.value)) == (code, "boom")


@pytest.mark.parametrize(
    ("event_type", "expected"),
    [
        (ImpactClaimRecorded, "impacts.impact_claim_recorded"),
        (ImpactClaimRetracted, "impacts.impact_claim_retracted"),
        (ImpactClaimCorrected, "impacts.impact_claim_corrected"),
        (InfrastructureAssetRegistered, "impacts.infrastructure_asset_registered"),
        (InfrastructureAssetRenamed, "impacts.infrastructure_asset_renamed"),
        (InfrastructureAssetRelocated, "impacts.infrastructure_asset_relocated"),
        (DamageRecorded, "impacts.damage_recorded"),
        (DamageRetracted, "impacts.damage_retracted"),
    ],
)
def test_impacts_recording_event_types_follow_the_routing_convention(
    event_type: type[DomainEvent], expected: str
) -> None:
    assert event_type.event_type == expected


def test_impact_claim_recorded_json_round_trip_keeps_discriminated_value() -> None:
    event = ImpactClaimRecorded(
        event_id=IDS.new_id(),
        occurred_at=START,
        aggregate_id=IDS.new_id(),
        hazard_event_id=IDS.new_id(),
        metric_code="economic_loss",
        value=MonetaryValue(amount=Decimal("10.50"), currency="PKR", price_year=2022),
        confidence=Confidence.LOW,
        source_id=IDS.new_id(),
        source_type="news",
        claimed_at=DateWithPrecision(value=START, precision=DatePrecision.DAY),
        scope=ClaimScope(),
        recorded_by=IDS.new_id(),
    )

    restored = ImpactClaimRecorded.model_validate_json(event.model_dump_json())

    assert (restored, restored.aggregate_type) == (event, "impact_claim")
