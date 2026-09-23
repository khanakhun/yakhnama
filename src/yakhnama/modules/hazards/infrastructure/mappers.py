"""Translate between the ``HazardType`` aggregate and its row model.

JSONB columns hold the ``model_dump(mode="json")`` form of the value objects; reading
validates them back through the aggregate, so malformed stored JSON fails loudly at
this boundary instead of travelling on as a ``dict``.

Patterns: Anti-Corruption Layer (mapper).
"""

from pydantic import BaseModel

from yakhnama.modules.hazards.domain.entities import HazardType
from yakhnama.modules.hazards.infrastructure.orm import HazardTypeRow


def to_json(value: BaseModel | None) -> dict[str, object] | None:
    """Return the JSONB form of an optional value object.

    Args:
        value: A Pydantic value object, or ``None``.

    Returns:
        Its JSON-mode dump, or ``None``.
    """
    return None if value is None else value.model_dump(mode="json")


def hazard_type_to_row(hazard_type: HazardType) -> HazardTypeRow:
    """Build the ``hazard_types`` row of ``hazard_type``.

    Args:
        hazard_type: The aggregate.

    Returns:
        A transient ``HazardTypeRow`` carrying the same values.
    """
    return HazardTypeRow(
        id=hazard_type.id,
        code=hazard_type.code,
        parent_code=hazard_type.parent_code,
        labels=hazard_type.labels.model_dump(mode="json"),
        description=to_json(hazard_type.description),
        alignment=hazard_type.alignment.model_dump(mode="json"),
        attributes_schema=hazard_type.attributes_schema,
        status=hazard_type.status.value,
        retirement=to_json(hazard_type.retirement),
        version=hazard_type.version,
        created_at=hazard_type.created_at,
        updated_at=hazard_type.updated_at,
    )


def row_to_hazard_type(row: HazardTypeRow) -> HazardType:
    """Rebuild the aggregate from its row.

    Args:
        row: A row loaded from ``hazard_types``.

    Returns:
        The validated ``HazardType``.

    Raises:
        pydantic.ValidationError: If the stored values break an invariant.
    """
    return HazardType.model_validate(
        {
            "id": row.id,
            "code": row.code,
            "parent_code": row.parent_code,
            "labels": row.labels,
            "description": row.description,
            "alignment": row.alignment,
            "attributes_schema": row.attributes_schema,
            "status": row.status,
            "retirement": row.retirement,
            "version": row.version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )
