"""fix(reports): hold revisions to their predecessors and index the moderation queues.

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-07T12:00:00+00:00

Schema migration with one read-only data check; no data is written here.

**Pre-check.** Before the new check is added, a ``DO`` block looks for reports whose
revision number and predecessor disagree (a revision 1 that supersedes another
report, or a later revision that supersedes none) and, if there are any, raises with
up to 50 of their ids so they can be repaired by hand. It runs in the migration's
transaction and in ``alembic --sql`` output alike. Migration ``0025`` cannot list
them itself (it is committed); on such data its own check
``lineage_root_is_first_revision`` fails instead.

**Check.** ``reports`` gains ``first_revision_supersedes_nothing``:
``(revision = 1) = (supersedes_id IS NULL)``, which ``Report`` already guarantees in
the domain (ADR 0022, amended).

**Indexes.**

- ``ix_reports_triage_gin``: GIN (``jsonb_path_ops``) on ``reports.triage`` for the
  moderators' ``triage_flag`` filter, a containment test
  ``triage @> '{"flags": [{"kind": ...}]}'``.
- ``ix_media_assets_completed_created_at_id``: ``(created_at, id)`` over completed
  uploads, which serves the moderators' photo queue in order when it is not
  filtered by moderation status (``ix_media_assets_moderation_queue`` leads with
  the status and cannot).

Downgrade drops the indexes and the check.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REVISION_CHECK = "(revision = 1) = (supersedes_id IS NULL)"

# Lists the reports the check would refuse, so a failure names what to repair.
_PRE_CHECK = """
DO $$
DECLARE
    offending text;
BEGIN
    SELECT string_agg(id::text, ', ' ORDER BY id) INTO offending
    FROM (
        SELECT id FROM reports
        WHERE (revision = 1) <> (supersedes_id IS NULL)
        ORDER BY id
        LIMIT 50
    ) AS listed;
    IF offending IS NOT NULL THEN
        RAISE EXCEPTION
            'migration 0026: reports whose revision and supersedes_id disagree '
            '(revision 1 must supersede nothing, a later revision must supersede '
            'its predecessor); repair them and run the migration again: %',
            offending;
    END IF;
END
$$
"""


def upgrade() -> None:
    """Check the data, then add the revision check and the two indexes."""
    op.execute(_PRE_CHECK)
    op.create_check_constraint(
        op.f("ck_reports_first_revision_supersedes_nothing"),
        "reports",
        _REVISION_CHECK,
    )
    op.create_index(
        "ix_reports_triage_gin",
        "reports",
        ["triage"],
        postgresql_using="gin",
        postgresql_ops={"triage": "jsonb_path_ops"},
    )
    op.create_index(
        "ix_media_assets_completed_created_at_id",
        "media_assets",
        ["created_at", "id"],
        postgresql_where=sa.text("upload_status = 'completed'"),
    )


def downgrade() -> None:
    """Drop the indexes and the revision check."""
    op.drop_index("ix_media_assets_completed_created_at_id", table_name="media_assets")
    op.drop_index("ix_reports_triage_gin", table_name="reports")
    op.drop_constraint(
        op.f("ck_reports_first_revision_supersedes_nothing"),
        "reports",
        type_="check",
    )
