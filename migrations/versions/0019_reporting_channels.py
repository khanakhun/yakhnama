"""feat(reports): reporting channels, assisted consent and guest submissions.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-05T00:00:00+00:00

Schema migration only; no data is written here.

Adds to ``reports`` the ``channel`` a report came through (``account``, ``guest`` or
``assisted``; ADR 0019) with the server default ``account``, which is what every
existing report is, and the consent record of an assisted report
(``assisted_consent_method``, ``assisted_consent_statement_version`` and the private
``assisted_note``), with checks that keep the channel and the consent record
consistent and an index on ``channel`` for the moderators' guest queue.

Creates ``guest_submissions`` (one guest's access record: the capability's SHA-256
digest, its expiry, the granted photo ids and, once submitted, the report, its unique
receipt reference and the content fingerprint) and ``guest_challenges`` (redeemed
proof-of-work challenges, kept until they expire; ADR 0020).

Downgrade drops the two tables, then the new ``reports`` columns, checks and index.
Guest and assisted reports keep their rows but lose the channel and the consent
record, which the earlier schema cannot hold.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the channel columns to ``reports``; create the two guest tables."""
    op.add_column(
        "reports",
        sa.Column(
            "channel",
            sa.String(length=16),
            server_default=sa.text("'account'"),
            nullable=False,
        ),
    )
    op.add_column(
        "reports",
        sa.Column("assisted_consent_method", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "reports",
        sa.Column(
            "assisted_consent_statement_version", sa.String(length=32), nullable=True
        ),
    )
    op.add_column(
        "reports", sa.Column("assisted_note", sa.String(length=500), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_reports_channel_known"),
        "reports",
        "channel IN ('account', 'guest', 'assisted')",
    )
    op.create_check_constraint(
        op.f("ck_reports_assisted_matches_channel"),
        "reports",
        "(channel = 'assisted') = (assisted_consent_method IS NOT NULL) "
        "AND (assisted_consent_method IS NULL) = "
        "(assisted_consent_statement_version IS NULL) "
        "AND (assisted_consent_method IS NOT NULL OR assisted_note IS NULL)",
    )
    op.create_index(op.f("ix_reports_channel"), "reports", ["channel"])

    op.create_table(
        "guest_submissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("capability_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("media_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=True),
        sa.Column("reference", sa.String(length=12), nullable=True),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("submitted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(report_id IS NULL) = (reference IS NULL) "
            "AND (report_id IS NULL) = (content_fingerprint IS NULL) "
            "AND (report_id IS NULL) = (submitted_at IS NULL)",
            name=op.f("ck_guest_submissions_closed_fields_together"),
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            name=op.f("fk_guest_submissions_report_id_reports"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_guest_submissions")),
        sa.UniqueConstraint("reference", name=op.f("uq_guest_submissions_reference")),
        sa.UniqueConstraint("report_id", name=op.f("uq_guest_submissions_report_id")),
    )
    op.create_index(
        op.f("ix_guest_submissions_created_at"), "guest_submissions", ["created_at"]
    )

    op.create_table(
        "guest_challenges",
        sa.Column("salt", sa.String(length=64), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("salt", name=op.f("pk_guest_challenges")),
    )
    op.create_index(
        op.f("ix_guest_challenges_expires_at"), "guest_challenges", ["expires_at"]
    )


def downgrade() -> None:
    """Drop the guest tables, then the channel columns, checks and index."""
    op.drop_index(op.f("ix_guest_challenges_expires_at"), table_name="guest_challenges")
    op.drop_table("guest_challenges")
    op.drop_index(
        op.f("ix_guest_submissions_created_at"), table_name="guest_submissions"
    )
    op.drop_table("guest_submissions")
    op.drop_index(op.f("ix_reports_channel"), table_name="reports")
    op.drop_constraint(
        op.f("ck_reports_assisted_matches_channel"), "reports", type_="check"
    )
    op.drop_constraint(op.f("ck_reports_channel_known"), "reports", type_="check")
    op.drop_column("reports", "assisted_note")
    op.drop_column("reports", "assisted_consent_statement_version")
    op.drop_column("reports", "assisted_consent_method")
    op.drop_column("reports", "channel")
