"""Translate between the ``Place`` aggregate and its row models.

Shapely and WKB stay on this side of the boundary: geometry travels as the domain's
GeoJSON models (``PlaceGeometry``, ``Coordinates``) and is converted to and from
GeoAlchemy2 ``WKBElement`` values here. WKB keeps every coordinate bit-for-bit, so a
stored geometry reads back equal to the one saved (``ST_AsGeoJSON`` would round to a
fixed number of decimals).

The search form of a name, ``place_names.text_folded``, is ``fold_search_text(text)``
(``application/specifications.py``). For a name written in Latin script it is passed
through PostgreSQL's ``unaccent`` as well, so that letters Unicode decomposition
leaves alone (``ł``, ``ø``, ``đ``) fold like the rest; the query service applies the
same rule to the search text, so both sides of a comparison are folded alike.

Patterns: Anti-Corruption Layer (mapper).
"""

import unicodedata
import uuid
from collections.abc import Iterable, Sequence
from typing import Final

from geoalchemy2 import WKBElement
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point as ShapelyPoint
from shapely.geometry import mapping, shape
from sqlalchemy import ColumnElement, func, literal

from yakhnama.modules.geography.application.specifications import fold_search_text
from yakhnama.modules.geography.domain.entities import Place
from yakhnama.modules.geography.domain.value_objects import (
    AdminLevel,
    PlaceGeometry,
    PlaceName,
    ScriptCode,
)
from yakhnama.modules.geography.infrastructure.orm import (
    WGS84_SRID,
    PlaceNameRow,
    PlaceRow,
)
from yakhnama.shared_kernel.value_objects import Coordinates

PLACE_NAME_ID_NAMESPACE: Final = uuid.UUID("0192a3b4-5c6d-7e8f-9a0b-1c2d3e4f5a6b")
"""Namespace of the UUIDv5 row ids of ``place_names`` (arbitrary, fixed forever)."""

_LATIN_PREFIX: Final = "LATIN "


def is_latin_text(text: str) -> bool:
    """Tell whether every letter of ``text`` is a Latin-script letter.

    Text without letters (digits, punctuation) counts as Latin, so a search for such
    text is folded the same way as the Latin names it may occur in.

    Args:
        text: Any text.

    Returns:
        ``True`` if no letter of ``text`` belongs to another script.
    """
    return all(
        unicodedata.name(char, "").startswith(_LATIN_PREFIX)
        for char in text
        if char.isalpha()
    )


def search_form(text: str) -> ColumnElement[str]:
    """Return the SQL expression of the stored search form of ``text``.

    Args:
        text: A name or a search text.

    Returns:
        ``unaccent(fold_search_text(text))`` for Latin text, otherwise the folded
        text as a bound literal.
    """
    folded = fold_search_text(text)
    if is_latin_text(folded):
        return func.unaccent(folded)
    return literal(folded)


def place_name_row_id(place_id: uuid.UUID, name: PlaceName) -> uuid.UUID:
    """Return the deterministic row id of ``name`` within the place ``place_id``.

    Args:
        place_id: The owning place.
        name: The name.

    Returns:
        A UUIDv5 over the place id and the name's ``(text, language, script)`` key.
    """
    script = "" if name.script is None else name.script.value
    return uuid.uuid5(
        PLACE_NAME_ID_NAMESPACE,
        f"{place_id}\x1f{name.text}\x1f{name.language}\x1f{script}",
    )


def geometry_to_element(geometry: PlaceGeometry | None) -> WKBElement | None:
    """Convert a domain geometry to a WGS84 ``WKBElement``.

    Args:
        geometry: The place footprint, or ``None``.

    Returns:
        The extended WKB value for the ``geometry`` column, or ``None``.
    """
    if geometry is None:
        return None
    return from_shape(
        shape(geometry.geojson.model_dump(mode="json")),
        srid=WGS84_SRID,
        extended=True,
    )


def element_to_geometry(element: WKBElement | None) -> PlaceGeometry | None:
    """Convert a stored ``geometry`` value back to the domain geometry.

    Args:
        element: The column value, or ``None``.

    Returns:
        The validated ``PlaceGeometry``, or ``None``.
    """
    if element is None:
        return None
    return PlaceGeometry.model_validate({"geojson": mapping(to_shape(element))})


