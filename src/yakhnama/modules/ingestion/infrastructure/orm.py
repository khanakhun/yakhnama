"""SQLAlchemy row models of the ``ingestion`` bounded context.

Rows are persistence shapes only (ADR 0004): ``mappers.py`` translates them to and
from the frozen domain entities, and nothing outside this package sees them.

- ``datasets``: one row per catalog entry. ``licence`` is the JSON dump of
  ``DatasetLicence``; the spatial coverage is stored as a WGS84 rectangle
  (``geometry(Polygon, 4326)``) so it can be searched spatially later, and the
  temporal coverage as start and end instants each with their precision.
- ``dataset_versions``: one row per release, unique by ``(dataset_id, label)``.
- ``ingestion_runs``: one row per run; ``counts`` and ``report`` are the JSON dumps
  of ``RunCounts`` and ``IngestionReport``.
- ``observations``: the narrow time series, one row per value (see below).
- ``raster_assets``: one row per STAC item; ``bands`` and ``assets`` are JSON arrays
  of the value objects' dumps and ``footprint`` is WGS84 with a GiST index.

**Observations are hypertable-ready.** The primary key is ``(observed_at,
dataset_version_id, variable_code, site_ref)`` with the time column first, so the
table can be turned into a TimescaleDB hypertable partitioned by ``observed_at``
without changing a column, the key or any query (TimescaleDB requires every unique
index to contain the partitioning column). For the same reason ``observations``
carries **no foreign key**: ``dataset_version_id`` and ``ingested_run_id`` are
lineage ids whose existence the pipeline guarantees (it only writes observations of
the run it is finishing, in the same transaction). ``site`` is the JSON dump of the
``StationRef`` or ``GridCellRef`` named by ``site_ref`` (``station:<code>`` or
``grid_cell:<id>``), which keeps the table long and narrow without a stations table.

**Collation.** ``datasets.code`` and ``observations.site_ref`` use the binary
``"C"`` collation. They are keyset-pagination sort keys, and the database default
(``en_US.utf8`` in the PostGIS image) ignores punctuation at the first comparison
level, which would order them differently from Python's ``sorted`` (the in-memory
Fake) and from the cursor comparison. Declaring it on the column lets the primary
key and the listing indexes serve the ``ORDER BY`` directly.

**Raster order.** ``acquired_start`` is derived by the mapper from
``acquired_at.truncate()`` (the start of the acquisition period), because rasters are
listed and filtered by that start, exactly as the domain and the Fake compute it.

Other modules' ids (``triggered_by``) carry no foreign key: they belong to modules
this one knows only through their facades.

Patterns: Adapter (ORM row models behind the repository and query service adapters).
"""

from datetime import datetime
from typing import Final
from uuid import UUID

