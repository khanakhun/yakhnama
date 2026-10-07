"""fix(reports): set a guest report's reservation fields together.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-05T00:00:00+00:00

Schema migration only; no data is written here.

Adds ``ck_guest_submissions_reserved_fields_together``: the content fingerprint, the
reserved source id and the submission time are all set (reserved or filed) or all
empty (open), as ``GuestSubmission`` guarantees (ADR 0020). ``0022`` filled
``source_id`` for every submission filed before ``0021``.

Downgrade drops the check.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the check."""
    op.create_check_constraint(
        op.f("ck_guest_submissions_reserved_fields_together"),
        "guest_submissions",
        "(content_fingerprint IS NULL) = (source_id IS NULL) "
        "AND (content_fingerprint IS NULL) = (submitted_at IS NULL)",
    )


def downgrade() -> None:
    """Drop the check."""
    op.drop_constraint(
        op.f("ck_guest_submissions_reserved_fields_together"),
        "guest_submissions",
        type_="check",
    )
