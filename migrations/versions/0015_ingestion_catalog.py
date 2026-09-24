"""feat(ingestion): create datasets, dataset_versions and ingestion_runs.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-23T00:00:00+00:00

Schema migration only; no data is written here.

Creates the ingestion catalog exactly as
``yakhnama.modules.ingestion.infrastructure.orm`` declares it:

- ``datasets``: one row per catalog entry, ``code`` unique with the binary ``"C"``
  collation (it is the keyset sort key of the catalog listing, which must order like
  Python), the licence as JSONB, the spatial coverage as a WGS84 rectangle with an
  explicit GiST index, the temporal coverage as start and end instants each with its
  precision (checked to be complete in pairs, and no end without a start);
- ``dataset_versions``: one row per release, unique ``(dataset_id, label)``;
- ``ingestion_runs``: one row per run, counts and report as JSONB, indexed by
  ``(dataset_version_id, created_at)`` for the newest-first listings.

Foreign keys run ``ingestion_runs -> dataset_versions -> datasets``, all ``ON DELETE
RESTRICT``: nothing in the catalog is ever deleted. ``triggered_by`` is a user id of
the identity module and carries no foreign key.

Also adds the GIN index ``ix_events_source_ids_gin`` on ``events.source_ids``
(Q-T7-b), which serves the JSONB containment test of
``is_source_cited_by_public_event``; 0012 created ``source_ids`` without one. It is
built without ``CONCURRENTLY`` on purpose: the ``events`` table is small at this
stage (moderator-created records only), so the brief lock is harmless and the
migration keeps a single transaction.

Lengths and names are literals on purpose: a migration never imports models.
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the three catalog tables and the ``events.source_ids`` GIN index."""
    op.create_table(
        "datasets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64, collation="C"), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("publisher", sa.String(length=200), nullable=False),
        sa.Column("licence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "spatial_bbox",
            geoalchemy2.types.Geometry(
                geometry_type="POLYGON", srid=4326, spatial_index=False
            ),
            nullable=True,
        ),
        sa.Column("temporal_start", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("temporal_start_precision", sa.String(length=16), nullable=True),
        sa.Column("temporal_end", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("temporal_end_precision", sa.String(length=16), nullable=True),
        sa.Column("update_frequency", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("description", sa.String(length=4000), nullable=True),
        sa.Column("homepage_url", sa.String(length=2048), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(temporal_start IS NULL) = (temporal_start_precision IS NULL)",
            name=op.f("ck_datasets_temporal_start_complete"),
        ),
        sa.CheckConstraint(
            "(temporal_end IS NULL) = (temporal_end_precision IS NULL)",
            name=op.f("ck_datasets_temporal_end_complete"),
        ),
        sa.CheckConstraint(
            "temporal_end IS NULL OR temporal_start IS NOT NULL",
            name=op.f("ck_datasets_temporal_end_needs_start"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_datasets")),
        sa.UniqueConstraint("code", name=op.f("uq_datasets_code")),
    )
    op.create_index(
        "ix_datasets_spatial_bbox_gist",
        "datasets",
        ["spatial_bbox"],
        postgresql_using="gist",
    )
    op.create_index(op.f("ix_datasets_status"), "datasets", ["status"])

    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("retrieved_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("retrieved_at_precision", sa.String(length=16), nullable=False),
        sa.Column("input_checksum", sa.String(length=64), nullable=False),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name=op.f("fk_dataset_versions_dataset_id_datasets"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_versions")),
        sa.UniqueConstraint(
            "dataset_id", "label", name=op.f("uq_dataset_versions_dataset_id")
        ),
    )

    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("adapter_name", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("counts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input_checksum", sa.String(length=64), nullable=True),
        sa.Column("triggered_by", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_versions.id"],
            name=op.f("fk_ingestion_runs_dataset_version_id_dataset_versions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingestion_runs")),
    )
    op.create_index(
        "ix_ingestion_runs_dataset_version_id_created_at",
        "ingestion_runs",
        ["dataset_version_id", "created_at"],
    )
    op.create_index(op.f("ix_ingestion_runs_status"), "ingestion_runs", ["status"])

    op.create_index(
        "ix_events_source_ids_gin", "events", ["source_ids"], postgresql_using="gin"
    )


def downgrade() -> None:
    """Drop everything ``upgrade`` created, in reverse order."""
    op.drop_index("ix_events_source_ids_gin", table_name="events")
    op.drop_index(op.f("ix_ingestion_runs_status"), table_name="ingestion_runs")
    op.drop_index(
        "ix_ingestion_runs_dataset_version_id_created_at", table_name="ingestion_runs"
    )
    op.drop_table("ingestion_runs")
    op.drop_table("dataset_versions")
    op.drop_index(op.f("ix_datasets_status"), table_name="datasets")
    op.drop_index("ix_datasets_spatial_bbox_gist", table_name="datasets")
    op.drop_table("datasets")
