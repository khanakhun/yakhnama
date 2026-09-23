"""feat(impacts): create infrastructure_assets, impact_claims and damage_records.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-23T00:00:00+00:00

Schema migration only; no data is written here.

Creates the tables exactly as ``yakhnama.modules.impacts.infrastructure.claims_orm``
declares them. ``infrastructure_assets`` has a unique ``osm_id`` and a WGS84
``location`` point with an explicit GiST index. ``impact_claims`` stores impact
values long and narrow, one metric and one JSONB ``value`` (discriminated by
``kind``) per row; ``metric_code`` references ``impact_metrics.code``,
``scope_asset_id`` references ``infrastructure_assets`` and ``supersedes_id``
references ``impact_claims`` and is unique, so at most one correction replaces a
claim. Indexes on ``(event_id, metric_code)``, ``(event_id, created_at, id)`` and
``source_id`` serve the per-event reads and source lookups. ``damage_records``
references ``infrastructure_assets`` and is indexed by ``event_id`` and
``asset_id``. Every foreign key is ``ON DELETE RESTRICT``; event, source, place and
user ids have none, because they belong to other modules. Lengths and names are
literals on purpose: a migration never imports models or their constants.
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the assets, claims and damage tables with their keys and indexes."""
    op.create_table(
        "infrastructure_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("osm_id", sa.String(length=32), nullable=True),
        sa.Column(
            "location",
            geoalchemy2.types.Geometry(
                geometry_type="POINT", srid=4326, spatial_index=False
            ),
            nullable=True,
        ),
        sa.Column("place_code", sa.String(length=64), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_infrastructure_assets")),
        sa.UniqueConstraint("osm_id", name=op.f("uq_infrastructure_assets_osm_id")),
    )
    op.create_index(
        "ix_infrastructure_assets_location_gist",
        "infrastructure_assets",
        ["location"],
        postgresql_using="gist",
    )

    op.create_table(
        "impact_claims",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("metric_code", sa.String(length=64), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("claimed_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("claimed_at_precision", sa.String(length=16), nullable=False),
        sa.Column("recorded_by", sa.Uuid(), nullable=False),
        sa.Column("scope_place_code", sa.String(length=64), nullable=True),
        sa.Column("scope_asset_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("retraction_reason", sa.String(length=1000), nullable=True),
        sa.Column("retracted_by", sa.Uuid(), nullable=True),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["metric_code"],
            ["impact_metrics.code"],
            name=op.f("fk_impact_claims_metric_code_impact_metrics"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scope_asset_id"],
            ["infrastructure_assets.id"],
            name=op.f("fk_impact_claims_scope_asset_id_infrastructure_assets"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["impact_claims.id"],
            name=op.f("fk_impact_claims_supersedes_id_impact_claims"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_impact_claims")),
        sa.UniqueConstraint(
            "supersedes_id", name=op.f("uq_impact_claims_supersedes_id")
        ),
    )
    op.create_index(
        "ix_impact_claims_event_id_created_at_id",
        "impact_claims",
        ["event_id", "created_at", "id"],
    )
    op.create_index(
        "ix_impact_claims_event_id_metric_code",
        "impact_claims",
        ["event_id", "metric_code"],
    )
    op.create_index(op.f("ix_impact_claims_source_id"), "impact_claims", ["source_id"])

    op.create_table(
        "damage_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("recorded_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("recorded_at_precision", sa.String(length=16), nullable=False),
        sa.Column("recorded_by", sa.Uuid(), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("retraction_reason", sa.String(length=1000), nullable=True),
        sa.Column("retracted_by", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["infrastructure_assets.id"],
            name=op.f("fk_damage_records_asset_id_infrastructure_assets"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_damage_records")),
    )
    op.create_index(op.f("ix_damage_records_asset_id"), "damage_records", ["asset_id"])
    op.create_index(op.f("ix_damage_records_event_id"), "damage_records", ["event_id"])


def downgrade() -> None:
    """Drop everything ``upgrade`` created, in reverse order."""
    op.drop_index(op.f("ix_damage_records_event_id"), table_name="damage_records")
    op.drop_index(op.f("ix_damage_records_asset_id"), table_name="damage_records")
    op.drop_table("damage_records")
    op.drop_index(op.f("ix_impact_claims_source_id"), table_name="impact_claims")
    op.drop_index("ix_impact_claims_event_id_metric_code", table_name="impact_claims")
    op.drop_index("ix_impact_claims_event_id_created_at_id", table_name="impact_claims")
    op.drop_table("impact_claims")
    op.drop_index(
        "ix_infrastructure_assets_location_gist", table_name="infrastructure_assets"
    )
    op.drop_table("infrastructure_assets")
