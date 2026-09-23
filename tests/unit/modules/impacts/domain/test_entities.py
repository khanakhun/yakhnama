"""Unit tests for ``yakhnama.modules.impacts.domain.entities``."""

import re
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError as PydanticValidationError

from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.errors import ImpactMetricRetiredError
from yakhnama.modules.impacts.domain.events import (
    ImpactMetricRelabelled,
    ImpactMetricRetired,
)
from yakhnama.modules.impacts.domain.value_objects import (
    ImpactMetricRef,
    MetricCategory,
    MetricStatus,
    RetirementReason,
    ValueKind,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.ids import Uuid7Generator
from yakhnama.shared_kernel.value_objects import LocalizedText

IDS = Uuid7Generator()
START = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
LABELS = LocalizedText(texts={"en": "Deaths"})
REASON = RetirementReason(explanation="split by cause", replaced_by="deaths_direct")


def _fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "id": IDS.new_id(),
        "code": "deaths",
        "labels": LABELS,
        "category": MetricCategory.HUMAN,
        "value_kind": ValueKind.COUNT,
        "unit": "count",
        "aggregation": "sum",
        "created_at": START,
        "updated_at": START,
    }
    fields.update(overrides)
    return fields


def _metric(**overrides: object) -> ImpactMetric:
    return ImpactMetric.model_validate(_fields(**overrides))


def test_impact_metric_minimal_fields_defaults_to_active_version_one() -> None:
    metric = _metric()

    assert (metric.status, metric.version, metric.retirement, metric.is_active) == (
        MetricStatus.ACTIVE,
        1,
        None,
        True,
    )


def test_impact_metric_offset_timestamps_are_normalised_to_utc() -> None:
    local = START.astimezone(timezone(timedelta(hours=5)))

    metric = _metric(created_at=local, updated_at=local)

    assert metric.created_at.utcoffset() == timedelta(0)


def test_impact_metric_ref_returns_code_reference() -> None:
    metric = _metric()

    ref = metric.ref

    assert ref == ImpactMetricRef(code="deaths")


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"unit": "metre"}, "a count metric must use unit 'count'"),
        ({"value_kind": ValueKind.MEASUREMENT}, "must not use unit 'count'"),
        ({"value_kind": ValueKind.MEASUREMENT, "unit": None}, "needs a unit"),
        ({"value_kind": ValueKind.MONETARY}, "carries a currency, not a unit"),
        ({"currency": "PKR"}, "must not carry a currency"),
        ({"labels": LocalizedText(texts={"ur": "اموات"})}, "English ('en')"),
        ({"status": MetricStatus.RETIRED}, "required exactly when retired"),
        ({"retirement": REASON}, "required exactly when retired"),
        (
            {
                "status": MetricStatus.RETIRED,
                "retirement": RetirementReason(explanation="x", replaced_by="deaths"),
            },
            "replaced by itself",
        ),
        ({"updated_at": START - timedelta(seconds=1)}, "earlier than created_at"),
    ],
)
def test_impact_metric_broken_invariant_raises_validation_error(
    overrides: dict[str, object], fragment: str
) -> None:
    fields = _fields(**overrides)

    with pytest.raises(PydanticValidationError, match=re.escape(fragment)):
        ImpactMetric.model_validate(fields)


@pytest.mark.parametrize(
    "overrides",
    [
        {"unit": "furlong", "value_kind": ValueKind.MEASUREMENT},
        {"currency": "pkr", "value_kind": ValueKind.MONETARY, "unit": None},
        {"version": 0},
        {"aggregation": "mean"},
        {"code": "Deaths"},
    ],
)
def test_impact_metric_malformed_field_raises_validation_error(
    overrides: dict[str, object],
) -> None:
    fields = _fields(**overrides)

    with pytest.raises(PydanticValidationError):
        ImpactMetric.model_validate(fields)


