"""Rules every reference file follows: header fields and a source on every entry."""

import pytest

from tests.unit.data.reference_files import (
    REFERENCE_DIRECTORY,
    REFERENCE_FILE_NAMES,
    load_reference,
    raw_entries,
)

HEADER_FIELDS = ("schema_version", "data_version", "source", "licence")

ALL_ENTRIES = [
    pytest.param(name, entry, id=f"{name}:{entry.get('code')}")
    for name in REFERENCE_FILE_NAMES
    for entry in raw_entries(name)
]


@pytest.mark.parametrize("name", REFERENCE_FILE_NAMES)
def test_reference_file_header_has_every_field_non_empty(name: str) -> None:
    raw = load_reference(name)

    missing = [field for field in HEADER_FIELDS if field not in raw]
    empty_texts = [
        field for field in ("source", "licence") if not str(raw.get(field, "")).strip()
    ]

    assert missing == []
    assert empty_texts == []
    assert raw["schema_version"] == 1
    assert isinstance(raw["data_version"], str)


@pytest.mark.parametrize(("name", "entry"), ALL_ENTRIES)
def test_reference_entry_source_is_set_to_non_empty_text(
    name: str, entry: dict[str, object]
) -> None:
    source = entry.get("source")

    is_set = isinstance(source, str) and bool(source.strip())

    assert is_set, f"{name}: entry {entry.get('code')!r} has no source"


@pytest.mark.parametrize("name", REFERENCE_FILE_NAMES)
def test_reference_file_entry_codes_are_unique(name: str) -> None:
    codes = [entry.get("code") for entry in raw_entries(name)]

    duplicates = sorted({str(code) for code in codes if codes.count(code) > 1})

    assert duplicates == []
    assert None not in codes


def test_reference_file_names_cover_every_yaml_file() -> None:
    on_disk = sorted(path.name for path in REFERENCE_DIRECTORY.glob("*.yaml"))

    assert on_disk == sorted(REFERENCE_FILE_NAMES)
