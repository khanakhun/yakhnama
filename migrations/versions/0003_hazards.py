"""feat(hazards): create hazard types.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-23T00:00:00+00:00

Schema migration only; no data is written here (the taxonomy is loaded by the seed
command, not by migrations).

Creates ``hazard_types`` exactly as ``yakhnama.modules.hazards.infrastructure.orm``
declares it. Parents are linked by the unique, immutable ``code`` (the domain links
them that way), with ``ON DELETE RESTRICT`` because codes are retired, never deleted.
Structured values are JSONB holding the JSON form of the domain value objects.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``hazard_types`` and the index on its parent link."""
    op.create_table(
        "hazard_types",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("parent_code", sa.String(length=64), nullable=True),
        sa.Column("labels", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "description", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("alignment", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attributes_schema", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("retirement", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_code"],
            ["hazard_types.code"],
            name=op.f("fk_hazard_types_parent_code_hazard_types"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hazard_types")),
        sa.UniqueConstraint("code", name=op.f("uq_hazard_types_code")),
    )
    op.create_index(
        op.f("ix_hazard_types_parent_code"), "hazard_types", ["parent_code"]
    )


def downgrade() -> None:
    """Drop everything ``upgrade`` created, in reverse order."""
    op.drop_index(op.f("ix_hazard_types_parent_code"), table_name="hazard_types")
    op.drop_table("hazard_types")
