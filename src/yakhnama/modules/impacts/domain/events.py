"""Domain events of the impact metric registry.

Patterns: Domain Events.
"""

from typing import ClassVar, Final, Literal

from yakhnama.modules.impacts.domain.value_objects import (
    MetricCategory,
    MetricCode,
    RetirementReason,
    ValueKind,
)
from yakhnama.shared_kernel.events import DomainEvent
from yakhnama.shared_kernel.value_objects import LocalizedText, Unit

IMPACT_METRIC_AGGREGATE_TYPE: Final = "impact_metric"


class ImpactMetricCreated(DomainEvent):
    """A metric was added to the registry.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"impact_metric"``.
        code: The new metric's code.
        category: Its category.
        value_kind: Its kind of value.
        unit: Its unit, ``None`` for monetary metrics.
        currency: Its currency, only for monetary metrics.
    """

    event_type: ClassVar[str] = "impacts.impact_metric_created"

    aggregate_type: Literal["impact_metric"] = IMPACT_METRIC_AGGREGATE_TYPE
    code: MetricCode
    category: MetricCategory
    value_kind: ValueKind
    unit: Unit | None
    currency: str | None


class ImpactMetricRetired(DomainEvent):
    """A metric was retired; its code stays taken for ever.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"impact_metric"``.
        code: The retired metric's code.
        reason: Why it was retired and what replaces it.
    """

    event_type: ClassVar[str] = "impacts.impact_metric_retired"

    aggregate_type: Literal["impact_metric"] = IMPACT_METRIC_AGGREGATE_TYPE
    code: MetricCode
    reason: RetirementReason


class ImpactMetricRelabelled(DomainEvent):
    """A metric's labels changed; its code, unit and meaning did not.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"impact_metric"``.
        code: The metric's code.
        labels: The new labels.
    """

    event_type: ClassVar[str] = "impacts.impact_metric_relabelled"

    aggregate_type: Literal["impact_metric"] = IMPACT_METRIC_AGGREGATE_TYPE
    code: MetricCode
    labels: LocalizedText