def test_impact_metric_monetary_with_currency_is_valid() -> None:
    metric = _metric(
        code="direct_economic_loss",
        category=MetricCategory.ECONOMIC,
        value_kind=ValueKind.MONETARY,
        unit=None,
        currency="PKR",
    )

    assert (metric.unit, metric.currency) == (None, "PKR")


@pytest.mark.parametrize(
    ("value_kind", "unit", "value", "expected"),
    [
        (ValueKind.COUNT, "count", 3.0, True),
        (ValueKind.COUNT, "count", 3.5, False),
        (ValueKind.COUNT, "count", -1.0, False),
        (ValueKind.MEASUREMENT, "square_metre", 3.5, True),
        (ValueKind.MEASUREMENT, "square_metre", -0.5, False),
    ],
)
def test_impact_metric_is_valid_value_counts_versus_measurements(
    value_kind: ValueKind, unit: str, value: float, *, expected: bool
) -> None:
    metric = _metric(value_kind=value_kind, unit=unit)

    result = metric.is_valid_value(value)

    assert result is expected


def test_impact_metric_retire_active_returns_retired_state_and_event(
    clock: Clock,
) -> None:
    metric = _metric()

    change = metric.retire(REASON, clock=clock, id_generator=IDS)

    event = change.events[0]
    assert isinstance(event, ImpactMetricRetired)
    assert (change.state.status, change.state.retirement, change.state.version) == (
        MetricStatus.RETIRED,
        REASON,
        2,
    )
    assert change.state.updated_at == START
    assert (event.code, event.reason, event.aggregate_id) == (
        "deaths",
        REASON,
        metric.id,
    )
    assert (event.aggregate_type, event.occurred_at) == (
        "impact_metric",
        change.state.updated_at,
    )
    assert metric.status is MetricStatus.ACTIVE


def test_impact_metric_retire_retired_raises_impact_metric_retired_error(
    clock: Clock,
) -> None:
    retired = _metric().retire(REASON, clock=clock, id_generator=IDS).state

    with pytest.raises(ImpactMetricRetiredError, match="cannot retire") as caught:
        retired.retire(REASON, clock=clock, id_generator=IDS)

    assert caught.value.details == {"code": "deaths"}


def test_impact_metric_relabel_new_labels_returns_state_and_event(clock: Clock) -> None:
    metric = _metric()
    labels = LocalizedText(texts={"en": "People killed", "ur": "ہلاکتیں"})

    change = metric.relabel(labels, clock=clock, id_generator=IDS)

    event = change.events[0]
    assert isinstance(event, ImpactMetricRelabelled)
    assert (change.state.labels, change.state.version, change.state.code) == (
        labels,
        2,
        "deaths",
    )
    assert (event.labels, event.code) == (labels, "deaths")


def test_impact_metric_relabel_same_labels_returns_unchanged_without_event(
    clock: Clock,
) -> None:
    metric = _metric()

    change = metric.relabel(
        LocalizedText(texts={"en": "Deaths"}), clock=clock, id_generator=IDS
    )

    assert (change.state, change.events) == (metric, ())


def test_impact_metric_relabel_without_english_raises_validation_error(
    clock: Clock,
) -> None:
    metric = _metric()

    with pytest.raises(PydanticValidationError, match="English"):
        metric.relabel(
            LocalizedText(texts={"ur": "اموات"}), clock=clock, id_generator=IDS
        )


def test_impact_metric_relabel_retired_raises_impact_metric_retired_error(
    clock: Clock,
) -> None:
    retired = _metric().retire(REASON, clock=clock, id_generator=IDS).state

    with pytest.raises(ImpactMetricRetiredError, match="cannot relabel"):
        retired.relabel(
            LocalizedText(texts={"en": "Killed"}),
            clock=clock,
            id_generator=IDS,
        )


def test_impact_metric_is_frozen() -> None:
    metric = _metric()

    with pytest.raises(PydanticValidationError):
        metric.code = "other"  # type: ignore[misc]  # reason: proving immutability
