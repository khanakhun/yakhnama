"""feat(exchange): record the visibility of every export job.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-23T00:00:00+00:00

Adds ``export_jobs.visibility`` (``public`` or ``moderation``), fixed when an
export is requested from the requesting actor's roles, so a download link is
handed out only to readers who may see that view at read time (Phase 4 security
review). Existing rows get the server default ``public``, except ``reports``
exports, which were only ever requested by moderators and are relabelled
``moderation`` (the domain refuses a public reports export). Every stored sidecar
gets the same ``visibility`` key, because the job invariant requires the sidecar
to describe the job's visibility.

Downgrade removes the key from the sidecars (the previous ``MetadataSidecar``
forbids unknown keys) and drops the column.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``visibility`` with default ``public``; relabel reports exports."""
    op.add_column(
        "export_jobs",
        sa.Column(
            "visibility",
            sa.String(length=16),
            server_default=sa.text("'public'"),
            nullable=False,
        ),
    )
    op.execute(
        "UPDATE export_jobs SET visibility = 'moderation' WHERE dataset = 'reports'"
    )
    op.execute(
        "UPDATE export_jobs SET sidecar = jsonb_set(sidecar, '{visibility}', "
        "to_jsonb(visibility)) WHERE sidecar IS NOT NULL"
    )


def downgrade() -> None:
    """Remove ``visibility`` from the sidecars, then drop the column."""
    op.execute(
        "UPDATE export_jobs SET sidecar = sidecar - 'visibility' "
        "WHERE sidecar IS NOT NULL"
    )
    op.drop_column("export_jobs", "visibility")
