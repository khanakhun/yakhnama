"""fix(reports): reserve guest reports, check what the domain guarantees.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-05T00:00:00+00:00

Schema migration only; no data is written here (``0022`` backfills, ``0023``
contracts).

``reports`` gains the checks the domain already guarantees: a guest report names no
organisation (``guest_without_organisation``), an assisted report's consent method
is ``verbal`` or ``written`` and its statement version has the domain's format. The
plain index on ``channel`` gives way to a partial index on ``(channel, created_at,
id)`` over the non-account channels, which serves the moderators' guest and assisted
queues newest first and leaves the bulk of account reports out.

``guest_submissions`` gains ``source_id`` (the platform source a guest report will
cite, reserved before the source exists, ADR 0020) and indexes on ``submitted_at``
(the hourly reports cap) and ``expires_at`` (the retention purge). The check that set
report, reference, fingerprint and submission time together gives way to
``filed_after_reserved`` (report id and reference together, and only after a
reservation) and to checks on the media list, version, timestamps and digest format;
``reserved_fields_together`` follows in ``0023``, once ``0022`` has backfilled
``source_id``.

Downgrade restores the ``0020`` schema. It fails while a guest submission is
reserved but not yet filed (a few seconds per report in flight): the earlier check
cannot describe that state, so wait for it to be filed or purged.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the checks, swap the channel index, expand ``guest_submissions``."""
    op.create_check_constraint(
        op.f("ck_reports_guest_without_organisation"),
        "reports",
        "channel <> 'guest' OR organization_id IS NULL",
    )
    op.create_check_constraint(
        op.f("ck_reports_assisted_consent_method_known"),
        "reports",
        "assisted_consent_method IS NULL "
        "OR assisted_consent_method IN ('verbal', 'written')",
    )
    op.create_check_constraint(
        op.f("ck_reports_assisted_statement_version_format"),
        "reports",
        "assisted_consent_statement_version IS NULL "
        "OR assisted_consent_statement_version ~ '^[a-z0-9][a-z0-9._-]{0,31}$'",
    )
    op.drop_index(op.f("ix_reports_channel"), table_name="reports")
    op.create_index(
        "ix_reports_channel_created_at_id_not_account",
        "reports",
        ["channel", "created_at", "id"],
        postgresql_where=sa.text("channel <> 'account'"),
    )

    op.add_column("guest_submissions", sa.Column("source_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_guest_submissions_submitted_at"),
        "guest_submissions",
        ["submitted_at"],
    )
    op.create_index(
        op.f("ix_guest_submissions_expires_at"), "guest_submissions", ["expires_at"]
    )
    op.drop_constraint(
        op.f("ck_guest_submissions_closed_fields_together"),
        "guest_submissions",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_guest_submissions_filed_after_reserved"),
        "guest_submissions",
        "(report_id IS NULL) = (reference IS NULL) "
        "AND (report_id IS NULL OR content_fingerprint IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_guest_submissions_media_ids_at_most_three"),
        "guest_submissions",
        "jsonb_typeof(media_ids) = 'array' AND jsonb_array_length(media_ids) <= 3",
    )
    op.create_check_constraint(
        op.f("ck_guest_submissions_version_positive"),
        "guest_submissions",
        "version >= 1",
    )
    op.create_check_constraint(
        op.f("ck_guest_submissions_times_ordered"),
        "guest_submissions",
        "created_at < expires_at AND created_at <= updated_at",
    )
    op.create_check_constraint(
        op.f("ck_guest_submissions_capability_digest_format"),
        "guest_submissions",
        "capability_digest ~ '^[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    """Restore the ``0020`` schema, in reverse order."""
    for name in (
        "ck_guest_submissions_capability_digest_format",
        "ck_guest_submissions_times_ordered",
        "ck_guest_submissions_version_positive",
        "ck_guest_submissions_media_ids_at_most_three",
        "ck_guest_submissions_filed_after_reserved",
    ):
        op.drop_constraint(op.f(name), "guest_submissions", type_="check")
    op.create_check_constraint(
        op.f("ck_guest_submissions_closed_fields_together"),
        "guest_submissions",
        "(report_id IS NULL) = (reference IS NULL) "
        "AND (report_id IS NULL) = (content_fingerprint IS NULL) "
        "AND (report_id IS NULL) = (submitted_at IS NULL)",
    )
    op.drop_index(
        op.f("ix_guest_submissions_expires_at"), table_name="guest_submissions"
    )
    op.drop_index(
        op.f("ix_guest_submissions_submitted_at"), table_name="guest_submissions"
    )
    op.drop_column("guest_submissions", "source_id")

    op.drop_index("ix_reports_channel_created_at_id_not_account", table_name="reports")
    op.create_index(op.f("ix_reports_channel"), "reports", ["channel"])
    for name in (
        "ck_reports_assisted_statement_version_format",
        "ck_reports_assisted_consent_method_known",
        "ck_reports_guest_without_organisation",
    ):
        op.drop_constraint(op.f(name), "reports", type_="check")
