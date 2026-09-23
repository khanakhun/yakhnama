"""Unit tests for ``yakhnama.modules.impacts.domain.factories``."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError as PydanticValidationError

from yakhnama.modules.impacts.domain.errors import InconsistentMetricDefinitionError
from yakhnama.modules.impacts.domain.events import (
    ImpactMetricCreated,
    ImpactMetricRetired,
)
from yakhnama.modules.impacts.domain.factories import ImpactMetricFactory
from yakhnama.modules.impacts.domain.reference import ImpactMetricReferenceEntry
from yakhnama.modules.impacts.domain.value_objects import (
    DesInventarField,
    MetricCategory,
    MetricStatus,
    SendaiIndicator,
    ValueKind,
)
from yakhnama.shared_kernel.ids import is_uuid7
from yakhnama.shared_kernel.value_objects import LocalizedText

START = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
LABELS = LocalizedText(texts={"en": "Deaths"})


def test_impact_metric_factory_create_count_returns_active_metric_and_event(
    factory: ImpactMetricFactory,
) -> None:
    change = factory.create(
        code="deaths",
        labels=LABELS,
        category=MetricCategory.HUMAN,
        value_kind=ValueKind.COUNT,
        unit="count",
        sendai=SendaiIndicator(code="A-1"),
        desinventar=DesInventarField(name="DEATHS"),
    )

    metric, (event,) = change.state, change.events
    assert isinstance(event, ImpactMetricCreated)
    assert is_uuid7(metric.id)
    assert (metric.status, metric.version, metric.aggregation) == (
        MetricStatus.ACTIVE,
        1,
        "sum",
    )
    assert metric.created_at == metric.updated_at == START
    assert (event.aggregate_id, event.code, event.unit, event.occurred_at) == (
        metric.id,
        "deaths",
        "count",
        START,
    )
    assert event.event_id != metric.id


def test_impact_metric_factory_create_measurement_defaults_to_max(
    factory: ImpactMetricFactory,
) -> None:
    change = factory.create(
        code="farmland_flooded_area",
        labels=LocalizedText(texts={"en": "Farmland flooded"}),
        category=MetricCategory.AGRICULTURE,
        value_kind=ValueKind.MEASUREMENT,
        unit="square_metre",
    )

    assert change.state.aggregation == "max"


def test_impact_metric_factory_create_explicit_aggregation_is_kept(
    factory: ImpactMetricFactory,
) -> None:
    change = factory.create(
        code="direct_economic_loss",
        labels=LocalizedText(texts={"en": "Direct economic loss"}),
        category=MetricCategory.ECONOMIC,
        value_kind=ValueKind.MONETARY,
        currency="PKR",
        aggregation="latest",
    )

    assert (change.state.aggregation, change.state.currency) == ("latest", "PKR")


def test_impact_metric_factory_create_inconsistent_raises_inconsistent_error(
    factory: ImpactMetricFactory,
) -> None:
    with pytest.raises(
        InconsistentMetricDefinitionError, match="needs a currency"
    ) as caught:
        factory.create(
            code="direct_economic_loss",
            labels=LABELS,
            category=MetricCategory.ECONOMIC,
            value_kind=ValueKind.MONETARY,
        )

    assert caught.value.details == {
        "code": "direct_economic_loss",
        "value_kind": "monetary",
    }


def test_impact_metric_factory_create_malformed_code_raises_validation_error(
    factory: ImpactMetricFactory,
) -> None:
    with pytest.raises(PydanticValidationError):
        factory.create(
            code="Deaths",
            labels=LABELS,
            category=MetricCategory.HUMAN,
            value_kind=ValueKind.COUNT,
            unit="count",
        )


def _entry(**overrides: object) -> ImpactMetricReferenceEntry:
    fields: dict[str, object] = {
        "code": "deaths",
        "labels": {"en": "Deaths"},
        "category": "human",
        "value_kind": "count",
        "unit": "count",
        "aggregation": "sum",
        "source": "proposed",
    }
    fields.update(overrides)
    return ImpactMetricReferenceEntry.model_validate(fields)


def test_impact_metric_factory_create_from_active_entry_emits_created_only(
    factory: ImpactMetricFactory,
) -> None:
    entry = _entry(description={"en": "People confirmed dead."})

    change = factory.create_from_reference(entry)

    assert [type(event) for event in change.events] == [ImpactMetricCreated]
    assert (change.state.code, change.state.labels, change.state.description) == (
        "deaths",
        entry.labels,
        entry.description,
    )


def test_impact_metric_factory_create_from_retired_entry_emits_created_then_retired(
    factory: ImpactMetricFactory,
) -> None:
    entry = _entry(
        status="retired",
        retirement={"explanation": "split", "replaced_by": "deaths_direct"},
    )

    change = factory.create_from_reference(entry)

    assert [type(event) for event in change.events] == [
        ImpactMetricCreated,
        ImpactMetricRetired,
    ]
    assert (change.state.status, change.state.retirement, change.state.version) == (
        MetricStatus.RETIRED,
        entry.retirement,
        2,
    )
