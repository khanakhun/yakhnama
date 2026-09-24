"""feat(exchange): create export_jobs and import_jobs.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-23T00:00:00+00:00

Schema migration only; no data is written here.

Creates the exchange job tables exactly as
``yakhnama.modules.exchange.infrastructure.orm`` declares them:

- ``export_jobs``: one row per export; ``filters`` (always), ``artifact`` and
  ``sidecar`` (once completed) as JSONB, validated through the aggregate on read.
  ``ix_export_jobs_requested_by_requested_at`` (``requested_by, requested_at, id``)
  serves a user's own newest-first listing, ``ix_export_jobs_requested_at_id`` the
  moderators' listing of every job, and ``ix_export_jobs_status`` status lookups;
- ``import_jobs``: one row per import; ``source_artifact`` (always), ``report``
  (once produced) and ``writes`` (created event ids, lineage source, batches
  applied; empty for a dry run) as JSONB. It is read by id only, so it has no
  secondary index.

``requested_by`` is a user id of the identity module and carries no foreign key.
Lengths and names are literals on purpose: a migration never imports models.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``export_jobs`` with its listing indexes, then ``import_jobs``."""
    op.create_table(
        "export_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("dataset", sa.String(length=32), nullable=False),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column("filters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("artifact", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("sidecar", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_summary", sa.String(length=1000), nullable=True),
        sa.Column("requested_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_export_jobs")),
    )
    op.create_index(
        "ix_export_jobs_requested_by_requested_at",
        "export_jobs",
        ["requested_by", "requested_at", "id"],
    )
    op.create_index(
        "ix_export_jobs_requested_at_id", "export_jobs", ["requested_at", "id"]
    )
    op.create_index(op.f("ix_export_jobs_status"), "export_jobs", ["status"])

    op.create_table(
        "import_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column(
            "source_artifact", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("writes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_summary", sa.String(length=1000), nullable=True),
        sa.Column("requested_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_jobs")),
    )


def downgrade() -> None:
    """Drop everything ``upgrade`` created, in reverse order."""
    op.drop_table("import_jobs")
    op.drop_index(op.f("ix_export_jobs_status"), table_name="export_jobs")
    op.drop_index("ix_export_jobs_requested_at_id", table_name="export_jobs")
    op.drop_index("ix_export_jobs_requested_by_requested_at", table_name="export_jobs")
    op.drop_table("export_jobs")
