"""Translate between the ``Event`` aggregate, relations and their row models.

Shapely and WKB stay on this side of the boundary: the footprint travels as
``EventGeometry`` (GeoJSON) in the domain and as a GeoAlchemy2 ``WKBElement`` here.
WKB keeps every coordinate bit for bit, so a stored geometry and centroid read back
equal to the ones saved, and the aggregate's "centroid equals the geometry's
centroid" invariant still holds after a round trip. Every read goes through
``model_validate``; hazard attributes go through ``DEFAULT_REGISTRY.validate`` under
the stored hazard code, so a row reloads with its concrete attribute class and a row
that no longer satisfies the domain fails loudly.

Patterns: Anti-Corruption Layer (mapper).
"""

import uuid
from datetime import datetime
from typing import Final

from geoalchemy2 import WKBElement
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point as ShapelyPoint
from shapely.geometry import mapping, shape

from yakhnama.modules.events.domain.entities import Event
from yakhnama.modules.events.domain.value_objects import (
    AffectedPlace,
    EventGeometry,
    EventPeriod,
    EventRelation,
)
from yakhnama.modules.events.infrastructure.orm import (
    WGS84_SRID,
    EventRelationRow,
    EventReportLinkRow,
    EventRow,
)
from yakhnama.modules.hazards.public import DEFAULT_REGISTRY, HazardAttributes
from yakhnama.shared_kernel.value_objects import Coordinates, DateWithPrecision

RELATION_ID_NAMESPACE: Final = uuid.UUID("5d0b8f4e-6a57-4c1b-9d1f-3f1e0c7a2b91")
"""UUIDv5 namespace of relation row ids; fixed forever once rows exist."""


def relation_row_id(relation: EventRelation) -> uuid.UUID:
    """Return the deterministic row id of a relation, derived from its key.

    ``EventRelation.key`` orders ``same_as`` ends, so both directions of one
    ``same_as`` pair map to the same id and the primary key rejects the second.

    Args:
        relation: The relation.

    Returns:
        A UUIDv5 of the key in ``RELATION_ID_NAMESPACE``.
    """
    low, high, kind = relation.key
    return uuid.uuid5(RELATION_ID_NAMESPACE, f"{low}\x1f{high}\x1f{kind.value}")


