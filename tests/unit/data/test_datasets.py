"""``data/reference/datasets.yaml`` against ``DatasetReferenceFile``."""

import pytest

from tests.unit.data.reference_files import REFERENCE_DIRECTORY, load_reference
from yakhnama.modules.ingestion.domain.reference import DatasetReferenceFile

FILE_NAME = "datasets.yaml"
FIXTURE_FILE = REFERENCE_DIRECTORY.parent / "fixtures" / "ingestion"


@pytest.fixture(scope="module")
def reference() -> DatasetReferenceFile:
    """Return the validated dataset catalog file."""
    return DatasetReferenceFile.model_validate(load_reference(FILE_NAME))


def test_datasets_yaml_validates_and_round_trips_equal(
    reference: DatasetReferenceFile,
) -> None:
    dumped = reference.model_dump(mode="json")

    result = DatasetReferenceFile.model_validate(dumped)

    assert result == reference


def test_datasets_yaml_every_entry_has_licence_with_attribution(
    reference: DatasetReferenceFile,
) -> None:
    result = [
        entry.code for entry in reference.datasets if not entry.licence.attribution
    ]

    assert reference.datasets
    assert result == []


def test_datasets_yaml_temperature_fixture_is_marked_synthetic_cc0(
    reference: DatasetReferenceFile,
) -> None:
    entry = reference.find("fixture.temperature_sample")

    assert entry is not None
    assert entry.is_fixture
    assert entry.licence.spdx_id == "CC0-1.0"
    assert entry.update_frequency == "static"
    assert entry.source == "synthetic fixture"
    assert (FIXTURE_FILE / "temperature_sample.csv").is_file()


def test_datasets_yaml_fixture_entries_use_the_fixture_code_prefix(
    reference: DatasetReferenceFile,
) -> None:
    result = sorted(
        entry.code
        for entry in reference.datasets
        if entry.is_fixture != entry.code.startswith("fixture.")
    )

    assert result == []
