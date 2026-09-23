"""Unit tests for ``yakhnama.modules.hazards.domain.reference``."""

import pytest
from pydantic import ValidationError as PydanticValidationError

from yakhnama.modules.hazards.domain.reference import (
    HazardTypeReferenceEntry,
    HazardTypeReferenceFile,
)
from yakhnama.modules.hazards.domain.value_objects import HazardTypeStatus
from yakhnama.shared_kernel.value_objects import LocalizedText


def _entry(
    code: str, parent: str | None = None, **overrides: object
) -> dict[str, object]:
    entry: dict[str, object] = {
        "code": code,
        "parent": parent,
        "labels": {"en": code.replace("_", " ").title()},
        "alignment": {"family": "hydrological", "main_event": "Flood"},
        "source": "proposed",
    }
    entry.update(overrides)
    return entry


def _file(*entries: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "data_version": "2026.09.0",
        "source": "Yakhnama maintainers (proposed)",
        "licence": "CC-BY-4.0",
        "entries": list(entries),
    }


def test_reference_file_valid_tree_validates_and_lists_codes() -> None:
    raw = _file(
        _entry("flood"),
        _entry("flash_flood", "flood", attributes_schema="flash_flood"),
        _entry(
            "old_flood",
            "flood",
            status="retired",
            retirement={"text": "merged", "replaced_by": "flash_flood"},
        ),
    )

    reference = HazardTypeReferenceFile.model_validate(raw)

    assert reference.codes() == {"flood", "flash_flood", "old_flood"}
    assert reference.entries[2].status is HazardTypeStatus.RETIRED


def test_reference_file_duplicate_code_raises_validation_error() -> None:
    raw = _file(_entry("flood"), _entry("flood"))

    with pytest.raises(PydanticValidationError, match="duplicate hazard codes"):
        HazardTypeReferenceFile.model_validate(raw)


def test_reference_file_missing_parent_raises_validation_error() -> None:
    raw = _file(_entry("glof", "flash_flood"))

    with pytest.raises(PydanticValidationError, match="parent missing"):
        HazardTypeReferenceFile.model_validate(raw)


def test_reference_file_missing_replacement_raises_validation_error() -> None:
    raw = _file(
        _entry(
            "old_flood",
            status="retired",
            retirement={"text": "merged", "replaced_by": "flash_flood"},
        )
    )

    with pytest.raises(PydanticValidationError, match="replaced by a code missing"):
        HazardTypeReferenceFile.model_validate(raw)


def test_reference_file_parent_cycle_raises_validation_error() -> None:
    raw = _file(_entry("aa", "bb"), _entry("bb", "aa"))

    with pytest.raises(PydanticValidationError, match="cycle"):
        HazardTypeReferenceFile.model_validate(raw)


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": 2},
        {"entries": []},
        {"licence": ""},
        {"data_version": "x" * 33},
        {"generated_by": "script"},
    ],
)
def test_reference_file_invalid_header_raises_validation_error(
    overrides: dict[str, object],
) -> None:
    raw = _file(_entry("flood")) | overrides

    with pytest.raises(PydanticValidationError):
        HazardTypeReferenceFile.model_validate(raw)


@pytest.mark.parametrize(
    "overrides",
    [
        {"labels": {"ur": "سیلاب"}},
        {"labels": {}},
        {"parent": "flood"},
        {"status": "retired"},
        {"retirement": {"text": "merged"}},
        {"status": "retired", "retirement": {"text": "x", "replaced_by": "flood"}},
        {"source": ""},
        {"notes": "x" * 2001},
        {"irdr": {"family": "hydrological", "main_event": "Flood"}},
        {"labels": {"english": "Flood"}},
    ],
)
def test_reference_entry_broken_rule_raises_validation_error(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(PydanticValidationError):
        HazardTypeReferenceEntry.model_validate(_entry("flood") | overrides)


def test_reference_entry_localized_labels_and_description_convert_to_kernel() -> None:
    entry = HazardTypeReferenceEntry.model_validate(
        _entry(
            "flood",
            labels={"en": "Flood"},
            description={"en": "Water over normally dry land."},
            notes="EXAMPLE ONLY",
        )
    )

    labels = entry.localized_labels()
    description = entry.localized_description()

    assert labels == LocalizedText(texts={"en": "Flood"})
    assert description == LocalizedText(texts={"en": "Water over normally dry land."})


def test_reference_entry_without_description_returns_none() -> None:
    entry = HazardTypeReferenceEntry.model_validate(_entry("flood"))

    description = entry.localized_description()

    assert description is None


def test_reference_entry_labels_are_read_only_and_dump_as_plain_mapping() -> None:
    entry = HazardTypeReferenceEntry.model_validate(_entry("flood"))

    dumped = entry.model_dump()

    with pytest.raises(TypeError):
        entry.labels["en"] = "Changed"  # type: ignore[index]  # reason: proves the mapping is read-only
    assert dumped["labels"] == {"en": "Flood"}
    assert dumped["description"] is None
    assert HazardTypeReferenceEntry.model_validate(dumped) == entry
