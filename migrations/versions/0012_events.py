"""feat(events): create events, event_relations and event_report_links.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-23T00:00:00+00:00

Schema migration only; no data is written here.

Creates the tables exactly as ``yakhnama.modules.events.infrastructure.orm``
declares them. ``events`` holds one row per event: the period with its precision
plus the mapper-derived ``period_earliest_at`` / ``period_latest_at`` bounds (search
order and period overlap), a WGS84 ``geometry`` (any type) and ``centroid`` (point),
each with an explicit GiST index, the hazard ``attributes`` and the aggregate's
collections as JSONB (validated by the domain and the hazard registry on read), and
a GIN index on ``affected_places`` for place search. ``merged_into`` references
``events.id``. ``event_relations`` holds one row per relation with a key-derived
primary key, a unique ``(from_event_id, to_event_id, kind)`` and indexes for
traversal in both directions (the unique index leads with ``from_event_id``).
``event_report_links`` is a narrow projection of the current report links for
lookups by report, keyed by ``(event_id, report_id)``. Every foreign key to
``events`` is ``ON DELETE RESTRICT`` except the projection's ``CASCADE``; events are
never deleted anyway. Lengths and names are literals on purpose: a migration never
imports models or their constants.
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the three events tables with their keys, checks and indexes."""
    op.create_table(
        "events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("hazard_code", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.String(length=2000), nullable=True),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("started_at_precision", sa.String(length=16), nullable=False),
        sa.Column("ended_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("ended_at_precision", sa.String(length=16), nullable=True),
        sa.Column(
            "period_earliest_at", postgresql.TIMESTAMP(timezone=True), nullable=False
        ),
        sa.Column(
            "period_latest_at", postgresql.TIMESTAMP(timezone=True), nullable=False
        ),
        sa.Column(
            "geometry",
            geoalchemy2.types.Geometry(
                geometry_type="GEOMETRY", srid=4326, spatial_index=False
            ),
            nullable=True,
        ),
        sa.Column(
            "centroid",
            geoalchemy2.types.Geometry(
                geometry_type="POINT", srid=4326, spatial_index=False
            ),
            nullable=True,
        ),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "source_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "affected_places", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "report_links", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "unlinked_reports", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("status_reason", sa.String(length=1000), nullable=True),
        sa.Column("merged_into", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(ended_at IS NULL) = (ended_at_precision IS NULL)",
            name=op.f("ck_events_end_complete"),
        ),
        sa.CheckConstraint(
            "period_earliest_at <= period_latest_at",
            name=op.f("ck_events_period_ordered"),
        ),
        sa.ForeignKeyConstraint(
            ["merged_into"],
            ["events.id"],
            name=op.f("fk_events_merged_into_events"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
    )
    op.create_index(
        "ix_events_affected_places_gin",
        "events",
        ["affected_places"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_events_centroid_gist", "events", ["centroid"], postgresql_using="gist"
    )
    op.create_index(
        "ix_events_geometry_gist", "events", ["geometry"], postgresql_using="gist"
    )
    op.create_index(op.f("ix_events_hazard_code"), "events", ["hazard_code"])
    op.create_index(
        "ix_events_period_earliest_at_id", "events", ["period_earliest_at", "id"]
    )
    op.create_index(op.f("ix_events_started_at"), "events", ["started_at"])
    op.create_index(op.f("ix_events_status"), "events", ["status"])

    op.create_table(
        "event_relations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("from_event_id", sa.Uuid(), nullable=False),
        sa.Column("to_event_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("related_by", sa.Uuid(), nullable=False),
        sa.Column("related_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "from_event_id <> to_event_id",
            name=op.f("ck_event_relations_distinct_ends"),
        ),
        sa.ForeignKeyConstraint(
            ["from_event_id"],
            ["events.id"],
            name=op.f("fk_event_relations_from_event_id_events"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["to_event_id"],
            ["events.id"],
            name=op.f("fk_event_relations_to_event_id_events"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_relations")),
        sa.UniqueConstraint(
            "from_event_id",
            "to_event_id",
            "kind",
            name=op.f("uq_event_relations_from_event_id"),
        ),
    )
    op.create_index(
        "ix_event_relations_to_event_id", "event_relations", ["to_event_id"]
    )

    op.create_table(
        "event_report_links",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("linked_by", sa.Uuid(), nullable=False),
        sa.Column("linked_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name=op.f("fk_event_report_links_event_id_events"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "event_id", "report_id", name=op.f("pk_event_report_links")
        ),
    )
    op.create_index(
        op.f("ix_event_report_links_report_id"), "event_report_links", ["report_id"]
    )


def downgrade() -> None:
    """Drop everything ``upgrade`` created, in reverse order."""
    op.drop_index(
        op.f("ix_event_report_links_report_id"), table_name="event_report_links"
    )
    op.drop_table("event_report_links")
    op.drop_index("ix_event_relations_to_event_id", table_name="event_relations")
    op.drop_table("event_relations")
    op.drop_index(op.f("ix_events_status"), table_name="events")
    op.drop_index(op.f("ix_events_started_at"), table_name="events")
    op.drop_index("ix_events_period_earliest_at_id", table_name="events")
    op.drop_index(op.f("ix_events_hazard_code"), table_name="events")
    op.drop_index("ix_events_geometry_gist", table_name="events")
    op.drop_index("ix_events_centroid_gist", table_name="events")
    op.drop_index("ix_events_affected_places_gin", table_name="events")
    op.drop_table("events")
