"""Unit tests for ``YamlReferenceFileReader`` over files in a temporary directory.

The only I/O is reading files the test itself wrote under ``tmp_path`` (and copies
of the repository's reference files); nothing touches a database or the network.
"""

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest
import yaml

from tests.fakes.seed import REFERENCE_DIRECTORY
from yakhnama.modules.geography.public import PlaceReferenceFile
from yakhnama.modules.hazards.public import HazardTypeReferenceFile
from yakhnama.modules.impacts.public import ImpactMetricReferenceFile
from yakhnama.seed.infrastructure import (
    HAZARD_TYPES_FILE,
    IMPACT_METRICS_FILE,
    PLACES_FILE,
    YamlReferenceFileReader,
)
from yakhnama.shared_kernel.errors import ValidationError

# A marker that must never appear in an error: it stands for file content.
CONTENT_MARKER: Final = "marker-from-file-content-4f1c"


@pytest.fixture
def reference_dir(tmp_path: Path) -> Path:
    """Return a temporary copy of the repository's reference files."""
    directory = tmp_path / "reference"
    directory.mkdir()
    for name in (HAZARD_TYPES_FILE, IMPACT_METRICS_FILE, PLACES_FILE):
        shutil.copyfile(REFERENCE_DIRECTORY / name, directory / name)
    return directory


def _error_text(error: ValidationError) -> str:
    return f"{error.message} {dict(error.details)!r}"


def test_reader_valid_files_return_validated_models(reference_dir: Path) -> None:
    reader = YamlReferenceFileReader(reference_dir)

    hazard_types = reader.read_hazard_types()
    impact_metrics = reader.read_impact_metrics()
    places = reader.read_places()

    assert isinstance(hazard_types, HazardTypeReferenceFile)
    assert isinstance(impact_metrics, ImpactMetricReferenceFile)
    assert isinstance(places, PlaceReferenceFile)
    assert hazard_types.entries
    assert impact_metrics.entries
    assert places.entries


def test_reader_directory_returns_configured_path(tmp_path: Path) -> None:
    reader = YamlReferenceFileReader(tmp_path)

    directory = reader.directory

    assert directory == tmp_path


def test_reader_construction_with_missing_directory_does_no_io(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    reader = YamlReferenceFileReader(missing)

    assert reader.directory == missing


@pytest.mark.parametrize(
    ("read", "file_name"),
    [
        (YamlReferenceFileReader.read_hazard_types, HAZARD_TYPES_FILE),
        (YamlReferenceFileReader.read_impact_metrics, IMPACT_METRICS_FILE),
        (YamlReferenceFileReader.read_places, PLACES_FILE),
    ],
)
def test_reader_missing_file_raises_validation_error_naming_file(
    tmp_path: Path,
    read: Callable[[YamlReferenceFileReader], object],
    file_name: str,
) -> None:
    reader = YamlReferenceFileReader(tmp_path)

    with pytest.raises(ValidationError) as caught:
        read(reader)

    assert caught.value.details == {"file": file_name, "reason": "not_found"}
    assert str(tmp_path) not in _error_text(caught.value)


def test_reader_corrupt_yaml_raises_validation_error_without_content(
    reference_dir: Path,
) -> None:
    (reference_dir / HAZARD_TYPES_FILE).write_text(
        f"schema_version: 1\nentries: [{CONTENT_MARKER}\n", encoding="utf-8"
    )
    reader = YamlReferenceFileReader(reference_dir)

    with pytest.raises(ValidationError) as caught:
        reader.read_hazard_types()

    assert caught.value.details["file"] == HAZARD_TYPES_FILE
    assert caught.value.details["reason"] == "not_yaml"
    assert isinstance(caught.value.details["line"], int)
    assert CONTENT_MARKER not in _error_text(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


def test_reader_unsafe_yaml_tag_is_rejected_not_constructed(
    reference_dir: Path,
) -> None:
    (reference_dir / PLACES_FILE).write_text(
        f"!!python/object/apply:os.system ['{CONTENT_MARKER}']\n", encoding="utf-8"
    )
    reader = YamlReferenceFileReader(reference_dir)

    with pytest.raises(ValidationError) as caught:
        reader.read_places()

    assert caught.value.details["reason"] == "not_yaml"
    assert CONTENT_MARKER not in _error_text(caught.value)


def test_reader_yaml_error_without_mark_raises_validation_error(
    reference_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_safe_load(stream: str) -> object:
        raise yaml.YAMLError(stream)

    monkeypatch.setattr(yaml, "safe_load", failing_safe_load)
    reader = YamlReferenceFileReader(reference_dir)

    with pytest.raises(ValidationError) as caught:
        reader.read_impact_metrics()

    assert caught.value.details == {"file": IMPACT_METRICS_FILE, "reason": "not_yaml"}


def test_reader_marked_yaml_error_without_mark_has_no_line(
    reference_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_safe_load(stream: str) -> object:
        del stream
        raise yaml.MarkedYAMLError(problem="no mark")

    monkeypatch.setattr(yaml, "safe_load", failing_safe_load)
    reader = YamlReferenceFileReader(reference_dir)

    with pytest.raises(ValidationError) as caught:
        reader.read_impact_metrics()

    assert caught.value.details["line"] is None


def test_reader_schema_mismatch_raises_validation_error_with_types_only(
    reference_dir: Path,
) -> None:
    (reference_dir / IMPACT_METRICS_FILE).write_text(
        f"schema_version: 1\ndata_version: '{CONTENT_MARKER}'\nunknown: x\n",
        encoding="utf-8",
    )
    reader = YamlReferenceFileReader(reference_dir)

    with pytest.raises(ValidationError) as caught:
        reader.read_impact_metrics()

    details = caught.value.details
    assert details["file"] == IMPACT_METRICS_FILE
    assert details["reason"] == "invalid"
    assert isinstance(details["error_count"], int)
    assert details["error_count"] >= 1
    assert isinstance(details["error_types"], tuple)
    assert "missing" in details["error_types"]
    assert CONTENT_MARKER not in _error_text(caught.value)
    assert caught.value.__cause__ is None


def test_reader_empty_file_raises_validation_error_invalid(
    reference_dir: Path,
) -> None:
    (reference_dir / PLACES_FILE).write_text("", encoding="utf-8")
    reader = YamlReferenceFileReader(reference_dir)

    with pytest.raises(ValidationError) as caught:
        reader.read_places()

    assert caught.value.details["reason"] == "invalid"


def test_reader_non_utf8_file_raises_validation_error_unreadable(
    reference_dir: Path,
) -> None:
    (reference_dir / HAZARD_TYPES_FILE).write_bytes(b"\xff\xfe\xfa" + b"x" * 8)
    reader = YamlReferenceFileReader(reference_dir)

    with pytest.raises(ValidationError) as caught:
        reader.read_hazard_types()

    assert caught.value.details == {
        "file": HAZARD_TYPES_FILE,
        "reason": "unreadable",
        "error_type": "UnicodeDecodeError",
    }


def test_reader_directory_in_place_of_file_raises_validation_error_unreadable(
    tmp_path: Path,
) -> None:
    (tmp_path / PLACES_FILE).mkdir()
    reader = YamlReferenceFileReader(tmp_path)

    with pytest.raises(ValidationError) as caught:
        reader.read_places()

    assert caught.value.details["reason"] == "unreadable"
    assert caught.value.details["error_type"] == "IsADirectoryError"
    assert str(tmp_path) not in _error_text(caught.value)
