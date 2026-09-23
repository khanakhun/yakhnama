"""SQLAlchemy row models of the ``events`` bounded context.

Rows are persistence shapes only (ADR 0004): ``mappers.py`` translates them to and
from the frozen ``Event`` aggregate and ``EventRelation`` values, and nothing outside
this package sees them.

``events`` holds one row per event. The aggregate's collections (``source_ids``,
``affected_places``, ``report_links``, ``unlinked_reports``) are JSONB arrays holding
exactly the JSON dump of their value objects, validated back through the domain on
every read; ``attributes`` is the JSON dump of one ``HazardAttributesUnion`` member and
is validated back through ``DEFAULT_REGISTRY`` under the event's hazard code, so it
reloads as its concrete attribute class. ``geometry`` (any of Point, Polygon,
MultiPolygon) and ``centroid`` (a point) are WGS84 (EPSG:4326, ADR 0002) with
``spatial_index=False`` and explicit, named GiST indexes.

``period_earliest_at`` and ``period_latest_at`` are derived by the mapper from the
period (``EventPeriod.earliest_instant`` and ``latest_instant``) so that ordering and
the period-overlap filter run on indexed columns with exactly the domain's
precision-aware semantics; computing season or month bounds in SQL would duplicate
the kernel's flooring rules.

``event_report_links`` is a narrow projection of ``events.report_links`` maintained by
the repository on every write, so events can be found by report without scanning
JSONB. The aggregate's JSONB array stays the source of truth.

``event_relations`` holds one row per ``EventRelation``. Its primary key is derived
from the relation's ``key`` (``same_as`` pairs unordered), so the database itself
refuses a duplicate relation, including a ``same_as`` recorded in the other
direction.

Report, source, place and user ids carry no foreign key: they belong to other
modules, which this module knows only through their facades.

Patterns: Adapter (ORM row models behind the repository and query service adapters).
"""

from datetime import datetime
from typing import Final
from uuid import UUID

from geoalchemy2 import Geometry, WKBElement
from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from yakhnama.platform.db import Base

WGS84_SRID: Final = 4326
"""EPSG code of every stored geometry (ADR 0002)."""

EVENTS_TABLE: Final = "events"
EVENT_RELATIONS_TABLE: Final = "event_relations"
EVENT_REPORT_LINKS_TABLE: Final = "event_report_links"


