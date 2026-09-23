"""Translate between the ingestion entities and their row models.

Shapely and WKB stay on this side of the boundary: a dataset's spatial coverage is a
``BoundingBox`` in the domain and a WGS84 rectangle here, and a raster footprint is
GeoJSON in the domain and a GeoAlchemy2 ``WKBElement`` here. WKB keeps every
coordinate bit for bit, so a box or footprint reads back equal to the one saved.
Every read goes through ``model_validate``, so a row that no longer satisfies the
domain fails loudly instead of leaking an invalid entity; JSONB columns hold exactly
``model_dump(mode="json")`` of their value objects.

Patterns: Anti-Corruption Layer (mapper).
"""

from typing import Final

from geoalchemy2 import WKBElement
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import box, mapping, shape

from yakhnama.modules.ingestion.domain.entities import (
    Dataset,
    DatasetVersion,
    IngestionRun,
    Observation,
    RasterAsset,
)
from yakhnama.modules.ingestion.domain.value_objects import (
    GridCellRef,
    RasterFootprint,
    SpatialCoverage,
    StationRef,
    TemporalCoverage,
)
from yakhnama.modules.ingestion.infrastructure.orm import (
    WGS84_SRID,
    DatasetRow,
    DatasetVersionRow,
    IngestionRunRow,
    ObservationRow,
    RasterAssetRow,
)
from yakhnama.platform.db import dump_json_column
from yakhnama.shared_kernel.value_objects import BoundingBox, DateWithPrecision

STATION_PREFIX: Final = "station:"
"""``site_ref`` prefix of a station; ``StationRef.site_ref`` builds the same."""

# --------------------------------------------------------------------------- #
# Geometry                                                                    #
# --------------------------------------------------------------------------- #


def bbox_to_element(coverage: SpatialCoverage | None) -> WKBElement | None:
    """Convert a spatial coverage to a WGS84 rectangle.

    Args:
        coverage: The coverage, or ``None``.

    Returns:
        The extended WKB polygon for ``datasets.spatial_bbox``, or ``None``.
    """
    if coverage is None:
        return None
    bbox = coverage.bbox
    rectangle = box(
        bbox.min_longitude, bbox.min_latitude, bbox.max_longitude, bbox.max_latitude
    )
    return from_shape(rectangle, srid=WGS84_SRID, extended=True)


def element_to_bbox(element: WKBElement | None) -> SpatialCoverage | None:
    """Convert a stored rectangle back to the spatial coverage.

    The rectangle's bounds are the box's edges; a degenerate box (a point or a line,
    which ``BoundingBox`` allows) keeps them too.

    Args:
        element: The column value, or ``None``.

    Returns:
        The validated coverage, or ``None``.
    """
    if element is None:
        return None
    min_longitude, min_latitude, max_longitude, max_latitude = to_shape(element).bounds
    return SpatialCoverage(
        bbox=BoundingBox(
            min_longitude=min_longitude,
            min_latitude=min_latitude,
            max_longitude=max_longitude,
            max_latitude=max_latitude,
        )
    )


def footprint_to_element(footprint: RasterFootprint) -> WKBElement:
    """Convert a raster footprint to a WGS84 ``WKBElement``.

    Args:
        footprint: The footprint.

    Returns:
        The extended WKB value for ``raster_assets.footprint``.
    """
    return from_shape(
        shape(footprint.geojson.model_dump(mode="json")),
        srid=WGS84_SRID,
        extended=True,
    )


def element_to_footprint(element: WKBElement) -> RasterFootprint:
    """Convert a stored footprint back to the domain value.

    Args:
        element: The column value.

    Returns:
        The validated footprint.
    """
    return RasterFootprint.model_validate({"geojson": mapping(to_shape(element))})


# --------------------------------------------------------------------------- #
# Datasets and versions                                                       #
# --------------------------------------------------------------------------- #


