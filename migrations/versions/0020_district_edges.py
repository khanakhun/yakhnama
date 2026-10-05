"""feat(geography): store shared district edge snapshots.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-05T00:00:00+00:00

Schema migration only; no data is written here.

Creates ``district_edge_sets`` (one immutable snapshot per boundary load, with the
attribution of its source: source, licence, dataset version, download time and the
file's SHA-256), ``district_edges`` (the line two districts of a snapshot share,
WGS84, with an explicit GiST index) and ``district_centroids`` (the place centroids a
snapshot's load set, kept as provenance for the next load), exactly as
``yakhnama.modules.geography.infrastructure.orm`` declares them (ADR 0021). Edges
and centroids go with their snapshot (``ON DELETE CASCADE``). Lengths and names are
literals on purpose: a migration never imports models or their constants.
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the snapshot, edge and centroid tables with their indexes."""
    op.create_table(
        "district_edge_sets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("region_code", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=500), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("licence", sa.String(length=100), nullable=False),
        sa.Column("licence_url", sa.String(length=2048), nullable=False),
        sa.Column("dataset_version", sa.String(length=100), nullable=False),
        sa.Column("retrieved_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_district_edge_sets")),
    )
    op.create_index(
        op.f("ix_district_edge_sets_created_at"),
        "district_edge_sets",
        ["created_at"],
    )

    op.create_table(
        "district_edges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("edge_set_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("source_code_a", sa.String(length=32), nullable=False),
        sa.Column("source_code_b", sa.String(length=32), nullable=False),
        sa.Column("place_code_a", sa.String(length=64), nullable=True),
        sa.Column("place_code_b", sa.String(length=64), nullable=True),
        sa.Column(
            "geometry",
            geoalchemy2.types.Geometry(
                geometry_type="GEOMETRY", srid=4326, spatial_index=False
            ),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["edge_set_id"],
            ["district_edge_sets.id"],
            name=op.f("fk_district_edges_edge_set_id_district_edge_sets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_district_edges")),
        sa.UniqueConstraint(
            "edge_set_id",
            "position",
            name="uq_district_edges_edge_set_id_position",
        ),
        sa.UniqueConstraint(
            "edge_set_id",
            "source_code_a",
            "source_code_b",
            name="uq_district_edges_edge_set_id_source_code_a_source_code_b",
        ),
    )
    op.create_index(
        "ix_district_edges_geometry_gist",
        "district_edges",
        ["geometry"],
        postgresql_using="gist",
    )

    op.create_table(
        "district_centroids",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("edge_set_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("source_code", sa.String(length=32), nullable=False),
        sa.Column("place_code", sa.String(length=64), nullable=False),
        sa.Column(
            "point",
            geoalchemy2.types.Geometry(
                geometry_type="POINT", srid=4326, spatial_index=False
            ),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["edge_set_id"],
            ["district_edge_sets.id"],
            name=op.f("fk_district_centroids_edge_set_id_district_edge_sets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_district_centroids")),
        sa.UniqueConstraint(
            "edge_set_id",
            "place_code",
            name="uq_district_centroids_edge_set_id_place_code",
        ),
        sa.UniqueConstraint(
            "edge_set_id",
            "position",
            name="uq_district_centroids_edge_set_id_position",
        ),
    )
    op.create_index(
        "ix_district_centroids_point_gist",
        "district_centroids",
        ["point"],
        postgresql_using="gist",
    )


def downgrade() -> None:
    """Drop the centroid, edge and snapshot tables, in reverse order."""
    op.drop_index(
        "ix_district_centroids_point_gist",
        table_name="district_centroids",
        postgresql_using="gist",
    )
    op.drop_table("district_centroids")
    op.drop_index(
        "ix_district_edges_geometry_gist",
        table_name="district_edges",
        postgresql_using="gist",
    )
    op.drop_table("district_edges")
    op.drop_index(
        op.f("ix_district_edge_sets_created_at"), table_name="district_edge_sets"
    )
    op.drop_table("district_edge_sets")
