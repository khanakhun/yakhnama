"""fix(reports): backfill the source of filed guest submissions.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-05T00:00:00+00:00

Data migration only. It runs after ``0021`` added ``guest_submissions.source_id`` as
nullable and before ``0023`` requires it on every reserved or filed submission
(expand, backfill, contract). A submission filed before ``0021`` has a report, and
the report's ``source_id`` is the platform source the reservation would have named.

One statement, not batches: guest submissions are new (``0019``) and capped at a
few hundred filed per hour, and a single ``UPDATE`` also renders in offline mode.

Downgrade is a deliberate no-op: downgrading ``0021`` drops the column anyway. Upgrade
is safe to re-run; it only touches rows whose ``source_id`` is still empty.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BACKFILL = (
    "UPDATE guest_submissions AS g SET source_id = r.source_id "
    "FROM reports AS r "
    "WHERE g.source_id IS NULL AND g.report_id = r.id"
)


def upgrade() -> None:
    """Copy each filed submission's report source."""
    op.execute(_BACKFILL)


def downgrade() -> None:
    """Do nothing; see the module docstring."""
