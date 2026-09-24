"""feat(media): create media_assets.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-23T00:00:00+00:00

Schema migration only; no data is written here.

Creates ``media_assets`` exactly as ``yakhnama.modules.media.infrastructure.orm``
declares it: one row per uploaded file. EXIF facts are split into ``exif`` (JSONB:
capture time with precision, camera) and ``exif_location`` (a private WGS84 point);
a check forbids a location without EXIF. A partial index on ``(owner_id, sha256)``
for completed uploads serves the per-owner deduplication lookup, and ``report_id``
is indexed for a report's assets. Owner, report and source ids have no foreign
keys, because they belong to other modules. ``exif_location`` has no GiST index: it
is never searched spatially. Lengths and names are literals on purpose: a migration
never imports models or their constants.
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``media_assets`` with its check and indexes."""
    op.create_table(
        "media_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("original_key", sa.String(length=256), nullable=False),
        sa.Column("public_key", sa.String(length=256), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("mime_type", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("exif", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "exif_location",
            geoalchemy2.types.Geometry(
                geometry_type="POINT", srid=4326, spatial_index=False
            ),
            nullable=True,
        ),
        sa.Column("upload_status", sa.String(length=16), nullable=False),
        sa.Column("scan_status", sa.String(length=16), nullable=False),
        sa.Column("moderation_status", sa.String(length=16), nullable=False),
        sa.Column("sensitivity", sa.String(length=32), nullable=False),
        sa.Column("moderation_reason", sa.String(length=500), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "exif_location IS NULL OR exif IS NOT NULL",
            name=op.f("ck_media_assets_exif_location_needs_exif"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_assets")),
    )
    op.create_index(
        "ix_media_assets_owner_id_sha256_completed",
        "media_assets",
        ["owner_id", "sha256"],
        postgresql_where=sa.text("upload_status = 'completed'"),
    )
    op.create_index(op.f("ix_media_assets_report_id"), "media_assets", ["report_id"])


def downgrade() -> None:
    """Drop everything ``upgrade`` created, in reverse order."""
    op.drop_index(op.f("ix_media_assets_report_id"), table_name="media_assets")
    op.drop_index(
        "ix_media_assets_owner_id_sha256_completed",
        table_name="media_assets",
        postgresql_where=sa.text("upload_status = 'completed'"),
    )
    op.drop_table("media_assets")
