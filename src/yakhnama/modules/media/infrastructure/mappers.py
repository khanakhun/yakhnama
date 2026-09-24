"""Translate between the ``MediaAsset`` aggregate and its row model.

Shapely and WKB stay on this side of the boundary: the EXIF location travels as the
kernel's ``Coordinates`` in the domain and as a GeoAlchemy2 ``WKBElement`` here.
Every read goes through ``model_validate``, so a row that no longer satisfies the
domain's invariants (a public key without approval, a completed upload without a
digest) fails loudly instead of producing an invalid aggregate.

Patterns: Anti-Corruption Layer (mapper).
"""

from geoalchemy2 import WKBElement
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point as ShapelyPoint

from yakhnama.modules.media.domain.entities import MediaAsset
from yakhnama.modules.media.domain.value_objects import ExifFacts
from yakhnama.modules.media.infrastructure.orm import WGS84_SRID, MediaAssetRow
from yakhnama.shared_kernel.value_objects import Coordinates


def location_to_element(location: Coordinates | None) -> WKBElement | None:
    """Convert an optional EXIF location to a WGS84 point ``WKBElement``.

    Args:
        location: The position, or ``None``.

    Returns:
        The extended WKB value for ``exif_location``, or ``None``.
    """
    if location is None:
        return None
    return from_shape(
        ShapelyPoint(location.longitude, location.latitude),
        srid=WGS84_SRID,
        extended=True,
    )


def element_to_location(element: WKBElement | None) -> Coordinates | None:
    """Convert a stored ``exif_location`` back to coordinates.

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
        message = f"exif_location column holds a {point.geom_type}, not a Point"
        raise TypeError(message)
    return Coordinates(longitude=point.x, latitude=point.y)


def exif_to_columns(
    exif: ExifFacts | None,
) -> tuple[dict[str, object] | None, WKBElement | None]:
    """Split EXIF facts into the ``exif`` JSONB value and the location point.

    Args:
        exif: The facts, or ``None`` when the original had no EXIF.

    Returns:
        ``(exif, exif_location)``, both ``None`` when ``exif`` is.
    """
    if exif is None:
        return None, None
    return (
        exif.model_dump(mode="json", include={"taken_at", "camera"}),
        location_to_element(exif.location),
    )


def columns_to_exif(
    exif: dict[str, object] | None, location: WKBElement | None
) -> ExifFacts | None:
    """Join the ``exif`` JSONB value and the location point back into EXIF facts.

    Args:
        exif: The stored JSONB object, or ``None``.
        location: The stored point, or ``None``.

    Returns:
        The validated facts, or ``None`` when the original had no EXIF.
    """
    if exif is None:
        return None
    return ExifFacts.model_validate({**exif, "location": element_to_location(location)})


def asset_to_values(asset: MediaAsset) -> dict[str, object]:
    """Return every column value of ``asset`` except the primary key.

    Args:
        asset: The aggregate.

    Returns:
        Column name to value, for inserts and versioned updates alike.
    """
    exif, exif_location = exif_to_columns(asset.exif)
    return {
        "owner_id": asset.owner_id,
        "report_id": asset.report_id,
        "source_id": asset.source_id,
        "original_key": asset.original_key,
        "public_key": asset.public_key,
        "sha256": asset.sha256,
        "mime_type": asset.mime_type.value,
        "byte_size": asset.byte_size,
        "exif": exif,
        "exif_location": exif_location,
        "upload_status": asset.upload_status.value,
        "scan_status": asset.scan_status.value,
        "moderation_status": asset.moderation_status.value,
        "sensitivity": asset.sensitivity.value,
        "moderation_reason": asset.moderation_reason,
        "version": asset.version,
        "created_at": asset.created_at,
        "updated_at": asset.updated_at,
    }


def asset_to_row(asset: MediaAsset) -> MediaAssetRow:
    """Build the ``media_assets`` row of ``asset``.

    Args:
        asset: The aggregate.

    Returns:
        A transient ``MediaAssetRow`` carrying the same values.
    """
    return MediaAssetRow(id=asset.id, **asset_to_values(asset))


def row_to_asset(row: MediaAssetRow) -> MediaAsset:
    """Rebuild an asset from its row.

    Args:
        row: A row loaded from ``media_assets``.

    Returns:
        The validated ``MediaAsset``.
    """
    return MediaAsset.model_validate(
        {
            "id": row.id,
            "owner_id": row.owner_id,
            "report_id": row.report_id,
            "source_id": row.source_id,
            "original_key": row.original_key,
            "public_key": row.public_key,
            "sha256": row.sha256,
            "mime_type": row.mime_type,
            "byte_size": row.byte_size,
            "exif": columns_to_exif(row.exif, row.exif_location),
            "upload_status": row.upload_status,
            "scan_status": row.scan_status,
            "moderation_status": row.moderation_status,
            "sensitivity": row.sensitivity,
            "moderation_reason": row.moderation_reason,
            "version": row.version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )
