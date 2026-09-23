"""feat(platform): create database extensions and the outbox table.

Revision ID: 0001
Revises:
Create Date: 2026-09-23T00:00:00+00:00

Schema migration only; no data is written here.

Creates the three extensions the schema relies on (ADR 0002): ``postgis`` for WGS84
geometries, ``pg_trgm`` for fuzzy place-name matching and ``unaccent`` for
accent-insensitive search. Then creates ``outbox_messages`` exactly as
``yakhnama.platform.outbox.models.OutboxMessage`` declares it (ADR 0007). Column
lengths are written out as literals on purpose: a migration never imports models or
their constants, because those change and a committed migration must not.

Downgrade drops the outbox table but leaves the extensions installed (proposed, see
``downgrade``).
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTENSIONS: Final = ("postgis", "pg_trgm", "unaccent")


def upgrade() -> None:
    """Create the extensions, then ``outbox_messages`` and its relay index."""
    for extension in EXTENSIONS:
        # IF NOT EXISTS: the compose and CI images already create postgis (and the
        # compose init script all three) when the data volume is initialised.
        op.execute(sa.text(f"CREATE EXTENSION IF NOT EXISTS {extension}"))

    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurred_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("published_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "attempts >= 0",
            name=op.f("ck_outbox_messages_attempts_non_negative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_messages")),
    )
    op.create_index(
        "ix_outbox_messages_published_at_created_at",
        "outbox_messages",
        ["published_at", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the index and the table; keep the extensions (proposed).

    The extensions are left installed on purpose: other schemas or tools sharing the
    database (PostGIS's own ``tiger`` and ``topology`` schemas in the official image,
    for one) depend on ``postgis``, and ``DROP EXTENSION`` would either fail or, with
    ``CASCADE``, destroy their objects. Leaving them is harmless because ``upgrade``
    uses ``IF NOT EXISTS``. This is a proposed default awaiting maintainer review.
    """
    op.drop_index(
        "ix_outbox_messages_published_at_created_at", table_name="outbox_messages"
    )
    op.drop_table("outbox_messages")