def dataset_to_values(dataset: Dataset) -> dict[str, object]:
    """Return every ``datasets`` column value of ``dataset`` except the key.

    Args:
        dataset: The aggregate.

    Returns:
        Column name to value, for inserts and versioned updates alike.
    """
    temporal = dataset.temporal_coverage
    start = None if temporal is None else temporal.start
    end = None if temporal is None else temporal.end
    return {
        "code": dataset.code,
        "title": dataset.title,
        "publisher": dataset.publisher,
        "licence": dump_json_column(dataset.licence),
        "spatial_bbox": bbox_to_element(dataset.spatial_coverage),
        "temporal_start": None if start is None else start.value,
        "temporal_start_precision": None if start is None else start.precision.value,
        "temporal_end": None if end is None else end.value,
        "temporal_end_precision": None if end is None else end.precision.value,
        "update_frequency": dataset.update_frequency.value,
        "status": dataset.status.value,
        "description": dataset.description,
        "homepage_url": dataset.homepage_url,
        "version": dataset.version,
        "created_at": dataset.created_at,
        "updated_at": dataset.updated_at,
    }


def dataset_to_row(dataset: Dataset) -> DatasetRow:
    """Build the ``datasets`` row of ``dataset``.

    Args:
        dataset: The aggregate.

    Returns:
        A transient row carrying the same values.
    """
    return DatasetRow(id=dataset.id, **dataset_to_values(dataset))


def _temporal_from_row(row: DatasetRow) -> TemporalCoverage | None:
    if row.temporal_start is None:
        return None
    end = (
        None
        if row.temporal_end is None
        else DateWithPrecision.model_validate(
            {"value": row.temporal_end, "precision": row.temporal_end_precision}
        )
    )
    return TemporalCoverage(
        start=DateWithPrecision.model_validate(
            {"value": row.temporal_start, "precision": row.temporal_start_precision}
        ),
        end=end,
    )


