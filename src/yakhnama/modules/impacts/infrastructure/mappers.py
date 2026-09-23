"""Translate between the ``ImpactMetric`` aggregate and its row model.

JSONB columns hold the ``model_dump(mode="json")`` form of the value objects; reading
validates them back through the aggregate, so malformed stored JSON fails loudly at
this boundary instead of travelling on as a ``dict``.

Patterns: Anti-Corruption Layer (mapper).
"""

from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.infrastructure.orm import ImpactMetricRow
from yakhnama.platform.db import dump_json_column


def metric_to_row(metric: ImpactMetric) -> ImpactMetricRow:
    """Build the ``impact_metrics`` row of ``metric``.

    Args:
        metric: The aggregate.

    Returns:
        A transient ``ImpactMetricRow`` carrying the same values.
    """
    return ImpactMetricRow(
        id=metric.id,
        code=metric.code,
        labels=metric.labels.model_dump(mode="json"),
        description=dump_json_column(metric.description),
        category=metric.category.value,
        value_kind=metric.value_kind.value,
        unit=metric.unit,
        currency=metric.currency,
        sendai=dump_json_column(metric.sendai),
        desinventar=dump_json_column(metric.desinventar),
        aggregation=metric.aggregation,
        status=metric.status.value,
        retirement=dump_json_column(metric.retirement),
        version=metric.version,
        created_at=metric.created_at,
        updated_at=metric.updated_at,
    )


def row_to_metric(row: ImpactMetricRow) -> ImpactMetric:
    """Rebuild the aggregate from its row.

    Args:
        row: A row loaded from ``impact_metrics``.

    Returns:
        The validated ``ImpactMetric``.

    Raises:
        pydantic.ValidationError: If the stored values break an invariant.
    """
    return ImpactMetric.model_validate(
        {
            "id": row.id,
            "code": row.code,
            "labels": row.labels,
            "description": row.description,
            "category": row.category,
            "value_kind": row.value_kind,
            "unit": row.unit,
            "currency": row.currency,
            "sendai": row.sendai,
            "desinventar": row.desinventar,
            "aggregation": row.aggregation,
            "status": row.status,
            "retirement": row.retirement,
            "version": row.version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )
