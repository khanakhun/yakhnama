"""feat(reports): create reports.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-23T00:00:00+00:00

Schema migration only; no data is written here.

Creates ``reports`` exactly as ``yakhnama.modules.reports.infrastructure.orm``
declares it: one row per report revision. ``observation`` is the reporter's exact,
private WGS84 point with an explicit GiST index; ``media_ids`` (JSONB array of
UUID strings) and ``triage`` (JSONB object) are validated by the domain on read.
The revision chain references ``reports.id`` (``ON DELETE RESTRICT``) and
``supersedes_id`` is unique, so at most one revision replaces a report. Reporter,
organisation and source ids have no foreign keys, because they belong to other
modules. A check keeps the hazard guess complete (code and confidence together).
Lengths and names are literals on purpose: a migration never imports models or
their constants.
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``reports`` with its keys, check, b-tree and GiST indexes."""
    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("reporter_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("observed_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("observed_at_precision", sa.String(length=16), nullable=False),
        sa.Column(
            "observation",
            geoalchemy2.types.Geometry(
                geometry_type="POINT", srid=4326, spatial_index=False
            ),
            nullable=False,
        ),
        sa.Column("accuracy_metres", sa.Float(), nullable=True),
        sa.Column("description", sa.String(length=4000), nullable=False),
        sa.Column("original_language", sa.String(length=8), nullable=False),
        sa.Column("hazard_code", sa.String(length=64), nullable=True),
        sa.Column("hazard_confidence", sa.String(length=16), nullable=True),
        sa.Column("place_hint", sa.String(length=64), nullable=True),
        sa.Column("media_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("superseded_by_id", sa.Uuid(), nullable=True),
        sa.Column("withdrawal_reason", sa.String(length=500), nullable=True),
        sa.Column("triage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("submitted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(hazard_code IS NULL) = (hazard_confidence IS NULL)",
            name=op.f("ck_reports_hazard_guess_complete"),
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["reports.id"],
            name=op.f("fk_reports_superseded_by_id_reports"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["reports.id"],
            name=op.f("fk_reports_supersedes_id_reports"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reports")),
        sa.UniqueConstraint("supersedes_id", name=op.f("uq_reports_supersedes_id")),
    )
    op.create_index("ix_reports_created_at_id", "reports", ["created_at", "id"])
    op.create_index(op.f("ix_reports_hazard_code"), "reports", ["hazard_code"])
    op.create_index(
        "ix_reports_observation_gist",
        "reports",
        ["observation"],
        postgresql_using="gist",
    )
    op.create_index(op.f("ix_reports_observed_at"), "reports", ["observed_at"])
    op.create_index(op.f("ix_reports_reporter_id"), "reports", ["reporter_id"])
    op.create_index(op.f("ix_reports_status"), "reports", ["status"])


def downgrade() -> None:
    """Drop everything ``upgrade`` created, in reverse order."""
    op.drop_index(op.f("ix_reports_status"), table_name="reports")
    op.drop_index(op.f("ix_reports_reporter_id"), table_name="reports")
    op.drop_index(op.f("ix_reports_observed_at"), table_name="reports")
    op.drop_index("ix_reports_observation_gist", table_name="reports")
    op.drop_index(op.f("ix_reports_hazard_code"), table_name="reports")
    op.drop_index("ix_reports_created_at_id", table_name="reports")
    op.drop_table("reports")
