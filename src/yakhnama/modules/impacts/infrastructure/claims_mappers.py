"""Translate between claims, assets, damage records and their row models.

Every read goes through ``model_validate``: a claim value is validated back into its
``ClaimValue`` variant by the ``kind`` discriminator, so a row that no longer
satisfies the domain fails loudly instead of producing an invalid aggregate. The
asset location travels as ``Coordinates`` in the domain and as a GeoAlchemy2
``WKBElement`` here; WKB keeps every coordinate bit for bit.

Patterns: Anti-Corruption Layer (mapper).
"""

from geoalchemy2 import WKBElement
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point as ShapelyPoint

from yakhnama.modules.impacts.domain.assets import InfrastructureAsset
from yakhnama.modules.impacts.domain.claims import ImpactClaim
from yakhnama.modules.impacts.domain.damage import DamageRecord
from yakhnama.modules.impacts.infrastructure.claims_orm import (
    WGS84_SRID,
    DamageRecordRow,
    ImpactClaimRow,
    InfrastructureAssetRow,
)
from yakhnama.shared_kernel.value_objects import Coordinates


def location_to_element(location: Coordinates | None) -> WKBElement | None:
    """Convert an asset location to a WGS84 point ``WKBElement``.

    Args:
        location: The point, or ``None``.

    Returns:
        The extended WKB value, or ``None``.
    """
    if location is None:
        return None
    return from_shape(
        ShapelyPoint(location.longitude, location.latitude),
        srid=WGS84_SRID,
        extended=True,
    )


def element_to_location(element: WKBElement | None) -> Coordinates | None:
    """Convert a stored point back to coordinates.

    Args:
        element: The column value, or ``None``.

    Returns:
        The validated ``Coordinates``, or ``None``.

    Raises:
        TypeError: If the column holds something other than a point, which the
            ``POINT`` column type rules out.
    """
    if element is None:
        return None
    point = to_shape(element)
    if not isinstance(point, ShapelyPoint):
        message = f"location column holds a {point.geom_type}, not a Point"
        raise TypeError(message)
    return Coordinates(longitude=point.x, latitude=point.y)


def claim_to_values(claim: ImpactClaim) -> dict[str, object]:
    """Return every ``impact_claims`` column value except the primary key.

    Args:
        claim: The aggregate.

    Returns:
        Column name to value, for inserts and versioned updates alike.
    """
    return {
        "event_id": claim.event_id,
        "metric_code": claim.metric.code,
        "value": claim.value.model_dump(mode="json"),
        "confidence": claim.confidence.value,
        "source_id": claim.source_id,
        "source_type": claim.source_type,
        "claimed_at": claim.claimed_at.value,
        "claimed_at_precision": claim.claimed_at.precision.value,
        "recorded_by": claim.recorded_by,
        "scope_place_code": claim.scope.place_code,
        "scope_asset_id": claim.scope.asset_id,
        "note": claim.note,
        "status": claim.status.value,
        "retraction_reason": claim.retraction_reason,
        "retracted_by": claim.retracted_by,
        "supersedes_id": claim.supersedes_id,
        "version": claim.version,
        "created_at": claim.created_at,
        "updated_at": claim.updated_at,
    }


def claim_to_row(claim: ImpactClaim) -> ImpactClaimRow:
    """Build the ``impact_claims`` row of ``claim``.

    Args:
        claim: The aggregate.

    Returns:
        A transient row carrying the same values.
    """
    return ImpactClaimRow(id=claim.id, **claim_to_values(claim))


def row_to_claim(row: ImpactClaimRow) -> ImpactClaim:
    """Rebuild a claim from its row.

    Args:
        row: A fully loaded row of ``impact_claims``.

    Returns:
        The validated ``ImpactClaim``.
    """
    return ImpactClaim.model_validate(
        {
            "id": row.id,
            "event_id": row.event_id,
            "metric": {"code": row.metric_code},
            "value": row.value,
            "confidence": row.confidence,
            "source_id": row.source_id,
            "source_type": row.source_type,
            "claimed_at": {
                "value": row.claimed_at,
                "precision": row.claimed_at_precision,
            },
            "recorded_by": row.recorded_by,
            "scope": {
                "place_code": row.scope_place_code,
                "asset_id": row.scope_asset_id,
            },
            "note": row.note,
            "status": row.status,
            "retraction_reason": row.retraction_reason,
            "retracted_by": row.retracted_by,
            "supersedes_id": row.supersedes_id,
            "version": row.version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


def asset_to_row(asset: InfrastructureAsset) -> InfrastructureAssetRow:
    """Build the ``infrastructure_assets`` row of ``asset``.

    Args:
        asset: The aggregate.

    Returns:
        A transient row carrying the same values.
    """
    return InfrastructureAssetRow(
        id=asset.id,
        kind=asset.kind.value,
        name=asset.name,
        osm_id=asset.osm_id,
        location=location_to_element(asset.location),
        place_code=asset.place_code,
        source_id=asset.source_id,
        version=asset.version,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
    )


def row_to_asset(row: InfrastructureAssetRow) -> InfrastructureAsset:
    """Rebuild an asset from its row.

    Args:
        row: A fully loaded row of ``infrastructure_assets``.

    Returns:
        The validated ``InfrastructureAsset``.
    """
    return InfrastructureAsset.model_validate(
        {
            "id": row.id,
            "kind": row.kind,
            "name": row.name,
            "osm_id": row.osm_id,
            "location": element_to_location(row.location),
            "place_code": row.place_code,
            "source_id": row.source_id,
            "version": row.version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


def damage_to_values(record: DamageRecord) -> dict[str, object]:
    """Return every ``damage_records`` column value except the primary key.

    Args:
        record: The aggregate.

    Returns:
        Column name to value, for inserts and versioned updates alike.
    """
    return {
        "event_id": record.event_id,
        "asset_id": record.asset_id,
        "level": record.level.value,
        "confidence": record.confidence.value,
        "source_id": record.source_id,
        "recorded_at": record.recorded_at.value,
        "recorded_at_precision": record.recorded_at.precision.value,
        "recorded_by": record.recorded_by,
        "note": record.note,
        "status": record.status.value,
        "retraction_reason": record.retraction_reason,
        "retracted_by": record.retracted_by,
        "version": record.version,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def damage_to_row(record: DamageRecord) -> DamageRecordRow:
    """Build the ``damage_records`` row of ``record``.

    Args:
        record: The aggregate.

    Returns:
        A transient row carrying the same values.
    """
    return DamageRecordRow(id=record.id, **damage_to_values(record))


def row_to_damage(row: DamageRecordRow) -> DamageRecord:
    """Rebuild a damage record from its row.

    Args:
        row: A fully loaded row of ``damage_records``.

    Returns:
        The validated ``DamageRecord``.
    """
    return DamageRecord.model_validate(
        {
            "id": row.id,
            "event_id": row.event_id,
            "asset_id": row.asset_id,
            "level": row.level,
            "confidence": row.confidence,
            "source_id": row.source_id,
            "recorded_at": {
                "value": row.recorded_at,
                "precision": row.recorded_at_precision,
            },
            "recorded_by": row.recorded_by,
            "note": row.note,
            "status": row.status,
            "retraction_reason": row.retraction_reason,
            "retracted_by": row.retracted_by,
            "version": row.version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )
