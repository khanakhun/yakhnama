"""``data/reference/admin_hierarchy_gb.yaml`` against ``PlaceReferenceFile``."""

import pytest

from tests.unit.data.reference_files import load_reference
from yakhnama.modules.geography.domain.reference import PlaceReferenceFile
from yakhnama.modules.geography.domain.value_objects import AdminLevel

FILE_NAME = "admin_hierarchy_gb.yaml"
EXPECTED_DISTRICTS = frozenset(
    {
        "Gilgit",
        "Hunza",
        "Nagar",
        "Ghizer",
        "Skardu",
        "Shigar",
        "Kharmang",
        "Ghanche",
        "Astore",
        "Diamer",
    }
)


@pytest.fixture(scope="module")
def reference() -> PlaceReferenceFile:
    """Return the validated place fixture file."""
    return PlaceReferenceFile.model_validate(load_reference(FILE_NAME))


def test_admin_hierarchy_gb_yaml_validates_and_round_trips_equal(
    reference: PlaceReferenceFile,
) -> None:
    dumped = reference.model_dump(mode="json")

    result = PlaceReferenceFile.model_validate(dumped)

    assert result == reference


def test_admin_hierarchy_gb_yaml_parents_exist_with_strictly_higher_level(
    reference: PlaceReferenceFile,
) -> None:
    levels = {entry.code: entry.level for entry in reference.entries}

    broken = sorted(
        entry.code
        for entry in reference.entries
        if entry.parent_code is not None
        and (
            entry.parent_code not in levels
            or levels[entry.parent_code].rank >= entry.level.rank
        )
    )

    assert broken == []


def test_admin_hierarchy_gb_yaml_every_entry_is_fixture_without_geometry(
    reference: PlaceReferenceFile,
) -> None:
    result = sorted(
        entry.code
        for entry in reference.entries
        if entry.status != "fixture"
        or entry.centroid is not None
        or entry.bbox is not None
    )

    assert result == []


def test_admin_hierarchy_gb_yaml_names_are_preferred_official_english(
    reference: PlaceReferenceFile,
) -> None:
    names = [name for entry in reference.entries for name in entry.names]

    result = [
        name.text
        for name in names
        if name.language != "en" or name.kind != "official" or not name.is_preferred
    ]

    assert result == []


def test_admin_hierarchy_gb_yaml_non_english_names_have_a_source(
    reference: PlaceReferenceFile,
) -> None:
    names = [name for entry in reference.entries for name in entry.names]

    unsourced = [
        name.text
        for name in names
        if name.language.partition("-")[0] != "en" and name.source is None
    ]

    assert unsourced == []


def test_admin_hierarchy_gb_yaml_levels_stop_at_district(
    reference: PlaceReferenceFile,
) -> None:
    counts = {
        level: sum(1 for entry in reference.entries if entry.level is level)
        for level in AdminLevel
    }

    district_names = frozenset(
        entry.names[0].text
        for entry in reference.entries
        if entry.level is AdminLevel.DISTRICT
    )

    assert counts[AdminLevel.COUNTRY] == 1
    assert counts[AdminLevel.PROVINCE_OR_REGION] == 1
    assert counts[AdminLevel.DIVISION] == 3
    assert district_names == EXPECTED_DISTRICTS
    assert counts[AdminLevel.TEHSIL] == 0
    assert counts[AdminLevel.UNION_COUNCIL] == 0
    assert counts[AdminLevel.VILLAGE] == 0


def test_admin_hierarchy_gb_yaml_factory_inputs_put_parents_first(
    reference: PlaceReferenceFile,
) -> None:
    drafts = reference.to_factory_inputs()

    seen: set[str] = set()
    orphans: list[str] = []
    for draft in drafts:
        if draft.parent_code is not None and draft.parent_code not in seen:
            orphans.append(draft.code)
        seen.add(draft.code)

    assert orphans == []
    assert len(drafts) == len(reference.entries)
