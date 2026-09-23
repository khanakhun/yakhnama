"""SQLAlchemy row models of the ``geography`` bounded context.

Rows are persistence shapes only (ADR 0004): ``mappers.py`` translates them to and
from the frozen ``Place`` aggregate, and nothing outside this package sees them.
Geometry is WGS84 (EPSG:4326, ADR 0002) with ``spatial_index=False`` and an explicit,
named GiST index, so Alembic autogenerate sees the index under our naming convention
(``write-migration`` skill).

Search runs on ``place_names.text_folded``: the name after ``fold_search_text``
(marks removed, case-folded) and, for names written in Latin script, PostgreSQL's
``unaccent`` (see ``mappers.py``). A GIN trigram index (``gin_trgm_ops``) serves both
the ``%`` similarity operator and ``ILIKE '%...%'`` substring matches.

Patterns: Adapter (ORM row models behind the repository and query service adapters).
"""

from datetime import datetime
from typing import Final
from uuid import UUID

from geoalchemy2 import Geometry, WKBElement
from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from yakhnama.platform.db import Base

WGS84_SRID: Final = 4326
"""EPSG code of every stored geometry (ADR 0002)."""

PLACES_TABLE: Final = "places"
PLACE_NAMES_TABLE: Final = "place_names"


class PlaceRow(Base):
    """Row model of the ``places`` table: one row per ``Place`` aggregate.

    Implements: Adapter (ORM row model of ``SqlAlchemyPlaceRepository``).

    Attributes:
        id: Primary key, the aggregate id (UUIDv7).
        code: Stable machine code, unique, never reused.
        level: ``AdminLevel`` value.
        parent_id: The enclosing place; ``NULL`` only for a country.
        geometry: WGS84 footprint (Point, Polygon or MultiPolygon), if known.
        centroid: Representative WGS84 point, if known.
        status: ``active``, ``merged`` or ``retired``.
        status_reason: Why the place was merged or retired.
        merged_into_id: The place that replaced this one when merged.
        version: Optimistic-concurrency version, compared on every update.
        created_at: First recorded, UTC.
        updated_at: Last changed, UTC.
    """

    __tablename__ = PLACES_TABLE
    __table_args__ = (
        Index("ix_places_geometry_gist", "geometry", postgresql_using="gist"),
        Index("ix_places_centroid_gist", "centroid", postgresql_using="gist"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    level: Mapped[str] = mapped_column(String(32), index=True)
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("places.id", ondelete="RESTRICT"), index=True
    )
    geometry: Mapped[WKBElement | None] = mapped_column(
        Geometry(geometry_type="GEOMETRY", srid=WGS84_SRID, spatial_index=False)
    )
    centroid: Mapped[WKBElement | None] = mapped_column(
        Geometry(geometry_type="POINT", srid=WGS84_SRID, spatial_index=False)
    )
    status: Mapped[str] = mapped_column(String(16))
    status_reason: Mapped[str | None] = mapped_column(String(500))
    merged_into_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("places.id", ondelete="RESTRICT")
    )
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class PlaceNameRow(Base):
    """Row model of the ``place_names`` table: one row per ``PlaceName`` of a place.

    Names are value objects of the ``Place`` aggregate, so their rows are replaced as
    a whole whenever the place is saved. ``id`` is derived from the place id and the
    name's ``(text, language, script)`` key (``mappers.place_name_row_id``), so a name
    keeps its row id across saves without needing an id generator here.

    Implements: Adapter (ORM row model of ``SqlAlchemyPlaceRepository``).

    Attributes:
        id: Primary key, deterministic per place and name key.
        place_id: The place the name belongs to; rows go with the place.
        position: Zero-based insertion order of the name within the place.
        text: The name, NFC-normalised.
        text_folded: ``text`` in the form search compares (see the module docstring).
        language: BCP 47 language code.
        script: ISO 15924 script code, if recorded.
        kind: ``official``, ``alternative``, ``historical`` or ``transliteration``.
        is_preferred: Whether it is the display name for its language.
        source_id: Provenance record of the name, once linked (Phase 3).
    """

    __tablename__ = PLACE_NAMES_TABLE
    __table_args__ = (
        UniqueConstraint(
            "place_id",
            "text",
            "language",
            "script",
            name="uq_place_names_place_id_text_language_script",
            # A name without a script and the same text and language is still the
            # same name, so NULL scripts must collide as well (PostgreSQL 15+).
            postgresql_nulls_not_distinct=True,
        ),
        UniqueConstraint(
            "place_id", "position", name="uq_place_names_place_id_position"
        ),
        Index(
            "ix_place_names_text_folded_gin",
            "text_folded",
            postgresql_using="gin",
            postgresql_ops={"text_folded": "gin_trgm_ops"},
        ),
        Index(
            "uq_place_names_place_id_language_preferred",
            "place_id",
            "language",
            unique=True,
            postgresql_where=text("is_preferred"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    # No separate index: both unique constraints lead with place_id and serve the
    # per-place lookups.
    place_id: Mapped[UUID] = mapped_column(ForeignKey("places.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(SmallInteger)
    text: Mapped[str] = mapped_column(String(200))
    # Text, not String(200): NFKD and unaccent can lengthen a name (a ligature such
    # as U+FDFA decomposes into eighteen characters), and a derived column must never
    # reject a name its source column accepted.
    text_folded: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(16))
    script: Mapped[str | None] = mapped_column(String(8))
    kind: Mapped[str] = mapped_column(String(32))
    is_preferred: Mapped[bool] = mapped_column(Boolean)
    source_id: Mapped[UUID | None]
