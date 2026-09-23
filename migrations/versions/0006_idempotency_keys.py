"""feat(platform): create idempotency keys.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-23T00:00:00+00:00

Schema migration only; no data is written here.

Creates ``idempotency_keys`` exactly as ``yakhnama.platform.idempotency.models``
declares it (ADR 0016): one row per ``(scope, key)``, a pending reservation while
``status_code`` is ``NULL`` and a stored 2xx response afterwards. Two check
constraints keep a row either fully pending or fully stored, and only with a success
status; the index on ``expires_at`` serves the purge of lapsed rows. ``scope`` is a
SHA-256 of the principal, never the subject itself. Lengths and names are literals on
purpose: a migration never imports models or their constants.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``idempotency_keys`` with its constraints and the expiry index."""
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=255), nullable=False),
        sa.Column("key", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("path", sa.String(length=2048), nullable=False),
        sa.Column("status_code", sa.SmallInteger(), nullable=True),
        sa.Column("response_body", sa.LargeBinary(), nullable=True),
        sa.Column(
            "response_headers", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(status_code IS NULL) = (response_body IS NULL)"
            " AND (status_code IS NULL) = (response_headers IS NULL)",
            name=op.f("ck_idempotency_keys_response_complete"),
        ),
        sa.CheckConstraint(
            "status_code IS NULL OR (status_code >= 200 AND status_code <= 299)",
            name=op.f("ck_idempotency_keys_status_code_success"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_keys")),
        sa.UniqueConstraint("scope", "key", name="uq_idempotency_keys_scope_key"),
    )
    op.create_index(
        "ix_idempotency_keys_expires_at", "idempotency_keys", ["expires_at"]
    )


def downgrade() -> None:
    """Drop everything ``upgrade`` created, in reverse order."""
    op.drop_index("ix_idempotency_keys_expires_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
