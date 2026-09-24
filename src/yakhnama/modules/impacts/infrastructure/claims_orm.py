"""SQLAlchemy row models of impact claims, infrastructure assets and damage records.

Rows are persistence shapes only (ADR 0004): ``claims_mappers.py`` translates them to
and from the frozen aggregates. Impact values are stored **long and narrow**: one row
per claim, one metric and one value (ADR 0002). ``value`` is a JSONB object holding
exactly ``model_dump(mode="json")`` of one ``ClaimValue`` variant, discriminated by
its ``kind`` (``count``, ``measurement`` or ``monetary``; monetary amounts are JSON
strings so the ``Decimal`` stays exact), and is validated back through the domain on
every read.

Claims are append-only: the only update is one retraction, and a correction is a
new row whose ``supersedes_id`` names the retracted one. ``supersedes_id`` is unique,
so at most one correction can ever replace a claim even if two moderators race.
``metric_code`` references ``impact_metrics.code`` (codes are never reused or
deleted); ``scope_asset_id`` and ``damage_records.asset_id`` reference
``infrastructure_assets``. Event, source, place and user ids carry no foreign key:
they belong to other modules. Every foreign key is ``ON DELETE RESTRICT``, and
nothing here is ever deleted.

An asset's ``location`` is a WGS84 point (EPSG:4326) with an explicit GiST index;
``osm_id`` is unique, so one OpenStreetMap element is registered at most once.

Patterns: Adapter (ORM row models behind the repository and query service adapters).
"""

from datetime import datetime
from typing import Final
from uuid import UUID

from geoalchemy2 import Geometry, WKBElement
from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from yakhnama.platform.db import Base

WGS84_SRID: Final = 4326
"""EPSG code of every stored geometry (ADR 0002)."""

INFRASTRUCTURE_ASSETS_TABLE: Final = "infrastructure_assets"
IMPACT_CLAIMS_TABLE: Final = "impact_claims"
DAMAGE_RECORDS_TABLE: Final = "damage_records"

# Lengths follow the domain bounds: MetricCode and PlaceCodeRef (at most 64
# characters), ASSET_NAME_MAX_LENGTH, REASON_MAX_LENGTH and NOTE_MAX_LENGTH, and the
# longest OsmId ("relation/" plus 19 digits).


class InfrastructureAssetRow(Base):
    """Row model of ``infrastructure_assets``: one row per asset.

    Implements: Adapter (ORM row model of ``SqlAlchemyInfrastructureAssetRepository``).

    Attributes:
        id: Primary key, the asset id (UUIDv7).
        kind: ``AssetKind`` value.
        name: Display name.
        osm_id: OpenStreetMap element, unique, if known.
        location: Representative WGS84 point, if known.
        place_code: The place it lies in, if known.
        source_id: The source describing it.
        version: Optimistic-concurrency version.
        created_at: When it was registered, UTC.
        updated_at: When it last changed, UTC.
    """

    __tablename__ = INFRASTRUCTURE_ASSETS_TABLE
    __table_args__ = (
        Index(
            "ix_infrastructure_assets_location_gist",
            "location",
            postgresql_using="gist",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    osm_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    location: Mapped[WKBElement | None] = mapped_column(
        Geometry(geometry_type="POINT", srid=WGS84_SRID, spatial_index=False)
    )
    place_code: Mapped[str | None] = mapped_column(String(64))
    source_id: Mapped[UUID]
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class ImpactClaimRow(Base):
    """Row model of ``impact_claims``: one row per claim, one metric and value.

    Implements: Adapter (ORM row model of ``SqlAlchemyImpactClaimRepository``).

    Attributes:
        id: Primary key, the claim id (UUIDv7).
        event_id: The hazard event the claim is about.
        metric_code: The metric; references ``impact_metrics.code``.
        value: The ``ClaimValue`` as a JSON object, discriminated by ``kind``.
        confidence: ``Confidence`` value.
        source_id: The provenance source of the figure.
        source_type: That source's type, copied for ranking.
        claimed_at: When the source made the claim, UTC.
        claimed_at_precision: ``DatePrecision`` of ``claimed_at``.
        recorded_by: The account that entered the claim.
        scope_place_code: The place the figure covers, if narrower than the event.
        scope_asset_id: The asset the figure covers, if any.
        note: A moderator's note, if any.
        status: ``active`` or ``retracted``.
        retraction_reason: Why it was retracted, if it was.
        retracted_by: Who retracted it, if anyone.
        supersedes_id: The claim this one corrects, unique, if any.
        version: Optimistic-concurrency version.
        created_at: When it was recorded, UTC.
        updated_at: When it last changed, UTC.
    """

    __tablename__ = IMPACT_CLAIMS_TABLE
    __table_args__ = (
        Index("ix_impact_claims_event_id_metric_code", "event_id", "metric_code"),
        # Serves the per-event listing in recording order.
        Index(
            "ix_impact_claims_event_id_created_at_id", "event_id", "created_at", "id"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    event_id: Mapped[UUID]
    metric_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("impact_metrics.code", ondelete="RESTRICT")
    )
    value: Mapped[dict[str, object]] = mapped_column(JSONB)
    confidence: Mapped[str] = mapped_column(String(16))
    source_id: Mapped[UUID] = mapped_column(index=True)
    source_type: Mapped[str] = mapped_column(String(16))
    claimed_at: Mapped[datetime]
    claimed_at_precision: Mapped[str] = mapped_column(String(16))
    recorded_by: Mapped[UUID]
    scope_place_code: Mapped[str | None] = mapped_column(String(64))
    scope_asset_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("infrastructure_assets.id", ondelete="RESTRICT")
    )
    note: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(16))
    retraction_reason: Mapped[str | None] = mapped_column(String(1000))
    retracted_by: Mapped[UUID | None]
    supersedes_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("impact_claims.id", ondelete="RESTRICT"), unique=True
    )
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class DamageRecordRow(Base):
    """Row model of ``damage_records``: damage to one asset in one event.

    Implements: Adapter (ORM row model of ``SqlAlchemyDamageRecordRepository``).

    Attributes:
        id: Primary key, the record id (UUIDv7).
        event_id: The hazard event that caused the damage.
        asset_id: The damaged asset; references ``infrastructure_assets``.
        level: ``DamageLevel`` value.
        confidence: ``Confidence`` value.
        source_id: The provenance source.
        recorded_at: When the source recorded the damage, UTC.
        recorded_at_precision: ``DatePrecision`` of ``recorded_at``.
        recorded_by: The account that entered the record.
        note: A moderator's note, if any.
        status: ``active`` or ``retracted``.
        retraction_reason: Why it was retracted, if it was.
        retracted_by: Who retracted it, if anyone.
        version: Optimistic-concurrency version.
        created_at: When it was entered, UTC.
        updated_at: When it last changed, UTC.
    """

    __tablename__ = DAMAGE_RECORDS_TABLE

    id: Mapped[UUID] = mapped_column(primary_key=True)
    event_id: Mapped[UUID] = mapped_column(index=True)
    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("infrastructure_assets.id", ondelete="RESTRICT"), index=True
    )
    level: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[str] = mapped_column(String(16))
    source_id: Mapped[UUID]
    recorded_at: Mapped[datetime]
    recorded_at_precision: Mapped[str] = mapped_column(String(16))
    recorded_by: Mapped[UUID]
    note: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(16))
    retraction_reason: Mapped[str | None] = mapped_column(String(1000))
    retracted_by: Mapped[UUID | None]
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
