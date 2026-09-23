"""Factories for the ``ingestion`` domain: datasets, versions, runs and records.

The ``...TestFactory`` suffix avoids clashing with the domain's own factories
(``yakhnama.modules.ingestion.domain.factories``). These factories build models
directly, bypassing registration, so they arrange state; they do not test creation
rules.

Titles, publishers, attributions, station codes and platforms are placeholders
(``"Test publisher <n>"``), never real organisations or instruments; URLs use the
reserved ``example.test`` domain (RFC 2606). Footprints lie inside a box around
Gilgit-Baltistan only so values look plausible; they are synthetic.

Patterns: Factory.
"""

import hashlib
from collections.abc import Mapping

from geojson_pydantic import Polygon
from polyfactory import PostGenerated, Use

from tests.factories.base import (
    FACTORY_IDS,
    YakhnamaModelFactory,
    pick,
    random_instant,
    sequence,
)
from yakhnama.modules.ingestion.domain.entities import (
    Dataset,
    DatasetVersion,
    IngestionRun,
    Observation,
    RasterAsset,
)
from yakhnama.modules.ingestion.domain.value_objects import (
    COG_MEDIA_TYPE,
    DatasetDetails,
    DatasetLicence,
    QualityFlag,
    RasterFootprint,
    StacAsset,
    StationRef,
    UpdateFrequency,
)
from yakhnama.shared_kernel.value_objects import (
    DatePrecision,
    DateWithPrecision,
    Measurement,
    SiUnit,
)

TEST_LICENCE_URL = "https://licences.example.test/cc-by-4.0"
"""A licence URL every factory-built licence may use; ``example.test`` is reserved."""

TEST_FOOTPRINT = RasterFootprint(
    geojson=Polygon.model_validate(
        {
            "type": "Polygon",
            "coordinates": [
                [[74.0, 36.0], [75.0, 36.0], [75.0, 37.0], [74.0, 37.0], [74.0, 36.0]]
            ],
        }
    )
)
"""A one-degree square footprint (synthetic)."""

_checksum_seeds = sequence("ingestion-test-input-{}")


def checksum() -> str:
    """Return a new, distinct SHA-256 hex digest.

    Returns:
        64 lower-case hex digits.
    """
    return hashlib.sha256(_checksum_seeds().encode()).hexdigest()


def _same_as_created_at(_name: str, values: Mapping[str, object]) -> object:
    # A record at version 1 has not changed since it was created.
    return values["created_at"]


def _retrieved_at_creation(_name: str, values: Mapping[str, object]) -> object:
    # Retrieval never follows recording; retrieving at the same instant is the
    # simplest valid arrangement.
    return DateWithPrecision.model_validate(
        {"value": values["created_at"], "precision": DatePrecision.EXACT}
    )


class DatasetLicenceTestFactory(YakhnamaModelFactory[DatasetLicence]):
    """Builds SPDX licences with a placeholder attribution.

    Implements: Factory.
    """

    __model__ = DatasetLicence

    spdx_id = "CC-BY-4.0"
    url = TEST_LICENCE_URL
    attribution = sequence("Test attribution {}")


class DatasetDetailsTestFactory(YakhnamaModelFactory[DatasetDetails]):
    """Builds registration details with a licence; optional fields stay ``None``.

    Implements: Factory.
    """

    __model__ = DatasetDetails

    code = sequence("test_dataset_{:05d}")
    title = sequence("Test dataset {}")
    publisher = sequence("Test publisher {}")
    licence = Use(DatasetLicenceTestFactory.build)
    update_frequency = Use(pick(tuple(UpdateFrequency)))


class DatasetTestFactory(YakhnamaModelFactory[Dataset]):
    """Builds active datasets at version 1.

    Implements: Factory.
    """

    __model__ = Dataset

    id = Use(FACTORY_IDS.new_id)
    code = sequence("test_dataset_{:05d}")
    title = sequence("Test dataset {}")
    publisher = sequence("Test publisher {}")
    licence = Use(DatasetLicenceTestFactory.build)
    update_frequency = Use(pick(tuple(UpdateFrequency)))
    version = 1
    created_at = Use(random_instant)
    updated_at = PostGenerated(_same_as_created_at)


class DatasetVersionTestFactory(YakhnamaModelFactory[DatasetVersion]):
    """Builds dataset versions retrieved at the instant they were recorded.

    Implements: Factory.
    """

    __model__ = DatasetVersion

    id = Use(FACTORY_IDS.new_id)
    dataset_id = Use(FACTORY_IDS.new_id)
    label = sequence("v{}")
    input_checksum = Use(checksum)
    created_at = Use(random_instant)
    retrieved_at = PostGenerated(_retrieved_at_creation)


class IngestionRunTestFactory(YakhnamaModelFactory[IngestionRun]):
    """Builds pending runs with zero counts and an empty report.

    Implements: Factory.
    """

    __model__ = IngestionRun

    id = Use(FACTORY_IDS.new_id)
    dataset_version_id = Use(FACTORY_IDS.new_id)
    adapter_name = "test_adapter"
    version = 1
    created_at = Use(random_instant)
    updated_at = PostGenerated(_same_as_created_at)


class StationRefTestFactory(YakhnamaModelFactory[StationRef]):
    """Builds station references with a placeholder code and no location.

    Implements: Factory.
    """

    __model__ = StationRef

    code = sequence("TEST-{:05d}")


class ObservationTestFactory(YakhnamaModelFactory[Observation]):
    """Builds good-quality air temperature observations at a station.

    Implements: Factory.
    """

    __model__ = Observation

    dataset_version_id = Use(FACTORY_IDS.new_id)
    station = Use(StationRefTestFactory.build)
    variable = "air_temperature"
    value = Measurement(value=273.15, unit=SiUnit.KELVIN)
    observed_at = Use(
        lambda: DateWithPrecision(value=random_instant(), precision=DatePrecision.HOUR)
    )
    quality = QualityFlag.GOOD
    ingested_run_id = Use(FACTORY_IDS.new_id)


class StacAssetTestFactory(YakhnamaModelFactory[StacAsset]):
    """Builds one COG data asset with an object-storage key.

    Implements: Factory.
    """

    __model__ = StacAsset

    key = "data"
    href = sequence("rasters/test/{:05d}.tif")
    media_type = COG_MEDIA_TYPE
    roles = ("data",)


class RasterAssetTestFactory(YakhnamaModelFactory[RasterAsset]):
    """Builds raster assets over ``TEST_FOOTPRINT`` with one COG asset.

    Implements: Factory.
    """

    __model__ = RasterAsset

    id = Use(FACTORY_IDS.new_id)
    dataset_version_id = Use(FACTORY_IDS.new_id)
    stac_id = sequence("test-scene-{:05d}")
    footprint = TEST_FOOTPRINT
    acquired_at = Use(
        lambda: DateWithPrecision(value=random_instant(), precision=DatePrecision.EXACT)
    )
    platform = "test-platform"
    assets = Use(lambda: (StacAssetTestFactory.build(),))
    version = 1
    created_at = Use(random_instant)
    updated_at = PostGenerated(_same_as_created_at)