def centroid_to_element(centroid: Coordinates | None) -> WKBElement | None:
    """Convert a centroid to a WGS84 point ``WKBElement``.

    Args:
        centroid: The representative point, or ``None``.

    Returns:
        The extended WKB value for the ``centroid`` column, or ``None``.
    """
    if centroid is None:
        return None
    return from_shape(
        ShapelyPoint(centroid.longitude, centroid.latitude),
        srid=WGS84_SRID,
        extended=True,
    )


def element_to_centroid(element: WKBElement | None) -> Coordinates | None:
    """Convert a stored ``centroid`` value back to coordinates.

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
        message = f"centroid column holds a {point.geom_type}, not a Point"
        raise TypeError(message)
    return Coordinates(longitude=point.x, latitude=point.y)


def place_to_row(place: Place) -> PlaceRow:
    """Build the ``places`` row of ``place``.

    Args:
        place: The aggregate.

    Returns:
        A transient ``PlaceRow`` carrying the same values.
    """
    return PlaceRow(
        id=place.id,
        code=place.code,
        level=place.level.value,
        parent_id=place.parent_id,
        geometry=geometry_to_element(place.geometry),
        centroid=centroid_to_element(place.centroid),
        status=place.status,
        status_reason=place.status_reason,
        merged_into_id=place.merged_into_id,
        version=place.version,
        created_at=place.created_at,
        updated_at=place.updated_at,
    )


def names_to_rows(place: Place) -> list[PlaceNameRow]:
    """Build one ``place_names`` row per name of ``place``, in insertion order.

    ``text_folded`` is assigned a SQL expression (``search_form``) that PostgreSQL
    evaluates during the insert, because ``unaccent`` exists only in the database.

    Args:
        place: The aggregate.

    Returns:
        Transient rows; ``position`` records the order of ``place.names``.
    """
    rows: list[PlaceNameRow] = []
    for position, name in enumerate(place.names):
        row = PlaceNameRow(
            id=place_name_row_id(place.id, name),
            place_id=place.id,
            position=position,
            text=name.text,
            language=name.language,
            script=None if name.script is None else name.script.value,
            kind=name.kind,
            is_preferred=name.is_preferred,
            source_id=name.source_id,
        )
        # A SQL expression as the value: SQLAlchemy renders it inline in the INSERT.
        row.text_folded = search_form(name.text)
        rows.append(row)
    return rows


def row_to_name(row: PlaceNameRow) -> PlaceName:
    """Rebuild a name from its row.

    Args:
        row: A row loaded from ``place_names``.

    Returns:
        The validated ``PlaceName``.
    """
    # model_validate rather than the constructor: the kind column is a plain string
    # and PlaceName's validation is what rejects a value outside PlaceNameKind.
    return PlaceName.model_validate(
        {
            "text": row.text,
            "language": row.language,
            "script": None if row.script is None else ScriptCode(row.script),
            "kind": row.kind,
            "is_preferred": row.is_preferred,
            "source_id": row.source_id,
        }
    )


def row_to_place(
    row: PlaceRow,
    name_rows: Iterable[PlaceNameRow],
    *,
    include_geometry: bool = True,
) -> Place:
    """Rebuild the aggregate from its rows.

    Args:
        row: The ``places`` row.
        name_rows: Its ``place_names`` rows, in any order.
        include_geometry: Read the ``geometry`` column; read models pass ``False``
            after deferring the column, because they never show the footprint.

    Returns:
        The validated ``Place``.
    """
    ordered: Sequence[PlaceNameRow] = sorted(name_rows, key=lambda item: item.position)
    return Place.model_validate(
        {
            "id": row.id,
            "code": row.code,
            "level": AdminLevel(row.level),
            "parent_id": row.parent_id,
            "names": tuple(row_to_name(name_row) for name_row in ordered),
            "geometry": element_to_geometry(row.geometry) if include_geometry else None,
            "centroid": element_to_centroid(row.centroid),
            "status": row.status,
            "status_reason": row.status_reason,
            "merged_into_id": row.merged_into_id,
            "version": row.version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )
