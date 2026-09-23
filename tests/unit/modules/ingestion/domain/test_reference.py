"""Unit tests for ``yakhnama.modules.ingestion.domain.reference``."""

import pytest
from pydantic import ValidationError as PydanticValidationError

from yakhnama.modules.ingestion.domain.reference import (
    DatasetReferenceEntry,
    DatasetReferenceFile,
)
from yakhnama.modules.ingestion.domain.value_objects import (
    DatasetStatus,
    UpdateFrequency,
)

# Shaped like ``yaml.safe_load`` output: the untyped structure the model validates.
ENTRY: dict[str, object] = {
    "code": "test_temperature_sample",
    "title": "Synthetic temperature sample",
    "publisher": "Yakhnama test fixtures",
    "licence": {
        "spdx_id": "CC0-1.0",
        "url": "https://licences.example.test/cc0",
        "attribution": "Synthetic data generated for Yakhnama tests.",
    },
    "update_frequency": "static",
    "spatial_coverage": {
        "bbox": {
            "min_longitude": 72.0,
            "min_latitude": 34.0,
            "max_longitude": 78.0,
            "max_latitude": 37.5,
        }
    },
    "temporal_coverage": {
        "start": {"value": "2024-01-01T00:00:00Z", "precision": "day"},
        "end": {"value": "2024-01-31T00:00:00Z", "precision": "day"},
    },
    "is_fixture": True,
    "source": "synthetic fixture",
}


def test_dataset_reference_entry_to_details_carries_licence_and_coverage() -> None:
    entry = DatasetReferenceEntry.model_validate(ENTRY)

    details = entry.to_details()

    assert details.code == "test_temperature_sample"
    assert details.licence == entry.licence
    assert details.update_frequency is UpdateFrequency.STATIC
    assert details.spatial_coverage == entry.spatial_coverage
    assert details.temporal_coverage == entry.temporal_coverage
    assert entry.status is DatasetStatus.ACTIVE
    assert entry.is_fixture is True


def test_dataset_reference_entry_without_licence_is_rejected() -> None:
    fields = {key: value for key, value in ENTRY.items() if key != "licence"}

    with pytest.raises(PydanticValidationError, match="licence"):
        DatasetReferenceEntry.model_validate(fields)


def test_dataset_reference_entry_unknown_field_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        DatasetReferenceEntry.model_validate({**ENTRY, "licence_url": "x"})


def test_dataset_reference_file_find_returns_entry_or_none() -> None:
    reference = DatasetReferenceFile.model_validate(
        {"schema_version": 1, "datasets": [ENTRY]}
    )

    found = reference.find("test_temperature_sample")
    missing = reference.find("absent")

    assert found is not None
    assert found.code == "test_temperature_sample"
    assert missing is None


def test_dataset_reference_file_duplicate_codes_are_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="duplicate dataset codes"):
        DatasetReferenceFile.model_validate(
            {"schema_version": 1, "datasets": [ENTRY, ENTRY]}
        )


def test_dataset_reference_file_unknown_schema_version_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        DatasetReferenceFile.model_validate({"schema_version": 2, "datasets": []})


def test_dataset_reference_file_empty_is_accepted() -> None:
    reference = DatasetReferenceFile.model_validate({"schema_version": 1})

    assert reference.datasets == ()
