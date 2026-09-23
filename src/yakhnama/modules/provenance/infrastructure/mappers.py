"""Translate between the ``Source`` aggregate and its row model.

Every read goes through ``model_validate``, so a row that no longer satisfies the
domain's rules (an unknown source type, a malformed licence, a URL with credentials)
fails loudly instead of producing an invalid aggregate.

Patterns: Anti-Corruption Layer (mapper).
"""

from datetime import datetime

from yakhnama.modules.provenance.domain.entities import Source
from yakhnama.modules.provenance.infrastructure.orm import SourceRow
from yakhnama.platform.db import dump_json_column
from yakhnama.shared_kernel.value_objects import DateWithPrecision


def date_to_columns(
    value: DateWithPrecision | None,
) -> tuple[datetime | None, str | None]:
    """Split an optional dated value into its instant and precision columns.

    Args:
        value: The dated value, or ``None``.

    Returns:
        ``(instant, precision)``, both ``None`` when ``value`` is.
    """
    if value is None:
        return None, None
    return value.value, value.precision.value


def columns_to_date(
    instant: datetime | None, precision: str | None
) -> DateWithPrecision | None:
    """Join an instant and precision column pair back into a dated value.

    Args:
        instant: The stored instant, or ``None``.
        precision: The stored precision, or ``None``.

    Returns:
        The validated value, or ``None`` if the instant is ``NULL``.
    """
    if instant is None:
        return None
    return DateWithPrecision.model_validate({"value": instant, "precision": precision})


def source_to_values(source: Source) -> dict[str, object]:
    """Return every column value of ``source`` except the primary key.

    Args:
        source: The aggregate.

    Returns:
        Column name to value, for inserts and versioned updates alike.
    """
    retrieved_at, retrieved_at_precision = date_to_columns(source.retrieved_at)
    return {
        "source_type": source.source_type.value,
        "title": source.title,
        "citation": source.citation,
        "url": source.url,
        "licence": dump_json_column(source.licence),
        "retrieved_at": retrieved_at,
        "retrieved_at_precision": retrieved_at_precision,
        "publisher": source.publisher,
        "language": source.language,
        "owner_actor_id": source.owner_actor_id,
        "organization_id": source.organization_id,
        "is_referenced": source.is_referenced,
        "version": source.version,
        "created_at": source.created_at,
        "updated_at": source.updated_at,
    }


def source_to_row(source: Source) -> SourceRow:
    """Build the ``sources`` row of ``source``.

    Args:
        source: The aggregate.

    Returns:
        A transient ``SourceRow`` carrying the same values.
    """
    return SourceRow(id=source.id, **source_to_values(source))


def row_to_source(row: SourceRow) -> Source:
    """Rebuild a source from its row.

    Args:
        row: A row loaded from ``sources``.

    Returns:
        The validated ``Source``.
    """
    return Source.model_validate(
        {
            "id": row.id,
            "source_type": row.source_type,
            "title": row.title,
            "citation": row.citation,
            "url": row.url,
            "licence": row.licence,
            "retrieved_at": columns_to_date(
                row.retrieved_at, row.retrieved_at_precision
            ),
            "publisher": row.publisher,
            "language": row.language,
            "owner_actor_id": row.owner_actor_id,
            "organization_id": row.organization_id,
            "is_referenced": row.is_referenced,
            "version": row.version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )
