"""feat(audit): create the append-only audit_entries table.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-23T00:00:00+00:00

Schema migration only; no data is written here.

Creates ``audit_entries`` exactly as ``yakhnama.modules.audit.infrastructure.orm``
declares it: ids, codes and digests only, ``event_id`` unique so a redelivered
event is recorded once, and ``recorded_at`` set by the database (``now()``). The
two indexes serve the per-target and the whole-log newest-first listings; they are
ascending because a B-tree is read backwards just as fast.

**Append-only guard (proposed).** A ``BEFORE UPDATE OR DELETE ... FOR EACH ROW``
trigger calls ``audit_entries_reject_change()``, which raises SQLSTATE ``P0001``
with a fixed message, so no code path (not even an ad-hoc session) can change or
remove an entry. ``TRUNCATE`` is not covered, because it is a table-level
operation granted separately; production roles must not be granted it (open
question for the maintainer, together with a dedicated role that has only
``INSERT`` and ``SELECT`` on the table). Alembic does not compare triggers, so the
guard is written here as raw SQL with a matching downgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CREATE_GUARD_FUNCTION = """
CREATE FUNCTION audit_entries_reject_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_entries is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'P0001';
END;
$$
"""

_CREATE_GUARD_TRIGGER = """
CREATE TRIGGER audit_entries_append_only
BEFORE UPDATE OR DELETE ON audit_entries
FOR EACH ROW EXECUTE FUNCTION audit_entries_reject_change()
"""


def upgrade() -> None:
    """Create ``audit_entries``, its indexes and the append-only trigger."""
    op.create_table(
        "audit_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("actor_kind", sa.String(length=8), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("before_digest", sa.String(length=71), nullable=True),
        sa.Column("after_digest", sa.String(length=71), nullable=True),
        sa.Column("payload_digest", sa.String(length=71), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_entries")),
        sa.UniqueConstraint("event_id", name=op.f("uq_audit_entries_event_id")),
    )
    op.create_index(
        "ix_audit_entries_occurred_at_id", "audit_entries", ["occurred_at", "id"]
    )
    op.create_index(
        "ix_audit_entries_target_type_target_id_occurred_at",
        "audit_entries",
        ["target_type", "target_id", "occurred_at", "id"],
    )
    op.execute(_CREATE_GUARD_FUNCTION)
    op.execute(_CREATE_GUARD_TRIGGER)


def downgrade() -> None:
    """Drop everything ``upgrade`` created, in reverse order."""
    op.execute("DROP TRIGGER audit_entries_append_only ON audit_entries")
    op.execute("DROP FUNCTION audit_entries_reject_change()")
    op.drop_index(
        "ix_audit_entries_target_type_target_id_occurred_at",
        table_name="audit_entries",
    )
    op.drop_index("ix_audit_entries_occurred_at_id", table_name="audit_entries")
    op.drop_table("audit_entries")