def row_to_dataset(row: DatasetRow) -> Dataset:
    """Rebuild a dataset from its row.

    Args:
        row: A fully loaded row of ``datasets``.

    Returns:
        The validated ``Dataset``.
    """
    return Dataset.model_validate(
        {
            "id": row.id,
            "code": row.code,
            "title": row.title,
            "publisher": row.publisher,
            "licence": row.licence,
            "spatial_coverage": element_to_bbox(row.spatial_bbox),
            "temporal_coverage": _temporal_from_row(row),
            "update_frequency": row.update_frequency,
            "status": row.status,
            "description": row.description,
            "homepage_url": row.homepage_url,
            "version": row.version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


def version_to_row(version: DatasetVersion) -> DatasetVersionRow:
    """Build the ``dataset_versions`` row of ``version``.

    Args:
        version: The entity.

    Returns:
        A transient row carrying the same values.
    """
    return DatasetVersionRow(
        id=version.id,
        dataset_id=version.dataset_id,
        label=version.label,
        retrieved_at=version.retrieved_at.value,
        retrieved_at_precision=version.retrieved_at.precision.value,
        input_checksum=version.input_checksum,
        notes=version.notes,
        created_at=version.created_at,
    )


def row_to_version(row: DatasetVersionRow) -> DatasetVersion:
    """Rebuild a dataset version from its row.

    Args:
        row: A row of ``dataset_versions``.

    Returns:
        The validated ``DatasetVersion``.
    """
    return DatasetVersion.model_validate(
        {
            "id": row.id,
            "dataset_id": row.dataset_id,
            "label": row.label,
            "retrieved_at": {
                "value": row.retrieved_at,
                "precision": row.retrieved_at_precision,
            },
            "input_checksum": row.input_checksum,
            "notes": row.notes,
            "created_at": row.created_at,
        }
    )


# --------------------------------------------------------------------------- #
# Runs                                                                        #
# --------------------------------------------------------------------------- #


def run_to_values(run: IngestionRun) -> dict[str, object]:
    """Return every ``ingestion_runs`` column value of ``run`` except the key.

    Args:
        run: The aggregate.

    Returns:
        Column name to value, for inserts and versioned updates alike.
    """
    return {
        "dataset_version_id": run.dataset_version_id,
        "adapter_name": run.adapter_name,
        "status": run.status.value,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "counts": dump_json_column(run.counts),
        "report": dump_json_column(run.report),
        "input_checksum": run.input_checksum,
        "triggered_by": run.triggered_by,
        "version": run.version,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def run_to_row(run: IngestionRun) -> IngestionRunRow:
    """Build the ``ingestion_runs`` row of ``run``.

    Args:
        run: The aggregate.

    Returns:
        A transient row carrying the same values.
    """
    return IngestionRunRow(id=run.id, **run_to_values(run))


def row_to_run(row: IngestionRunRow) -> IngestionRun:
    """Rebuild a run from its row.

    Args:
        row: A row of ``ingestion_runs``.

    Returns:
        The validated ``IngestionRun``.
    """
    return IngestionRun.model_validate(
        {
            "id": row.id,
            "dataset_version_id": row.dataset_version_id,
            "adapter_name": row.adapter_name,
            "status": row.status,
            "created_at": row.created_at,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "counts": row.counts,
            "report": row.report,
            "input_checksum": row.input_checksum,
            "triggered_by": row.triggered_by,
            "version": row.version,
            "updated_at": row.updated_at,
        }
    )


# --------------------------------------------------------------------------- #
# Observations                                                                #
# --------------------------------------------------------------------------- #


def observation_to_values(observation: Observation) -> dict[str, object]:
    """Return the ``observations`` column values of ``observation``.

    Args:
        observation: The entity.

    Returns:
        Column name to value, primary key included, for a bulk insert.
    """
    site = observation.station or observation.grid_cell
    value = observation.value
    return {
        "observed_at": observation.observed_at.value,
        "dataset_version_id": observation.dataset_version_id,
        "variable_code": observation.variable,
        "site_ref": observation.site_ref,
        "observed_at_precision": observation.observed_at.precision.value,
        "site": dump_json_column(site),
        "value": None if value is None else value.value,
        "unit": None if value is None else value.unit,
        "quality": observation.quality.value,
        "ingested_run_id": observation.ingested_run_id,
    }


def row_to_observation(row: ObservationRow) -> Observation:
    """Rebuild an observation from its row.

    ``site_ref`` says which kind of site ``site`` holds, so the JSON needs no kind
    field of its own.

    Args:
        row: A row of ``observations``.

    Returns:
        The validated ``Observation``.
    """
    is_station = row.site_ref.startswith(STATION_PREFIX)
    return Observation.model_validate(
        {
            "dataset_version_id": row.dataset_version_id,
            "station": StationRef.model_validate(row.site) if is_station else None,
            "grid_cell": None if is_station else GridCellRef.model_validate(row.site),
            "variable": row.variable_code,
            "value": (
                None if row.value is None else {"value": row.value, "unit": row.unit}
            ),
            "observed_at": {
                "value": row.observed_at,
                "precision": row.observed_at_precision,
            },
            "quality": row.quality,
            "ingested_run_id": row.ingested_run_id,
        }
    )


# --------------------------------------------------------------------------- #
# Raster assets                                                               #
# --------------------------------------------------------------------------- #


def raster_to_row(asset: RasterAsset) -> RasterAssetRow:
    """Build the ``raster_assets`` row of ``asset``.

    Args:
        asset: The aggregate.

    Returns:
        A transient row; ``acquired_start`` is the start of the acquisition period.
    """
    return RasterAssetRow(
        id=asset.id,
        dataset_version_id=asset.dataset_version_id,
        stac_id=asset.stac_id,
        footprint=footprint_to_element(asset.footprint),
        acquired_at=asset.acquired_at.value,
        acquired_at_precision=asset.acquired_at.precision.value,
        acquired_start=asset.acquired_at.truncate().value,
        platform=asset.platform,
        cloud_cover=asset.cloud_cover,
        bands=[band.model_dump(mode="json") for band in asset.bands],
        assets=[item.model_dump(mode="json") for item in asset.assets],
        version=asset.version,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
    )


def row_to_raster(row: RasterAssetRow) -> RasterAsset:
    """Rebuild a raster asset from its row.

    Args:
        row: A fully loaded row of ``raster_assets``.

    Returns:
        The validated ``RasterAsset``.
    """
    return RasterAsset.model_validate(
        {
            "id": row.id,
            "dataset_version_id": row.dataset_version_id,
            "stac_id": row.stac_id,
            "footprint": element_to_footprint(row.footprint),
            "acquired_at": {
                "value": row.acquired_at,
                "precision": row.acquired_at_precision,
            },
            "platform": row.platform,
            "cloud_cover": row.cloud_cover,
            "bands": row.bands,
            "assets": row.assets,
            "version": row.version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )
