"""feat(reports): review marks on report lineages, and the moderation queues' indexes.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-07T00:00:00+00:00

Schema migration with one data step: the backfill of ``reports.lineage_id``.

``reports`` gains ``lineage_id``, the id of the report's revision 1 (ADR 0022). Every
existing row is backfilled by walking the revision chain from each revision 1
(``supersedes_id IS NULL``) with a recursive query; then the column becomes
``NOT NULL``, references ``reports.id`` and gets an index, and the check
``lineage_root_is_first_revision`` holds revision 1 to be its own lineage and every
later revision to belong to another. ``media_ids`` gets a GIN index
(``jsonb_path_ops``) for the media moderation queue's "which report lists this
asset" containment test.

``report_reviews`` (one row per marked lineage, its current mark) and
``report_review_marks`` (every mark, insert-only) are created. Neither changes a
report row: marks never touch a report's status or its reporter's rights.

``media_assets`` gets ``ix_media_assets_moderation_queue`` on
``(moderation_status, created_at, id)`` over completed uploads, which serves the
moderators' photo queue oldest first.

Downgrade drops the two tables, the indexes, the check and the column. It loses
every review mark, which the ``0024`` schema cannot hold.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATES_CHECK = "state IN ('new', 'reviewed', 'archived')"

# Walks every chain from its revision 1; a chain is short (one row per correction).
_BACKFILL_LINEAGE = sa.text(
    "WITH RECURSIVE chain (id, lineage_id) AS ("
    " SELECT id, id FROM reports WHERE supersedes_id IS NULL"
    " UNION ALL"
    " SELECT reports.id, chain.lineage_id FROM reports"
    " JOIN chain ON reports.supersedes_id = chain.id"
    ") "
    "UPDATE reports SET lineage_id = chain.lineage_id "
    "FROM chain WHERE reports.id = chain.id"
)


def upgrade() -> None:
    """Add and backfill the lineage, create the review tables and the indexes."""
    op.add_column("reports", sa.Column("lineage_id", sa.Uuid(), nullable=True))
    op.execute(_BACKFILL_LINEAGE)
    op.alter_column("reports", "lineage_id", nullable=False)
    op.create_foreign_key(
        op.f("fk_reports_lineage_id_reports"),
        "reports",
        "reports",
        ["lineage_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(op.f("ix_reports_lineage_id"), "reports", ["lineage_id"])
    op.create_check_constraint(
        op.f("ck_reports_lineage_root_is_first_revision"),
        "reports",
        "(revision = 1) = (lineage_id = id)",
    )
    op.create_index(
        "ix_reports_media_ids_gin",
        "reports",
        ["media_ids"],
        postgresql_using="gin",
        postgresql_ops={"media_ids": "jsonb_path_ops"},
    )
    op.create_table(
        "report_reviews",
        sa.Column("lineage_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("last_mark_id", sa.Uuid(), nullable=False),
        sa.Column("reviewed_report_id", sa.Uuid(), nullable=False),
        sa.Column("reviewed_revision", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(_STATES_CHECK, name=op.f("ck_report_reviews_state_known")),
        sa.CheckConstraint(
            "version >= 1", name=op.f("ck_report_reviews_version_positive")
        ),
        sa.CheckConstraint(
            "reviewed_revision >= 1", name=op.f("ck_report_reviews_revision_positive")
        ),
        sa.CheckConstraint(
            "created_at <= updated_at", name=op.f("ck_report_reviews_times_ordered")
        ),
        sa.ForeignKeyConstraint(
            ["lineage_id"],
            ["reports.id"],
            name=op.f("fk_report_reviews_lineage_id_reports"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_report_id"],
            ["reports.id"],
            name=op.f("fk_report_reviews_reviewed_report_id_reports"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("lineage_id", name=op.f("pk_report_reviews")),
    )
    op.create_index(op.f("ix_report_reviews_state"), "report_reviews", ["state"])
    op.create_table(
        "report_review_marks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lineage_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("marked_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            _STATES_CHECK, name=op.f("ck_report_review_marks_state_known")
        ),
        sa.CheckConstraint(
            "revision >= 1", name=op.f("ck_report_review_marks_revision_positive")
        ),
        sa.ForeignKeyConstraint(
            ["lineage_id"],
            ["report_reviews.lineage_id"],
            name=op.f("fk_report_review_marks_lineage_id_report_reviews"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            name=op.f("fk_report_review_marks_report_id_reports"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_review_marks")),
    )
    op.create_index(
        "ix_report_review_marks_lineage_id_marked_at_id",
        "report_review_marks",
        ["lineage_id", "marked_at", "id"],
    )
    op.create_index(
        "ix_media_assets_moderation_queue",
        "media_assets",
        ["moderation_status", "created_at", "id"],
        postgresql_where=sa.text("upload_status = 'completed'"),
    )


def downgrade() -> None:
    """Drop the review tables, the indexes, the check and the lineage."""
    op.drop_index("ix_media_assets_moderation_queue", table_name="media_assets")
    op.drop_index(
        "ix_report_review_marks_lineage_id_marked_at_id",
        table_name="report_review_marks",
    )
    op.drop_table("report_review_marks")
    op.drop_index(op.f("ix_report_reviews_state"), table_name="report_reviews")
    op.drop_table("report_reviews")
    op.drop_index("ix_reports_media_ids_gin", table_name="reports")
    op.drop_constraint(
        op.f("ck_reports_lineage_root_is_first_revision"), "reports", type_="check"
    )
    op.drop_index(op.f("ix_reports_lineage_id"), table_name="reports")
    op.drop_constraint(
        op.f("fk_reports_lineage_id_reports"), "reports", type_="foreignkey"
    )
    op.drop_column("reports", "lineage_id")
