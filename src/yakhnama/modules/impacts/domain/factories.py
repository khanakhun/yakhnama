"""Creation of new impacts aggregates: metrics, claims, assets and damage records.

Patterns: Factory.
"""

from yakhnama.modules.impacts.domain.assets import InfrastructureAsset
from yakhnama.modules.impacts.domain.claims import (
    ImpactClaim,
    ensure_metric_accepts_claims,
    ensure_value_fits_metric,
)
from yakhnama.modules.impacts.domain.damage import DamageRecord
from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.errors import InconsistentMetricDefinitionError
from yakhnama.modules.impacts.domain.events import (
    DamageRecorded,
    ImpactClaimRecorded,
    ImpactMetricCreated,
    InfrastructureAssetRegistered,
)
from yakhnama.modules.impacts.domain.reference import ImpactMetricReferenceEntry
from yakhnama.modules.impacts.domain.value_objects import (
    Aggregation,
    AssetKind,
    ClaimScope,
    ClaimValue,
    DamageLevel,
    DesInventarField,
    MetricCategory,
    SendaiIndicator,
    SourceTypeName,
    ValueKind,
    default_aggregation,
    definition_problems,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import EntityId, IdGenerator
from yakhnama.shared_kernel.value_objects import (
    Confidence,
    Coordinates,
    DateWithPrecision,
    LocalizedText,
)


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


class ImpactClaimFactory:
    """Records new, active impact claims with their ``ImpactClaimRecorded`` event.

    Implements: Factory.
    """

    def __init__(self, *, clock: Clock, id_generator: IdGenerator) -> None:
        """Create the factory.

        Args:
            clock: Source of recording times.
            id_generator: Source of claim and event ids.
        """
        self._clock = clock
        self._id_generator = id_generator

    def record(  # noqa: PLR0913  # reason: one keyword per claim field, no grouping
        self,
        *,
        metric: ImpactMetric,
        event_id: EntityId,
        value: ClaimValue,
        confidence: Confidence,
        source_id: EntityId,
        source_type: SourceTypeName,
        claimed_at: DateWithPrecision,
        recorded_by: EntityId,
        scope: ClaimScope | None = None,
        note: str | None = None,
    ) -> AggregateChange[ImpactClaim]:
        """Record a claim after checking the value against its metric.

        The caller checks that the event and the source exist; a factory cannot see
        other aggregates.

        Args:
            metric: The metric the value is claimed for; must be active.
            event_id: The hazard event the claim is about.
            value: The claimed value, of the metric's kind and unit or currency.
            confidence: How far the source's figure can be trusted.
            source_id: The provenance source of the figure.
            source_type: That source's type.
            claimed_at: When the source made the claim.
            recorded_by: The account recording it.
            scope: The part of the event covered; the whole event if omitted.
            note: A moderator's note, if any.

        Returns:
            The claim and one ``ImpactClaimRecorded`` event.

        Raises:
            ImpactMetricRetiredError: If the metric is retired.
            ClaimValueKindMismatchError: If the value's kind is not the metric's.
            ClaimValueUnitMismatchError: If its unit or currency is not the metric's.
            pydantic.ValidationError: If any other field is malformed.
        """
        ensure_metric_accepts_claims(metric)
        ensure_value_fits_metric(metric, value)
        now = self._clock.now()
        claim = ImpactClaim(
            id=self._id_generator.new_id(),
            event_id=event_id,
            metric=metric.ref,
            value=value,
            confidence=confidence,
            source_id=source_id,
            source_type=source_type,
            claimed_at=claimed_at,
            recorded_by=recorded_by,
            scope=scope or ClaimScope(),
            note=note,
            created_at=now,
            updated_at=now,
        )
        event = ImpactClaimRecorded(
            event_id=self._id_generator.new_id(),
            occurred_at=now,
            aggregate_id=claim.id,
            hazard_event_id=claim.event_id,
            metric_code=claim.metric.code,
            value=claim.value,
            confidence=claim.confidence,
            source_id=claim.source_id,
            source_type=claim.source_type,
            claimed_at=claim.claimed_at,
            scope=claim.scope,
            recorded_by=claim.recorded_by,
        )
        return AggregateChange[ImpactClaim](state=claim, events=(event,))


class InfrastructureAssetFactory:
    """Registers new infrastructure assets with their registration event.

    Implements: Factory.
    """

    def __init__(self, *, clock: Clock, id_generator: IdGenerator) -> None:
        """Create the factory.

        Args:
            clock: Source of registration times.
            id_generator: Source of asset and event ids.
        """
        self._clock = clock
        self._id_generator = id_generator

    def register(  # noqa: PLR0913  # reason: one keyword per asset field, no grouping
        self,
        *,
        kind: AssetKind,
        name: str,
        source_id: EntityId,
        osm_id: str | None = None,
        location: Coordinates | None = None,
        place_code: str | None = None,
    ) -> AggregateChange[InfrastructureAsset]:
        """Register an asset.

        The caller checks that no asset with the same ``osm_id`` exists.

        Args:
            kind: What the asset is.
            name: Display name, 1 to 200 characters of safe text.
            source_id: The provenance source describing the asset.
            osm_id: Its OpenStreetMap element, if known.
            location: A representative point, if known.
            place_code: The place it lies in, if known.

        Returns:
            The asset and one ``InfrastructureAssetRegistered`` event.

        Raises:
            pydantic.ValidationError: If a field is malformed.
        """
        now = self._clock.now()
        asset = InfrastructureAsset(
            id=self._id_generator.new_id(),
            kind=kind,
            name=name,
            osm_id=osm_id,
            location=location,
            place_code=place_code,
            source_id=source_id,
            created_at=now,
            updated_at=now,
        )
        event = InfrastructureAssetRegistered(
            event_id=self._id_generator.new_id(),
            occurred_at=now,
            aggregate_id=asset.id,
            kind=asset.kind,
            osm_id=asset.osm_id,
            place_code=asset.place_code,
            source_id=asset.source_id,
        )
        return AggregateChange[InfrastructureAsset](state=asset, events=(event,))


class DamageRecordFactory:
    """Records new, active damage records with their ``DamageRecorded`` event.

    Implements: Factory.
    """

    def __init__(self, *, clock: Clock, id_generator: IdGenerator) -> None:
        """Create the factory.

        Args:
            clock: Source of recording times.
            id_generator: Source of record and event ids.
        """
        self._clock = clock
        self._id_generator = id_generator

    def record(  # noqa: PLR0913  # reason: one keyword per record field, no grouping
        self,
        *,
        event_id: EntityId,
        asset_id: EntityId,
        level: DamageLevel,
        confidence: Confidence,
        source_id: EntityId,
        recorded_at: DateWithPrecision,
        recorded_by: EntityId,
        note: str | None = None,
    ) -> AggregateChange[DamageRecord]:
        """Record damage to an asset.

        The caller checks that the event, the asset and the source exist.

        Args:
            event_id: The hazard event that caused the damage.
            asset_id: The damaged asset.
            level: How badly it was hit.
            confidence: How far the source can be trusted.
            source_id: The provenance source of the record.
            recorded_at: When the source recorded the damage.
            recorded_by: The account entering it.
            note: A moderator's note, if any.

        Returns:
            The record and one ``DamageRecorded`` event.

        Raises:
            pydantic.ValidationError: If a field is malformed.
        """
        now = self._clock.now()
        record = DamageRecord(
            id=self._id_generator.new_id(),
            event_id=event_id,
            asset_id=asset_id,
            level=level,
            confidence=confidence,
            source_id=source_id,
            recorded_at=recorded_at,
            recorded_by=recorded_by,
            note=note,
            created_at=now,
            updated_at=now,
        )
        event = DamageRecorded(
            event_id=self._id_generator.new_id(),
            occurred_at=now,
            aggregate_id=record.id,
            hazard_event_id=record.event_id,
            asset_id=record.asset_id,
            level=record.level,
            confidence=record.confidence,
            source_id=record.source_id,
            recorded_at=record.recorded_at,
            recorded_by=record.recorded_by,
        )
        return AggregateChange[DamageRecord](state=record, events=(event,))
