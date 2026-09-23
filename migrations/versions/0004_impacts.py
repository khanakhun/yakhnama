"""feat(impacts): create impact metrics.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-23T00:00:00+00:00

Schema migration only; no data is written here (the registry is loaded by the seed
command, not by migrations).

Creates ``impact_metrics`` exactly as ``yakhnama.modules.impacts.infrastructure.orm``
declares it: one row per registry entry, with structured values as JSONB holding the
JSON form of the domain value objects. Impact values themselves are stored long and
narrow in a later phase (ADR 0002).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``impact_metrics`` and the index on its category."""
    op.create_table(
        "impact_metrics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("labels", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "description", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("value_kind", sa.String(length=16), nullable=False),
        sa.Column("unit", sa.String(length=64), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("sendai", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "desinventar", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("aggregation", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("retirement", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_impact_metrics")),
        sa.UniqueConstraint("code", name=op.f("uq_impact_metrics_code")),
    )
    op.create_index(op.f("ix_impact_metrics_category"), "impact_metrics", ["category"])


def downgrade() -> None:
    """Drop everything ``upgrade`` created, in reverse order."""
    op.drop_index(op.f("ix_impact_metrics_category"), table_name="impact_metrics")
    op.drop_table("impact_metrics")
