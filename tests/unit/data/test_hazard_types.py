"""``data/reference/hazard_types.yaml`` against ``HazardTypeReferenceFile``."""

from collections import Counter

import pytest

from tests.unit.data.reference_files import load_reference
from yakhnama.modules.hazards.domain.attributes import DEFAULT_REGISTRY
from yakhnama.modules.hazards.domain.reference import (
    ENGLISH,
    HazardTypeReferenceFile,
)
from yakhnama.modules.hazards.domain.value_objects import HazardTypeStatus

FILE_NAME = "hazard_types.yaml"


@pytest.fixture(scope="module")
def reference() -> HazardTypeReferenceFile:
    """Return the validated hazard taxonomy file."""
    return HazardTypeReferenceFile.model_validate(load_reference(FILE_NAME))


def test_hazard_types_yaml_validates_and_round_trips_equal(
    reference: HazardTypeReferenceFile,
) -> None:
    dumped = reference.model_dump(mode="json")

    result = HazardTypeReferenceFile.model_validate(dumped)

    assert result == reference


def test_hazard_types_yaml_attribute_schemas_match_registry_exactly(
    reference: HazardTypeReferenceFile,
) -> None:
    schemas = Counter(
        entry.attributes_schema
        for entry in reference.entries
        if entry.attributes_schema is not None
    )

    result = frozenset(schemas)

    assert result == DEFAULT_REGISTRY.codes()
    assert all(count == 1 for count in schemas.values())


def test_hazard_types_yaml_attribute_bearing_types_are_leaves(
    reference: HazardTypeReferenceFile,
) -> None:
    parents = {entry.parent for entry in reference.entries if entry.parent}

    bearing_parents = sorted(
        entry.code
        for entry in reference.entries
        if entry.attributes_schema is not None and entry.code in parents
    )

    assert bearing_parents == []


def test_hazard_types_yaml_attribute_schema_names_its_own_code(
    reference: HazardTypeReferenceFile,
) -> None:
    mismatched = sorted(
        entry.code
        for entry in reference.entries
        if entry.attributes_schema is not None and entry.attributes_schema != entry.code
    )

    assert mismatched == []


def test_hazard_types_yaml_non_english_labels_have_a_cited_source(
    reference: HazardTypeReferenceFile,
) -> None:
    unsourced = sorted(
        entry.code
        for entry in reference.entries
        if set(entry.labels) - {ENGLISH} and entry.source == "proposed"
    )

    assert unsourced == []


def test_hazard_types_yaml_every_entry_is_active_with_notes(
    reference: HazardTypeReferenceFile,
) -> None:
    result = [
        entry.code
        for entry in reference.entries
        if entry.status is not HazardTypeStatus.ACTIVE or entry.notes is None
    ]

    assert result == []
