"""feat(ingestion): create observations and raster_assets.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-23T00:00:00+00:00

Schema migration only; no data is written here.

Creates the two ingestion data tables exactly as
``yakhnama.modules.ingestion.infrastructure.orm`` declares them.

``observations`` is the long, narrow time series, **hypertable-ready**:

- the primary key is ``(observed_at, dataset_version_id, variable_code, site_ref)``,
  the observation's natural key with the time column first. TimescaleDB requires
  every unique index of a hypertable to include the partitioning column, so
  ``SELECT create_hypertable('observations', 'observed_at', migrate_data => true)``
  can be run later without changing a column, the key or any query;
- there are **no foreign keys** from this table (``dataset_version_id`` and
  ``ingested_run_id`` are lineage ids the pipeline writes in the run's own
  transaction), so partitioning is never blocked by a constraint;
- the primary key's b-tree already serves time-range scans on ``observed_at``, so
  no separate time index is created (TimescaleDB adds its own when converting);
  ``ix_observations_dataset_version_id_variable_code`` covers ``(dataset_version_id,
  variable_code, observed_at, site_ref)`` and serves the time-series query in its
  exact keyset order and the count per version;
- ``site_ref`` uses the binary ``"C"`` collation, the keyset order the application
  and its cursors assume; ``site`` is the station or grid-cell JSON; ``value`` and
  ``unit`` are null together (a missing value).

``raster_assets`` holds one STAC item per row: ``(dataset_version_id, stac_id)``
unique, a WGS84 ``footprint`` (any geometry type, polygons in practice) with an
explicit GiST index for the box search, the acquisition instant with its precision
plus the derived ``acquired_start`` (start of the period) indexed with ``id`` for the
newest-first listing, ``cloud_cover`` checked to be a percentage, and bands and
assets as JSONB. Its foreign key to ``dataset_versions`` is ``ON DELETE RESTRICT``.

Lengths and names are literals on purpose: a migration never imports models.
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``observations`` and ``raster_assets`` with their keys and indexes."""
    # Hypertable-ready: time-first primary key and no foreign keys (see above).
    op.create_table(
        "observations",
        sa.Column("observed_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("variable_code", sa.String(length=64), nullable=False),
        sa.Column("site_ref", sa.String(length=74, collation="C"), nullable=False),
        sa.Column("observed_at_precision", sa.String(length=16), nullable=False),
        sa.Column("site", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("value", sa.Double(), nullable=True),
        sa.Column("unit", sa.String(length=64), nullable=True),
        sa.Column("quality", sa.String(length=16), nullable=False),
        sa.Column("ingested_run_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "(value IS NULL) = (unit IS NULL)",
            name=op.f("ck_observations_value_complete"),
        ),
        sa.PrimaryKeyConstraint(
            "observed_at",
            "dataset_version_id",
            "variable_code",
            "site_ref",
            name=op.f("pk_observations"),
        ),
    )
    op.create_index(
        "ix_observations_dataset_version_id_variable_code",
        "observations",
        ["dataset_version_id", "variable_code", "observed_at", "site_ref"],
    )

    op.create_table(
        "raster_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("stac_id", sa.String(length=128), nullable=False),
        sa.Column(
            "footprint",
            geoalchemy2.types.Geometry(
                geometry_type="GEOMETRY", srid=4326, spatial_index=False
            ),
            nullable=False,
        ),
        sa.Column("acquired_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("acquired_at_precision", sa.String(length=16), nullable=False),
        sa.Column(
            "acquired_start", postgresql.TIMESTAMP(timezone=True), nullable=False
        ),
        sa.Column("platform", sa.String(length=64), nullable=False),
        sa.Column("cloud_cover", sa.Double(), nullable=True),
        sa.Column("bands", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("assets", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "cloud_cover IS NULL OR (cloud_cover >= 0 AND cloud_cover <= 100)",
            name=op.f("ck_raster_assets_cloud_cover_percentage"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_versions.id"],
            name=op.f("fk_raster_assets_dataset_version_id_dataset_versions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_raster_assets")),
        sa.UniqueConstraint(
            "dataset_version_id",
            "stac_id",
            name=op.f("uq_raster_assets_dataset_version_id"),
        ),
    )
    op.create_index(
        "ix_raster_assets_footprint_gist",
        "raster_assets",
        ["footprint"],
        postgresql_using="gist",
    )
    op.create_index(
        "ix_raster_assets_acquired_start_id",
        "raster_assets",
        ["acquired_start", "id"],
    )


def downgrade() -> None:
    """Drop everything ``upgrade`` created, in reverse order."""
    op.drop_index("ix_raster_assets_acquired_start_id", table_name="raster_assets")
    op.drop_index("ix_raster_assets_footprint_gist", table_name="raster_assets")
    op.drop_table("raster_assets")
    op.drop_index(
        "ix_observations_dataset_version_id_variable_code", table_name="observations"
    )
    op.drop_table("observations")