from geoalchemy2 import Geometry, WKBElement
from sqlalchemy import (
    CheckConstraint,
    Double,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from yakhnama.platform.db import Base

WGS84_SRID: Final = 4326
"""EPSG code of every stored geometry (ADR 0002)."""

BINARY_COLLATION: Final = "C"
"""Code-point collation of keyset sort keys; see the module docstring."""

DATASETS_TABLE: Final = "datasets"
DATASET_VERSIONS_TABLE: Final = "dataset_versions"
INGESTION_RUNS_TABLE: Final = "ingestion_runs"
OBSERVATIONS_TABLE: Final = "observations"
RASTER_ASSETS_TABLE: Final = "raster_assets"


class DatasetRow(Base):
    """Row model of the ``datasets`` table: one row per ``Dataset``.

    Implements: Adapter (ORM row model of ``SqlAlchemyDatasetRepository``).

    Attributes:
        id: Primary key, the dataset id (UUIDv7).
        code: Catalog code, unique, binary collation.
        title: Human-readable title.
        publisher: Who publishes the data.
        licence: ``DatasetLicence`` as JSON.
        spatial_bbox: The spatial coverage as a WGS84 rectangle, if known.
        temporal_start: Start of the temporal coverage, UTC, if known.
        temporal_start_precision: ``DatePrecision`` of ``temporal_start``.
        temporal_end: End of the temporal coverage, UTC, if known.
        temporal_end_precision: ``DatePrecision`` of ``temporal_end``.
        update_frequency: ``UpdateFrequency`` value.
        status: ``DatasetStatus`` value.
        description: Long-form description, if any.
        homepage_url: The dataset's page, if online.
        version: Optimistic-concurrency version, compared on every update.
        created_at: When it was registered, UTC.
        updated_at: When it last changed, UTC.
    """

    __tablename__ = DATASETS_TABLE
    __table_args__ = (
        UniqueConstraint("code"),
        CheckConstraint(
            "(temporal_start IS NULL) = (temporal_start_precision IS NULL)",
            name="temporal_start_complete",
        ),
        CheckConstraint(
            "(temporal_end IS NULL) = (temporal_end_precision IS NULL)",
            name="temporal_end_complete",
        ),
        # An end without a start cannot come from TemporalCoverage.
        CheckConstraint(
            "temporal_end IS NULL OR temporal_start IS NOT NULL",
            name="temporal_end_needs_start",
        ),
        Index("ix_datasets_spatial_bbox_gist", "spatial_bbox", postgresql_using="gist"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    # Lengths follow the domain bounds: DATASET_CODE_PATTERN (64),
    # DATASET_TITLE_MAX_LENGTH, PUBLISHER_MAX_LENGTH, DATASET_DESCRIPTION_MAX_LENGTH
    # and DATASET_URL_MAX_LENGTH.
    code: Mapped[str] = mapped_column(String(64, collation=BINARY_COLLATION))
    title: Mapped[str] = mapped_column(String(300))
    publisher: Mapped[str] = mapped_column(String(200))
    licence: Mapped[dict[str, object]] = mapped_column(JSONB)
    spatial_bbox: Mapped[WKBElement | None] = mapped_column(
        Geometry(geometry_type="POLYGON", srid=WGS84_SRID, spatial_index=False)
    )
    temporal_start: Mapped[datetime | None]
    temporal_start_precision: Mapped[str | None] = mapped_column(String(16))
    temporal_end: Mapped[datetime | None]
    temporal_end_precision: Mapped[str | None] = mapped_column(String(16))
    update_frequency: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), index=True)
    description: Mapped[str | None] = mapped_column(String(4000))
    homepage_url: Mapped[str | None] = mapped_column(String(2048))
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class DatasetVersionRow(Base):
    """Row model of the ``dataset_versions`` table: one row per ``DatasetVersion``.

    Implements: Adapter (ORM row model of ``SqlAlchemyDatasetVersionRepository``).

    Attributes:
        id: Primary key, the version id (UUIDv7).
        dataset_id: The dataset it belongs to.
        label: The release label, unique within the dataset.
        retrieved_at: When the input was retrieved, UTC.
        retrieved_at_precision: ``DatePrecision`` of ``retrieved_at``.
        input_checksum: SHA-256 of the input bytes, lower-case hex.
        notes: Curator remarks, if any.
        created_at: When the version was recorded, UTC.
    """

    __tablename__ = DATASET_VERSIONS_TABLE
    __table_args__ = (
        # Named uq_dataset_versions_dataset_id by the convention; leading with
        # dataset_id it also serves every per-dataset lookup and listing.
        UniqueConstraint("dataset_id", "label"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="RESTRICT")
    )
    # VERSION_LABEL_PATTERN (64), SHA256_PATTERN (64) and NOTES_MAX_LENGTH.
    label: Mapped[str] = mapped_column(String(64))
    retrieved_at: Mapped[datetime]
    retrieved_at_precision: Mapped[str] = mapped_column(String(16))
    input_checksum: Mapped[str] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime]


