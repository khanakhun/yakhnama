"""feat(platform): add lease and dead-letter columns to the outbox.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-23T00:00:00+00:00

Schema migration only; no data is written here.

Adds to ``outbox_messages`` exactly what ``yakhnama.platform.outbox.models`` declares
for lease-based claiming and dead-lettering (ADR 0007, Phase 3 plan §2):

- ``leased_until``: a relay reserves a claimed row until this instant, so the claim
  transaction can commit before the subscribers run and a crashed relay's rows
  become claimable again once the lease lapses;
- ``dead_lettered_at``: set when a row used its last attempt;
- a check that a row is never both published and dead-lettered;
- an index on ``(published_at, leased_until, attempts)`` for the claim filter.

Both columns are nullable and have no default, so existing rows need no backfill:
``NULL`` means "not leased" and "not dead-lettered". Names are literals on purpose: a
migration never imports models or their constants.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the two columns, the check constraint and the claim index."""
    op.add_column(
        "outbox_messages",
        sa.Column("leased_until", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "outbox_messages",
        sa.Column(
            "dead_lettered_at", postgresql.TIMESTAMP(timezone=True), nullable=True
        ),
    )
    op.create_check_constraint(
        op.f("ck_outbox_messages_published_or_dead_lettered"),
        "outbox_messages",
        "published_at IS NULL OR dead_lettered_at IS NULL",
    )
    op.create_index(
        "ix_outbox_messages_published_at_leased_until_attempts",
        "outbox_messages",
        ["published_at", "leased_until", "attempts"],
        unique=False,
    )


def downgrade() -> None:
    """Drop everything ``upgrade`` added, in reverse order.

    The lease and dead-letter state of existing rows is lost; the rows themselves and
    their ``attempts`` stay, so a relay of the previous revision resumes on them.
    """
    op.drop_index(
        "ix_outbox_messages_published_at_leased_until_attempts",
        table_name="outbox_messages",
    )
    op.drop_constraint(
        op.f("ck_outbox_messages_published_or_dead_lettered"),
        "outbox_messages",
        type_="check",
    )
    op.drop_column("outbox_messages", "dead_lettered_at")
    op.drop_column("outbox_messages", "leased_until")
