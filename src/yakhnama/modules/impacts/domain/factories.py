"""Creation of new ``ImpactMetric`` aggregates.

Patterns: Factory.
"""

from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.errors import InconsistentMetricDefinitionError
from yakhnama.modules.impacts.domain.events import ImpactMetricCreated
from yakhnama.modules.impacts.domain.reference import ImpactMetricReferenceEntry
from yakhnama.modules.impacts.domain.value_objects import (
    Aggregation,
    DesInventarField,
    MetricCategory,
    SendaiIndicator,
    ValueKind,
    default_aggregation,
    definition_problems,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import IdGenerator
from yakhnama.shared_kernel.value_objects import LocalizedText


class ImpactMetricFactory:
    """Builds new, active metrics with their ``ImpactMetricCreated`` event.

    Implements: Factory.
    """

    def __init__(self, *, clock: Clock, id_generator: IdGenerator) -> None:
        """Create the factory.

        Args:
            clock: Source of creation times.
            id_generator: Source of metric and event ids.
        """
        self._clock = clock
        self._id_generator = id_generator

    def create(  # noqa: PLR0913  # reason: one keyword per metric field, no grouping
        self,
        *,
        code: str,
        labels: LocalizedText,
        category: MetricCategory,
        value_kind: ValueKind,
        unit: str | None = None,
        currency: str | None = None,
        description: LocalizedText | None = None,
        sendai: SendaiIndicator | None = None,
        desinventar: DesInventarField | None = None,
        aggregation: Aggregation | None = None,
    ) -> AggregateChange[ImpactMetric]:
        """Create an active metric.

        The caller checks the code is not taken (``ImpactMetricRegistry``); a factory
        cannot see the registry.

        Args:
            code: New metric code.
            labels: Display names, English required.
            category: The metric's group.
            value_kind: Count, SI measurement or money.
            unit: Unit, following the ``value_kind`` rule.
            currency: ISO 4217 code for monetary metrics.
            description: Exact meaning, if written.
            sendai: Proposed Sendai indicator.
            desinventar: Proposed DesInventar field.
            aggregation: How claims combine; ``default_aggregation(value_kind)`` if
                omitted.

        Returns:
            The metric and one ``ImpactMetricCreated`` event.

        Raises:
            InconsistentMetricDefinitionError: If unit and currency contradict
                ``value_kind``.
            pydantic.ValidationError: If any other field is malformed.
        """
        problems = definition_problems(value_kind, unit, currency)
        if problems:
            raise InconsistentMetricDefinitionError(
                "; ".join(problems),
                details={"code": code, "value_kind": value_kind.value},
            )
        now = self._clock.now()
        metric = ImpactMetric(
            id=self._id_generator.new_id(),
            code=code,
            labels=labels,
            description=description,
            category=category,
            value_kind=value_kind,
            unit=unit,
            currency=currency,
            sendai=sendai,
            desinventar=desinventar,
            aggregation=aggregation or default_aggregation(value_kind),
            created_at=now,
            updated_at=now,
        )
        event = ImpactMetricCreated(
            event_id=self._id_generator.new_id(),
            occurred_at=now,
            aggregate_id=metric.id,
            code=metric.code,
            category=metric.category,
            value_kind=metric.value_kind,
            unit=metric.unit,
            currency=metric.currency,
        )
        return AggregateChange[ImpactMetric](state=metric, events=(event,))

    def create_from_reference(
        self, entry: ImpactMetricReferenceEntry
    ) -> AggregateChange[ImpactMetric]:
        """Create a metric from a reference file entry.

        A retired entry is created and then retired, so the event stream shows both
        facts in order.

        Args:
            entry: A validated reference entry.

        Returns:
            The metric with ``ImpactMetricCreated`` and, for a retired entry,
            ``ImpactMetricRetired``.
        """
        created = self.create(
            code=entry.code,
            labels=entry.labels,
            category=entry.category,
            value_kind=entry.value_kind,
            unit=entry.unit,
            currency=entry.currency,
            description=entry.description,
            sendai=entry.sendai,
            desinventar=entry.desinventar,
            aggregation=entry.aggregation,
        )
        # The entry guarantees a retirement reason exactly when it is retired.
        if entry.retirement is None:
            return created
        retired = created.state.retire(
            entry.retirement, clock=self._clock, id_generator=self._id_generator
        )
        return AggregateChange[ImpactMetric](
            state=retired.state, events=(*created.events, *retired.events)
        )