class EventRow(Base):
    """Row model of the ``events`` table: one row per ``Event``.

    Implements: Adapter (ORM row model of ``SqlAlchemyEventRepository``).

    Attributes:
        id: Primary key, the event id (UUIDv7).
        hazard_code: The hazard type code.
        title: Short name.
        summary: Moderator's summary, if any.
        started_at: Start of the period, UTC, as recorded.
        started_at_precision: ``DatePrecision`` of ``started_at``.
        ended_at: End of the period, UTC, if known.
        ended_at_precision: ``DatePrecision`` of ``ended_at``; set exactly with it.
        period_earliest_at: Derived: ``EventPeriod.earliest_instant()``.
        period_latest_at: Derived: ``EventPeriod.latest_instant()``.
        geometry: The WGS84 footprint (point, polygon or multipolygon), if mapped.
        centroid: The representative WGS84 point, if known.
        attributes: Hazard-specific attributes as JSON, if any.
        source_ids: Cited source ids, a JSON array of strings in citation order.
        affected_places: ``AffectedPlace`` values as a JSON array.
        report_links: ``ReportLink`` values as a JSON array, in link order.
        unlinked_reports: ``ReportUnlink`` values as a JSON array; append-only.
        status: ``EventStatus`` value.
        status_reason: Why it was retracted or merged, if it was.
        merged_into: The surviving event, when merged.
        created_by: The moderator who created it.
        version: Optimistic-concurrency version, compared on every update.
        created_at: When it was created, UTC.
        updated_at: When it last changed, UTC.
    """

    __tablename__ = EVENTS_TABLE
    __table_args__ = (
        CheckConstraint(
            "(ended_at IS NULL) = (ended_at_precision IS NULL)",
            name="end_complete",
        ),
        CheckConstraint(
            "period_earliest_at <= period_latest_at", name="period_ordered"
        ),
        Index("ix_events_geometry_gist", "geometry", postgresql_using="gist"),
        Index("ix_events_centroid_gist", "centroid", postgresql_using="gist"),
        # Serves the newest-first keyset search (read backwards).
        Index("ix_events_period_earliest_at_id", "period_earliest_at", "id"),
        # Serves EventPlaceSpecification's JSONB containment test.
        Index(
            "ix_events_affected_places_gin",
            "affected_places",
            postgresql_using="gin",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    # Lengths follow the domain bounds: HazardCode (at most 64 characters),
    # TITLE_MAX_LENGTH, SUMMARY_MAX_LENGTH and CHANGE_REASON_MAX_LENGTH.
    hazard_code: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(String(2000))
    started_at: Mapped[datetime] = mapped_column(index=True)
    started_at_precision: Mapped[str] = mapped_column(String(16))
    ended_at: Mapped[datetime | None]
    ended_at_precision: Mapped[str | None] = mapped_column(String(16))
    period_earliest_at: Mapped[datetime]
    period_latest_at: Mapped[datetime]
    geometry: Mapped[WKBElement | None] = mapped_column(
        Geometry(geometry_type="GEOMETRY", srid=WGS84_SRID, spatial_index=False)
    )
    centroid: Mapped[WKBElement | None] = mapped_column(
        Geometry(geometry_type="POINT", srid=WGS84_SRID, spatial_index=False)
    )
    attributes: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    source_ids: Mapped[list[str]] = mapped_column(JSONB)
    affected_places: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    report_links: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    unlinked_reports: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), index=True)
    status_reason: Mapped[str | None] = mapped_column(String(1000))
    merged_into: Mapped[UUID | None] = mapped_column(
        ForeignKey("events.id", ondelete="RESTRICT")
    )
    created_by: Mapped[UUID]
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class EventReportLinkRow(Base):
    """Row model of ``event_report_links``: one row per current report link.

    A projection of ``events.report_links`` for lookups by report; rewritten with
    the event on every save.

    Implements: Adapter (ORM row model of ``SqlAlchemyEventRepository``).

    Attributes:
        event_id: The event.
        report_id: The linked report.
        role: ``primary``, ``supporting`` or ``contradicting``.
        linked_by: Who made the link.
        linked_at: When, UTC.
    """

    __tablename__ = EVENT_REPORT_LINKS_TABLE

    # The composite primary key is the (event_id, report_id) uniqueness rule: one
    # link per report and event, as the aggregate enforces.
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    report_id: Mapped[UUID] = mapped_column(primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(16))
    linked_by: Mapped[UUID]
    linked_at: Mapped[datetime]


class EventRelationRow(Base):
    """Row model of ``event_relations``: one row per ``EventRelation``.

    Implements: Adapter (ORM row model of ``SqlAlchemyEventRelationRepository``).

    Attributes:
        id: Primary key derived from the relation's key (see ``mappers``).
        from_event_id: The event the relation starts from.
        to_event_id: The event it points to.
        kind: ``RelationKind`` value.
        note: Optional explanation.
        related_by: Who recorded it.
        related_at: When, UTC.
    """

    __tablename__ = EVENT_RELATIONS_TABLE
    __table_args__ = (
        CheckConstraint("from_event_id <> to_event_id", name="distinct_ends"),
        # Named uq_event_relations_from_event_id by the convention; it also serves
        # traversal from the relation's start.
        UniqueConstraint("from_event_id", "to_event_id", "kind"),
        # Serves graph traversal towards the relation's start.
        Index("ix_event_relations_to_event_id", "to_event_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    from_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.id", ondelete="RESTRICT")
    )
    to_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.id", ondelete="RESTRICT")
    )
    kind: Mapped[str] = mapped_column(String(16))
    # RELATION_NOTE_MAX_LENGTH.
    note: Mapped[str | None] = mapped_column(String(500))
    related_by: Mapped[UUID]
    related_at: Mapped[datetime]