class IngestionRunRow(Base):
    """Row model of the ``ingestion_runs`` table: one row per ``IngestionRun``.

    Implements: Adapter (ORM row model of ``SqlAlchemyIngestionRunRepository``).

    Attributes:
        id: Primary key, the run id (UUIDv7).
        dataset_version_id: The version ingested.
        adapter_name: The source adapter used.
        status: ``RunStatus`` value.
        started_at: When a worker started it, if it did.
        finished_at: When it finished, if it did.
        counts: ``RunCounts`` as JSON.
        report: ``IngestionReport`` as JSON.
        input_checksum: SHA-256 of the bytes read, once known.
        triggered_by: The requesting user, or ``None`` for the system.
        version: Optimistic-concurrency version, compared on every update.
        created_at: When the run was requested, UTC.
        updated_at: When it last changed, UTC.
    """

    __tablename__ = INGESTION_RUNS_TABLE
    __table_args__ = (
        # Serves the per-version, newest-first run listings.
        Index(
            "ix_ingestion_runs_dataset_version_id_created_at",
            "dataset_version_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    dataset_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="RESTRICT")
    )
    # ADAPTER_NAME_PATTERN (64); "partially_succeeded" is the longest status.
    adapter_name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    counts: Mapped[dict[str, object]] = mapped_column(JSONB)
    report: Mapped[dict[str, object]] = mapped_column(JSONB)
    input_checksum: Mapped[str | None] = mapped_column(String(64))
    triggered_by: Mapped[UUID | None]
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class ObservationRow(Base):
    """Row model of the ``observations`` table: one row per ``Observation``.

    Append-only and hypertable-ready; see the module docstring.

    Implements: Adapter (ORM row model of ``SqlAlchemyObservationRepository``).

    Attributes:
        observed_at: The observed instant, UTC; first primary key column.
        dataset_version_id: The dataset version it came from (no foreign key).
        variable_code: The variable code.
        site_ref: ``station:<code>`` or ``grid_cell:<id>``, binary collation.
        observed_at_precision: ``DatePrecision`` of ``observed_at``.
        site: The ``StationRef`` or ``GridCellRef`` as JSON.
        value: The value in the registry unit, or ``NULL`` when missing.
        unit: The unit of ``value``; set exactly with it.
        quality: ``QualityFlag`` value.
        ingested_run_id: The run that ingested it (lineage, no foreign key).
    """

    __tablename__ = OBSERVATIONS_TABLE
    __table_args__ = (
        # Time first, so a TimescaleDB hypertable can partition on observed_at.
        PrimaryKeyConstraint(
            "observed_at", "dataset_version_id", "variable_code", "site_ref"
        ),
        CheckConstraint("(value IS NULL) = (unit IS NULL)", name="value_complete"),
        # Serves the time-series query (one version or a dataset's versions, one
        # variable, a time window, ordered by instant then site) and the count
        # per version.
        Index(
            "ix_observations_dataset_version_id_variable_code",
            "dataset_version_id",
            "variable_code",
            "observed_at",
            "site_ref",
        ),
    )

    observed_at: Mapped[datetime]
    dataset_version_id: Mapped[UUID]
    # VARIABLE_CODE_PATTERN (64) and SITE_REF_MAX_LENGTH (74).
    variable_code: Mapped[str] = mapped_column(String(64))
    site_ref: Mapped[str] = mapped_column(String(74, collation=BINARY_COLLATION))
    observed_at_precision: Mapped[str] = mapped_column(String(16))
    site: Mapped[dict[str, object]] = mapped_column(JSONB)
    value: Mapped[float | None] = mapped_column(Double)
    unit: Mapped[str | None] = mapped_column(String(64))
    quality: Mapped[str] = mapped_column(String(16))
    ingested_run_id: Mapped[UUID]


class RasterAssetRow(Base):
    """Row model of the ``raster_assets`` table: one row per ``RasterAsset``.

    Implements: Adapter (ORM row model of ``SqlAlchemyRasterAssetCatalog``).

    Attributes:
        id: Primary key, the asset id (UUIDv7).
        dataset_version_id: The dataset version it belongs to.
        stac_id: The STAC item id, unique within the version.
        footprint: The covered area, WGS84 polygon or multipolygon.
        acquired_at: When the scene was acquired, UTC, as recorded.
        acquired_at_precision: ``DatePrecision`` of ``acquired_at``.
        acquired_start: Derived: start of the acquisition period.
        platform: The satellite or instrument platform.
        cloud_cover: Percentage of cloud, if known.
        bands: ``StacBand`` values as a JSON array.
        assets: ``StacAsset`` values as a JSON array.
        version: Optimistic-concurrency version.
        created_at: When it was catalogued, UTC.
        updated_at: When it last changed, UTC.
    """

    __tablename__ = RASTER_ASSETS_TABLE
    __table_args__ = (
        # Named uq_raster_assets_dataset_version_id by the convention.
        UniqueConstraint("dataset_version_id", "stac_id"),
        CheckConstraint(
            "cloud_cover IS NULL OR (cloud_cover >= 0 AND cloud_cover <= 100)",
            name="cloud_cover_percentage",
        ),
        Index("ix_raster_assets_footprint_gist", "footprint", postgresql_using="gist"),
        # Serves the newest-acquisition-first keyset listing (read backwards).
        Index("ix_raster_assets_acquired_start_id", "acquired_start", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    dataset_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="RESTRICT")
    )
    # STAC_ID_PATTERN (128) and PLATFORM_MAX_LENGTH.
    stac_id: Mapped[str] = mapped_column(String(128))
    footprint: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="GEOMETRY", srid=WGS84_SRID, spatial_index=False)
    )
    acquired_at: Mapped[datetime]
    acquired_at_precision: Mapped[str] = mapped_column(String(16))
    acquired_start: Mapped[datetime]
    platform: Mapped[str] = mapped_column(String(64))
    cloud_cover: Mapped[float | None] = mapped_column(Double)
    bands: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    assets: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
