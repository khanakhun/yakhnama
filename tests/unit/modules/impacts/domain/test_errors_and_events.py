"""Unit tests for ``yakhnama.modules.impacts.domain.errors`` and ``events``."""

from datetime import UTC, datetime

import pytest

from yakhnama.modules.impacts.domain.errors import (
    ImpactMetricNotFoundError,
    ImpactMetricRetiredError,
    InconsistentMetricDefinitionError,
    MetricCodeAlreadyUsedError,
)
from yakhnama.modules.impacts.domain.events import (
    ImpactMetricCreated,
    ImpactMetricRelabelled,
    ImpactMetricRetired,
)
from yakhnama.modules.impacts.domain.value_objects import MetricCategory, ValueKind
from yakhnama.shared_kernel.errors import (
    ConflictError,
    InvalidTransitionError,
    InvariantViolationError,
    NotFoundError,
    YakhnamaError,
)
from yakhnama.shared_kernel.ids import Uuid7Generator

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
