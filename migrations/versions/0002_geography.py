"""feat(geography): create places and place names.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-23T00:00:00+00:00

Schema migration only; no data is written here.

Creates ``places`` and ``place_names`` exactly as
``yakhnama.modules.geography.infrastructure.orm`` declares them: WGS84 geometry and
centroid columns with explicit GiST indexes (ADR 0002), a GIN trigram index
(``gin_trgm_ops``, from the ``pg_trgm`` extension created in 0001) on the folded
search form of every name, and a partial unique index allowing at most one preferred
name per place and language. Lengths and names are literals on purpose: a migration
never imports models or their constants.
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``places`` and ``place_names`` with their constraints and indexes."""
    op.create_table(
        "places",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("level", sa.String(length=32), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column(
            "geometry",
            geoalchemy2.types.Geometry(
                geometry_type="GEOMETRY", srid=4326, spatial_index=False
            ),
            nullable=True,
        ),
        sa.Column(
            "centroid",
            geoalchemy2.types.Geometry(
                geometry_type="POINT", srid=4326, spatial_index=False
            ),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("status_reason", sa.String(length=500), nullable=True),
        sa.Column("merged_into_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["merged_into_id"],
            ["places.id"],
            name=op.f("fk_places_merged_into_id_places"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["places.id"],
            name=op.f("fk_places_parent_id_places"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_places")),
        sa.UniqueConstraint("code", name=op.f("uq_places_code")),
    )
    op.create_index(
        "ix_places_centroid_gist", "places", ["centroid"], postgresql_using="gist"
    )
    op.create_index(
        "ix_places_geometry_gist", "places", ["geometry"], postgresql_using="gist"
    )
    op.create_index(op.f("ix_places_level"), "places", ["level"])
    op.create_index(op.f("ix_places_parent_id"), "places", ["parent_id"])

    op.create_table(
        "place_names",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("place_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("text", sa.String(length=200), nullable=False),
        sa.Column("text_folded", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("script", sa.String(length=8), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("is_preferred", sa.Boolean(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["place_id"],
            ["places.id"],
            name=op.f("fk_place_names_place_id_places"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_place_names")),
        sa.UniqueConstraint(
            "place_id", "position", name="uq_place_names_place_id_position"
        ),
        sa.UniqueConstraint(
            "place_id",
            "text",
            "language",
            "script",
            name="uq_place_names_place_id_text_language_script",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        "ix_place_names_text_folded_gin",
        "place_names",
        ["text_folded"],
        postgresql_using="gin",
        postgresql_ops={"text_folded": "gin_trgm_ops"},
    )
    op.create_index(
        "uq_place_names_place_id_language_preferred",
        "place_names",
        ["place_id", "language"],
        unique=True,
        postgresql_where=sa.text("is_preferred"),
    )


def downgrade() -> None:
    """Drop everything ``upgrade`` created, in reverse order."""
    op.drop_index(
        "uq_place_names_place_id_language_preferred", table_name="place_names"
    )
    op.drop_index("ix_place_names_text_folded_gin", table_name="place_names")
    op.drop_table("place_names")
    op.drop_index(op.f("ix_places_parent_id"), table_name="places")
    op.drop_index(op.f("ix_places_level"), table_name="places")
    op.drop_index("ix_places_geometry_gist", table_name="places")
    op.drop_index("ix_places_centroid_gist", table_name="places")
    op.drop_table("places")
