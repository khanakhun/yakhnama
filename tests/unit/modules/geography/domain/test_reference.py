"""Unit tests for ``yakhnama.modules.geography.domain.reference``."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from yakhnama.modules.geography.domain.factories import PlaceFactory
from yakhnama.modules.geography.domain.reference import (
    PlaceReferenceEntry,
    PlaceReferenceFile,
    PlaceReferenceName,
)
from yakhnama.modules.geography.domain.value_objects import (
    AdminLevel,
    PlaceName,
    ScriptCode,
)
from yakhnama.shared_kernel.ids import Uuid7Generator
from yakhnama.shared_kernel.value_objects import BoundingBox, Coordinates

if TYPE_CHECKING:
    from yakhnama.modules.geography.domain.entities import Place

NOW = datetime(2026, 9, 23, tzinfo=UTC)


class _FixedClock:
    """Always returns ``NOW``.

    Implements: Fake.
    """

    def now(self) -> datetime:
        return NOW


def _entry(
    code: str, level: str, parent_code: str | None = None, **extra: object
) -> dict[str, object]:
    return {
        "code": code,
        "level": level,
        "parent_code": parent_code,
        "names": [{"text": code, "language": "en", "is_preferred": True}],
        "status": "fixture",
        **extra,
    }


def _file(entries: list[dict[str, object]], **extra: object) -> dict[str, object]:
    return {
        "schema_version": 1,
        "data_version": "0.1.0",
        "source": "Test fixture, not a claim about real boundaries",
        "licence": "CC0-1.0",
        "entries": entries,
        **extra,
    }


# Children listed before parents on purpose, to prove the ordering of drafts.
VALID_FILE = _file(
    [
        _entry("pk.gb.hunza", "district", "pk.gb"),
        _entry("pk.gb", "province_or_region", "pk"),
        _entry(
            "pk",
            "country",
            centroid={"longitude": 69.3, "latitude": 30.4},
            bbox={
                "min_longitude": 60.0,
                "min_latitude": 23.0,
                "max_longitude": 78.0,
                "max_latitude": 37.5,
            },
        ),
    ]
)


def test_place_reference_file_valid_payload_round_trips_through_json() -> None:
    reference = PlaceReferenceFile.model_validate(VALID_FILE)

    restored = PlaceReferenceFile.model_validate_json(reference.model_dump_json())

    assert restored == reference


def test_place_reference_file_round_trips_through_model_dump() -> None:
    reference = PlaceReferenceFile.model_validate(VALID_FILE)

    restored = PlaceReferenceFile.model_validate(reference.model_dump())

    assert restored == reference


def test_place_reference_file_to_factory_inputs_orders_parents_first() -> None:
    reference = PlaceReferenceFile.model_validate(VALID_FILE)

    drafts = reference.to_factory_inputs()

    assert [draft.code for draft in drafts] == ["pk", "pk.gb", "pk.gb.hunza"]
    assert drafts[0].centroid == Coordinates(longitude=69.3, latitude=30.4)
    assert drafts[0].geometry is None


def test_place_reference_file_drafts_feed_the_factory_in_order() -> None:
    reference = PlaceReferenceFile.model_validate(VALID_FILE)
    clock = _FixedClock()
    ids = Uuid7Generator(clock=clock)
    created: dict[str, Place] = {}

    for draft in reference.to_factory_inputs():
        parent = None if draft.parent_code is None else created[draft.parent_code]
        change = PlaceFactory().create(draft, parent=parent, ids=ids, clock=clock)
        created[draft.code] = change.state

    assert created["pk.gb.hunza"].parent_id == created["pk.gb"].id


@pytest.mark.parametrize(
    "payload",
    [
        _file([_entry("pk", "country"), _entry("pk", "country")]),
        _file([_entry("pk.gb", "province_or_region", "pk")]),
        _file([_entry("pk", "country"), _entry("pk.gb", "province_or_region")]),
        _file([_entry("pk", "country"), _entry("xx", "country", "pk")]),
        _file(
            [
                _entry("pk", "country"),
                _entry("pk.gb.hunza", "district", "pk"),
                _entry("pk.gb", "province_or_region", "pk.gb.hunza"),
            ]
        ),
        _file([_entry("pk", "country")], schema_version=2),
        _file([]),
        _file([_entry("pk", "country")], data_version=" 1"),
        _file([_entry("pk", "country")], licence=""),
    ],
    ids=[
        "duplicate-codes",
        "parent-not-in-file",
        "missing-parent",
        "country-under-country",
        "region-under-district",
        "unknown-schema-version",
        "no-entries",
        "malformed-data-version",
        "empty-licence",
    ],
)
def test_place_reference_file_invalid_payload_raises_validation_error(
    payload: dict[str, object],
) -> None:
    with pytest.raises(PydanticValidationError):
        PlaceReferenceFile.model_validate(payload)


def test_place_reference_entry_sourced_without_source_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError, match="no source"):
        PlaceReferenceEntry.model_validate(_entry("pk", "country", status="sourced"))


def test_place_reference_entry_sourced_with_source_is_accepted() -> None:
    entry = PlaceReferenceEntry.model_validate(
        _entry("pk", "country", status="sourced", source="A cited dataset")
    )

    assert entry.source == "A cited dataset"


def test_place_reference_entry_own_parent_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError, match="own parent"):
        PlaceReferenceEntry.model_validate(_entry("pk.gb", "district", "pk.gb"))


def test_place_reference_entry_centroid_outside_bbox_raises_validation_error() -> None:
    payload = _entry(
        "pk",
        "country",
        centroid={"longitude": 10.0, "latitude": 10.0},
        bbox={
            "min_longitude": 60.0,
            "min_latitude": 23.0,
            "max_longitude": 78.0,
            "max_latitude": 37.5,
        },
    )

    with pytest.raises(PydanticValidationError, match="outside its bbox"):
        PlaceReferenceEntry.model_validate(payload)


@pytest.mark.parametrize("status", ["active", "retired", ""])
def test_place_reference_entry_unknown_status_raises_validation_error(
    status: str,
) -> None:
    with pytest.raises(PydanticValidationError):
        PlaceReferenceEntry.model_validate(_entry("pk", "country", status=status))


def test_place_reference_entry_to_draft_carries_every_field() -> None:
    entry = PlaceReferenceEntry.model_validate(
        _entry("pk.gb", "province_or_region", "pk")
    )

    draft = entry.to_draft()

    assert (draft.code, draft.level, draft.parent_code) == (
        "pk.gb",
        AdminLevel.PROVINCE_OR_REGION,
        "pk",
    )
    assert draft.names == (PlaceName(text="pk.gb", language="en", is_preferred=True),)


@pytest.mark.parametrize("language", ["ur", "shi", "bsk", "bft", "wbl", "khw"])
def test_place_reference_name_local_language_without_source_raises_validation_error(
    language: str,
) -> None:
    with pytest.raises(PydanticValidationError, match="needs a source"):
        PlaceReferenceName(text="Hunza", language=language)


@pytest.mark.parametrize("language", ["en", "en-Latn"])
def test_place_reference_name_english_without_source_is_accepted(
    language: str,
) -> None:
    name = PlaceReferenceName(text="Hunza", language=language)

    assert name.source is None


def test_place_reference_name_to_place_name_drops_textual_source() -> None:
    reference = PlaceReferenceName(
        text="ہنزہ",
        language="ur",
        script=ScriptCode.ARAB,
        kind="official",
        is_preferred=True,
        source="A cited gazetteer",
    )

    name = reference.to_place_name()

    assert name == PlaceName(
        text="ہنزہ", language="ur", script=ScriptCode.ARAB, is_preferred=True
    )


def test_place_reference_name_contradicting_script_raises_on_conversion() -> None:
    reference = PlaceReferenceName(
        text="Hunza", language="ur-Arab", script=ScriptCode.LATN, source="A source"
    )

    with pytest.raises(PydanticValidationError, match="contradicts"):
        reference.to_place_name()


def test_place_reference_entry_bbox_is_kept_but_not_turned_into_geometry() -> None:
    box = BoundingBox(
        min_longitude=74.0, min_latitude=36.0, max_longitude=75.0, max_latitude=37.0
    )
    entry = PlaceReferenceEntry.model_validate(
        _entry("pk.gb.hunza", "district", "pk.gb", bbox=box.model_dump())
    )

    draft = entry.to_draft()

    assert (entry.bbox, draft.geometry) == (box, None)


@given(
    text=st.text(
        alphabet=st.characters(categories=("L", "N")), min_size=1, max_size=40
    ),
    language=st.sampled_from(["en", "ur", "shi", "bsk"]),
    script=st.none() | st.sampled_from(list(ScriptCode)),
    is_preferred=st.booleans(),
)
def test_place_reference_name_round_trip_returns_equal_name(
    text: str, language: str, script: ScriptCode | None, *, is_preferred: bool
) -> None:
    name = PlaceReferenceName(
        text=text,
        language=language,
        script=script,
        is_preferred=is_preferred,
        source="A cited source",
    )

    restored = PlaceReferenceName.model_validate(name.model_dump())

    assert restored == name
