"""feat(provenance): create sources.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-23T00:00:00+00:00

Schema migration only; no data is written here.

Creates ``sources`` exactly as ``yakhnama.modules.provenance.infrastructure.orm``
declares it: one row per provenance record, with ``licence`` as a JSONB object and
``retrieved_at`` stored as an instant plus its precision (a check keeps the pair
both set or both ``NULL``). ``owner_actor_id`` and ``organization_id`` have no
foreign keys, because users and organisations belong to the ``identity`` module.
Indexes serve filtering by type, lookups by owner and the newest-first keyset
listing on ``(created_at, id)``. Lengths and names are literals on purpose: a
migration never imports models or their constants.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``sources`` with its check constraint and indexes."""
    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("citation", sa.String(length=1000), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column("licence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("retrieved_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("retrieved_at_precision", sa.String(length=16), nullable=True),
        sa.Column("publisher", sa.String(length=200), nullable=True),
        sa.Column("language", sa.String(length=8), nullable=True),
        sa.Column("owner_actor_id", sa.Uuid(), nullable=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("is_referenced", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(retrieved_at IS NULL) = (retrieved_at_precision IS NULL)",
            name=op.f("ck_sources_retrieved_at_with_precision"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sources")),
    )
    op.create_index("ix_sources_created_at_id", "sources", ["created_at", "id"])
    op.create_index(op.f("ix_sources_owner_actor_id"), "sources", ["owner_actor_id"])
    op.create_index(op.f("ix_sources_source_type"), "sources", ["source_type"])


def downgrade() -> None:
    """Drop everything ``upgrade`` created, in reverse order."""
    op.drop_index(op.f("ix_sources_source_type"), table_name="sources")
    op.drop_index(op.f("ix_sources_owner_actor_id"), table_name="sources")
    op.drop_index("ix_sources_created_at_id", table_name="sources")
    op.drop_table("sources")
