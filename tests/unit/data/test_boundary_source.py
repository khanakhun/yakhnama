"""``data/boundaries/cod_ab_pak_gb_districts.yaml`` against ``DistrictBoundarySource``.

The file pins the COD-AB archive and links its Gilgit-Baltistan districts to the
gazetteer (ADR 0021). These tests keep the links honest: every linked code is a
district of the gazetteer fixture, and no gazetteer district is silently left out.
"""

from pathlib import Path
from typing import Final

import pytest
import yaml

from tests.unit.data.reference_files import load_reference
from yakhnama.modules.geography.domain.boundaries import DistrictBoundarySource
from yakhnama.modules.geography.domain.reference import PlaceReferenceFile
from yakhnama.modules.geography.domain.value_objects import AdminLevel

SOURCE_FILE: Final = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "boundaries"
    / "cod_ab_pak_gb_districts.yaml"
)
COD_AB_GB_DISTRICTS: Final = 14
UNLINKED: Final = frozenset({"PK311", "PK312", "PK313", "PK314"})


@pytest.fixture(scope="module")
def source() -> DistrictBoundarySource:
    """Return the validated boundary source file."""
    return DistrictBoundarySource.model_validate(
        yaml.safe_load(SOURCE_FILE.read_text(encoding="utf-8"))
    )


@pytest.fixture(scope="module")
def gazetteer_districts() -> frozenset[str]:
    """Return the district codes of the gazetteer fixture."""
    reference = PlaceReferenceFile.model_validate(
        load_reference("admin_hierarchy_gb.yaml")
    )
    return frozenset(
        entry.code for entry in reference.entries if entry.level is AdminLevel.DISTRICT
    )


def test_boundary_source_pins_cod_ab_for_gilgit_baltistan(
    source: DistrictBoundarySource,
) -> None:
    assert source.dataset == "cod-ab-pak"
    assert source.region_code == "PK3"
    assert source.region_place_code == "pk.gb"
    assert source.archive_member == "pak_admin2.geojson"
    assert source.download_url.startswith("https://data.humdata.org/")
    assert source.licence == "CC BY-IGO 3.0"
    assert len(source.links) == COD_AB_GB_DISTRICTS


def test_boundary_source_links_only_gazetteer_districts(
    source: DistrictBoundarySource, gazetteer_districts: frozenset[str]
) -> None:
    linked = {link.place_code for link in source.links if link.place_code is not None}

    assert linked <= gazetteer_districts
    assert linked == gazetteer_districts


def test_boundary_source_leaves_districts_without_gazetteer_place_unlinked(
    source: DistrictBoundarySource,
) -> None:
    unlinked = {link.source_code for link in source.links if link.place_code is None}

    assert unlinked == UNLINKED
    assert all(
        link.note is not None for link in source.links if link.place_code is None
    )
