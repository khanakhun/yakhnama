"""feat(verification): create verification_cases.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-23T00:00:00+00:00

Schema migration only; no data is written here.

Creates ``verification_cases`` exactly as
``yakhnama.modules.verification.infrastructure.orm`` declares it: one row per case,
with its whole transition ``history`` as a JSONB array (replayed against the state
table on read). ``(target_kind, target_id)`` is unique, so a target has at most one
case for life; it also serves the events read model's join on
``target_kind = 'event'``. Indexes on ``state``, ``assigned_to`` and
``(created_at, id)`` serve the moderator listings. The target id has no foreign key,
because reports, events and claims belong to other modules. Lengths and names are
literals on purpose: a migration never imports models or their constants.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``verification_cases`` with its unique target and indexes."""
    op.create_table(
        "verification_cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("target_kind", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("initial_state", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("history", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("assigned_to", sa.Uuid(), nullable=True),
        sa.Column("opened_by", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_cases")),
        sa.UniqueConstraint(
            "target_kind",
            "target_id",
            name=op.f("uq_verification_cases_target_kind"),
        ),
    )
    op.create_index(
        op.f("ix_verification_cases_assigned_to"),
        "verification_cases",
        ["assigned_to"],
    )
    op.create_index(
        "ix_verification_cases_created_at_id",
        "verification_cases",
        ["created_at", "id"],
    )
    op.create_index(
        op.f("ix_verification_cases_state"), "verification_cases", ["state"]
    )


def downgrade() -> None:
    """Drop everything ``upgrade`` created, in reverse order."""
    op.drop_index(op.f("ix_verification_cases_state"), table_name="verification_cases")
    op.drop_index(
        "ix_verification_cases_created_at_id", table_name="verification_cases"
    )
    op.drop_index(
        op.f("ix_verification_cases_assigned_to"), table_name="verification_cases"
    )
    op.drop_table("verification_cases")
