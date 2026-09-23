"""``data/reference/impact_metrics.yaml`` against ``ImpactMetricReferenceFile``."""

import pytest

from tests.unit.data.reference_files import load_reference
from yakhnama.modules.impacts.domain.reference import ImpactMetricReferenceFile
from yakhnama.modules.impacts.domain.value_objects import (
    COUNT_UNIT,
    REQUIRED_LABEL_LANGUAGE,
    MetricStatus,
    ValueKind,
    default_aggregation,
)
from yakhnama.shared_kernel.value_objects import is_known_unit

FILE_NAME = "impact_metrics.yaml"


@pytest.fixture(scope="module")
def reference() -> ImpactMetricReferenceFile:
    """Return the validated impact metric file."""
    return ImpactMetricReferenceFile.model_validate(load_reference(FILE_NAME))


def test_impact_metrics_yaml_validates_and_round_trips_equal(
    reference: ImpactMetricReferenceFile,
) -> None:
    dumped = reference.model_dump(mode="json")

    result = ImpactMetricReferenceFile.model_validate(dumped)

    assert result == reference
    assert reference.entries


def test_impact_metrics_yaml_count_metrics_use_unit_count(
    reference: ImpactMetricReferenceFile,
) -> None:
    result = sorted(
        entry.code
        for entry in reference.entries
        if entry.value_kind is ValueKind.COUNT and entry.unit != COUNT_UNIT
    )

    assert result == []


def test_impact_metrics_yaml_has_no_monetary_metric(
    reference: ImpactMetricReferenceFile,
) -> None:
    result = sorted(
        entry.code
        for entry in reference.entries
        if entry.value_kind is ValueKind.MONETARY or entry.currency is not None
    )

    assert result == []


def test_impact_metrics_yaml_units_are_registered(
    reference: ImpactMetricReferenceFile,
) -> None:
    result = sorted(
        entry.code
        for entry in reference.entries
        if entry.unit is None or not is_known_unit(entry.unit)
    )

    assert result == []


def test_impact_metrics_yaml_aggregation_follows_proposed_default(
    reference: ImpactMetricReferenceFile,
) -> None:
    result = sorted(
        entry.code
        for entry in reference.entries
        if entry.aggregation != default_aggregation(entry.value_kind)
    )

    assert result == []


def test_impact_metrics_yaml_non_english_labels_have_a_cited_source(
    reference: ImpactMetricReferenceFile,
) -> None:
    unsourced = sorted(
        entry.code
        for entry in reference.entries
        if set(entry.labels.texts) - {REQUIRED_LABEL_LANGUAGE}
        and entry.source == "proposed"
    )

    assert unsourced == []


def test_impact_metrics_yaml_every_entry_is_active_with_notes(
    reference: ImpactMetricReferenceFile,
) -> None:
    result = sorted(
        entry.code
        for entry in reference.entries
        if entry.status is not MetricStatus.ACTIVE or entry.notes is None
    )

    assert result == []


def test_impact_metrics_yaml_cropland_affected_is_square_metre_measurement(
    reference: ImpactMetricReferenceFile,
) -> None:
    entries = {entry.code: entry for entry in reference.entries}

    cropland = entries["cropland_affected"]

    assert cropland.value_kind is ValueKind.MEASUREMENT
    assert cropland.unit == "square_metre"