def geometry_to_element(geometry: EventGeometry | None) -> WKBElement | None:
    """Convert an event footprint to a WGS84 ``WKBElement``.

    Args:
        geometry: The footprint, or ``None``.

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


def element_to_geometry(element: WKBElement | None) -> EventGeometry | None:
    """Convert a stored ``geometry`` value back to the domain footprint.

    Args:
        element: The column value, or ``None``.

    Returns:
        The validated ``EventGeometry``, or ``None``.
    """
    if element is None:
        return None
    return EventGeometry.model_validate({"geojson": mapping(to_shape(element))})


def coordinates_to_element(coordinates: Coordinates | None) -> WKBElement | None:
    """Convert a point to a WGS84 point ``WKBElement``.

    Args:
        coordinates: The point, or ``None``.

    Returns:
        The extended WKB value for a ``geometry(Point, 4326)`` column, or ``None``.
    """
    if coordinates is None:
        return None
    return from_shape(
        ShapelyPoint(coordinates.longitude, coordinates.latitude),
        srid=WGS84_SRID,
        extended=True,
    )


def element_to_coordinates(element: WKBElement | None) -> Coordinates | None:
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
        message = f"centroid column holds a {point.geom_type}, not a Point"
        raise TypeError(message)
    return Coordinates(longitude=point.x, latitude=point.y)


def period_from_columns(
    started_at: datetime,
    started_at_precision: str,
    ended_at: datetime | None,
    ended_at_precision: str | None,
) -> EventPeriod:
    """Join the four period columns into one value.

    Args:
        started_at: The stored start instant.
        started_at_precision: Its precision.
        ended_at: The stored end instant, if any.
        ended_at_precision: Its precision, if any.

    Returns:
        The validated period.
    """
    return EventPeriod(
        started_at=DateWithPrecision.model_validate(
            {"value": started_at, "precision": started_at_precision}
        ),
        ended_at=(
            None
            if ended_at is None
            else DateWithPrecision.model_validate(
                {"value": ended_at, "precision": ended_at_precision}
            )
        ),
    )


def affected_places_from_column(
    values: list[dict[str, object]],
) -> tuple[AffectedPlace, ...]:
    """Validate the stored ``affected_places`` array.

    Args:
        values: The decoded JSONB array.

    Returns:
        The places, in stored order.
    """
    return tuple(AffectedPlace.model_validate(value) for value in values)


def attributes_from_column(
    hazard_code: str, value: dict[str, object] | None
) -> HazardAttributes | None:
    """Validate stored hazard attributes through the registry.

    Args:
        hazard_code: The event's hazard code; the registry code of its schema.
        value: The decoded JSONB object, or ``None``.

    Returns:
        The concrete attribute model, or ``None``.

    Raises:
        UnknownHazardAttributesError: If no schema is registered for the code.
        pydantic.ValidationError: If the stored value no longer fits the schema.
    """
    return None if value is None else DEFAULT_REGISTRY.validate(hazard_code, value)


def event_to_values(event: Event) -> dict[str, object]:
    """Return every ``events`` column value of ``event`` except the primary key.

    Args:
        event: The aggregate.

    Returns:
        Column name to value, for inserts and versioned updates alike.
    """
    period = event.period
    ended = period.ended_at
    return {
        "hazard_code": event.hazard_type.code,
        "title": event.title,
        "summary": event.summary,
        "started_at": period.started_at.value,
        "started_at_precision": period.started_at.precision.value,
        "ended_at": None if ended is None else ended.value,
        "ended_at_precision": None if ended is None else ended.precision.value,
        "period_earliest_at": period.earliest_instant(),
        "period_latest_at": period.latest_instant(),
        "geometry": geometry_to_element(event.geometry),
        "centroid": coordinates_to_element(event.centroid),
        "attributes": (
            None
            if event.attributes is None
            else event.attributes.model_dump(mode="json")
        ),
        "source_ids": [str(source_id) for source_id in event.source_ids],
        "affected_places": [
            place.model_dump(mode="json") for place in event.affected_places
        ],
        "report_links": [link.model_dump(mode="json") for link in event.report_links],
        "unlinked_reports": [
            unlink.model_dump(mode="json") for unlink in event.unlinked_reports
        ],
        "status": event.status.value,
        "status_reason": event.status_reason,
        "merged_into": event.merged_into,
        "created_by": event.created_by,
        "version": event.version,
        "created_at": event.created_at,
        "updated_at": event.updated_at,
    }


def event_to_row(event: Event) -> EventRow:
    """Build the ``events`` row of ``event``.

    Args:
        event: The aggregate.

    Returns:
        A transient ``EventRow`` carrying the same values.
    """
    return EventRow(id=event.id, **event_to_values(event))


def report_link_rows(event: Event) -> list[EventReportLinkRow]:
    """Build the ``event_report_links`` projection rows of ``event``.

    Args:
        event: The aggregate.

    Returns:
        One transient row per current report link.
    """
    return [
        EventReportLinkRow(
            event_id=event.id,
            report_id=link.report_id,
            role=link.role,
            linked_by=link.linked_by,
            linked_at=link.linked_at,
        )
        for link in event.report_links
    ]


def row_to_event(row: EventRow) -> Event:
    """Rebuild an event from its row.

    Args:
        row: A fully loaded row of ``events``.

    Returns:
        The validated ``Event``.
    """
    return Event.model_validate(
        {
            "id": row.id,
            "hazard_type": {"code": row.hazard_code},
            "title": row.title,
            "summary": row.summary,
            "period": period_from_columns(
                row.started_at,
                row.started_at_precision,
                row.ended_at,
                row.ended_at_precision,
            ),
            "geometry": element_to_geometry(row.geometry),
            "centroid": element_to_coordinates(row.centroid),
            "affected_places": affected_places_from_column(row.affected_places),
            "attributes": attributes_from_column(row.hazard_code, row.attributes),
            "source_ids": tuple(uuid.UUID(value) for value in row.source_ids),
            "report_links": row.report_links,
            "unlinked_reports": row.unlinked_reports,
            "status": row.status,
            "status_reason": row.status_reason,
            "merged_into": row.merged_into,
            "created_by": row.created_by,
            "version": row.version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


def relation_to_row(relation: EventRelation) -> EventRelationRow:
    """Build the ``event_relations`` row of a relation.

    Args:
        relation: The relation.

    Returns:
        A transient row with the key-derived id.
    """
    return EventRelationRow(
        id=relation_row_id(relation),
        from_event_id=relation.from_event_id,
        to_event_id=relation.to_event_id,
        kind=relation.kind.value,
        note=relation.note,
        related_by=relation.related_by,
        related_at=relation.related_at,
    )


def row_to_relation(row: EventRelationRow) -> EventRelation:
    """Rebuild a relation from its row.

    Args:
        row: A row of ``event_relations``.

    Returns:
        The validated ``EventRelation``.
    """
    return EventRelation.model_validate(
        {
            "from_event_id": row.from_event_id,
            "to_event_id": row.to_event_id,
            "kind": row.kind,
            "note": row.note,
            "related_by": row.related_by,
            "related_at": row.related_at,
        }
    )
