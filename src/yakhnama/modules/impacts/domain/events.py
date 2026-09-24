"""Domain events of the impacts module.

Events carry ids and non-personal, structured fields only (the outbox payload
contract of Phase 3): free text such as notes and retraction reasons stays on the
aggregate, and the audit log reads it from there. Because ``DomainEvent`` already
uses ``event_id`` for the occurrence of the domain event, the hazard ``Event`` a claim
or damage record belongs to is carried as ``hazard_event_id``.

Patterns: Domain Events.
"""

from typing import ClassVar, Final, Literal

from yakhnama.modules.impacts.domain.value_objects import (
    AssetKind,
    ClaimScope,
    ClaimValue,
    DamageLevel,
    MetricCategory,
    MetricCode,
    OsmId,
    PlaceCodeRef,
    RetirementReason,
    SourceTypeName,
    ValueKind,
)
from yakhnama.shared_kernel.events import DomainEvent
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.value_objects import (
    Confidence,
    Coordinates,
    DateWithPrecision,
    LocalizedText,
    Unit,
)

IMPACT_METRIC_AGGREGATE_TYPE: Final = "impact_metric"
IMPACT_CLAIM_AGGREGATE_TYPE: Final = "impact_claim"
INFRASTRUCTURE_ASSET_AGGREGATE_TYPE: Final = "infrastructure_asset"
DAMAGE_RECORD_AGGREGATE_TYPE: Final = "damage_record"


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


class ImpactClaimRecorded(DomainEvent):
    """A new impact claim was recorded.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"impact_claim"``.
        hazard_event_id: The event the claim is about.
        metric_code: The metric's code.
        value: The claimed value.
        confidence: How far the source's figure can be trusted.
        source_id: The source the figure comes from.
        source_type: That source's type.
        claimed_at: When the source made the claim.
        scope: What part of the event the claim covers.
        recorded_by: The account that recorded it.
    """

    event_type: ClassVar[str] = "impacts.impact_claim_recorded"

    aggregate_type: Literal["impact_claim"] = IMPACT_CLAIM_AGGREGATE_TYPE
    hazard_event_id: EntityId
    metric_code: MetricCode
    value: ClaimValue
    confidence: Confidence
    source_id: EntityId
    source_type: SourceTypeName
    claimed_at: DateWithPrecision
    scope: ClaimScope
    recorded_by: EntityId


class ImpactClaimRetracted(DomainEvent):
    """A claim was retracted; it stays stored and no longer counts.

    The reason is free text and stays on the claim.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"impact_claim"``.
        hazard_event_id: The event the claim is about.
        metric_code: The metric's code.
        retracted_by: The account that retracted it.
        superseded_by_id: The correcting claim, when the retraction is half of a
            correction.
    """

    event_type: ClassVar[str] = "impacts.impact_claim_retracted"

    aggregate_type: Literal["impact_claim"] = IMPACT_CLAIM_AGGREGATE_TYPE
    hazard_event_id: EntityId
    metric_code: MetricCode
    retracted_by: EntityId
    superseded_by_id: EntityId | None = None


class ImpactClaimCorrected(DomainEvent):
    """A claim was recorded as the correction of an earlier one.

    ``aggregate_id`` is the new claim; the earlier claim is retracted in the same
    change (``ImpactClaimRetracted``).

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"impact_claim"``.
        hazard_event_id: The event the claims are about.
        metric_code: The metric's code.
        supersedes_id: The claim this one corrects.
        value: The corrected value.
        confidence: The corrected confidence.
        claimed_at: When the source made the corrected claim.
        recorded_by: The account that recorded the correction.
    """

    event_type: ClassVar[str] = "impacts.impact_claim_corrected"

    aggregate_type: Literal["impact_claim"] = IMPACT_CLAIM_AGGREGATE_TYPE
    hazard_event_id: EntityId
    metric_code: MetricCode
    supersedes_id: EntityId
    value: ClaimValue
    confidence: Confidence
    claimed_at: DateWithPrecision
    recorded_by: EntityId


class InfrastructureAssetRegistered(DomainEvent):
    """An infrastructure asset was registered.

    The name is free text and stays on the asset.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"infrastructure_asset"``.
        kind: What the asset is.
        osm_id: Its OpenStreetMap element, if known.
        place_code: The place it is in, if known.
        source_id: The source the asset's description comes from.
    """

    event_type: ClassVar[str] = "impacts.infrastructure_asset_registered"

    aggregate_type: Literal["infrastructure_asset"] = (
        INFRASTRUCTURE_ASSET_AGGREGATE_TYPE
    )
    kind: AssetKind
    osm_id: OsmId | None
    place_code: PlaceCodeRef | None
    source_id: EntityId


class InfrastructureAssetRenamed(DomainEvent):
    """An asset's name changed; the name itself stays on the asset.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"infrastructure_asset"``.
    """

    event_type: ClassVar[str] = "impacts.infrastructure_asset_renamed"

    aggregate_type: Literal["infrastructure_asset"] = (
        INFRASTRUCTURE_ASSET_AGGREGATE_TYPE
    )


class InfrastructureAssetRelocated(DomainEvent):
    """An asset's location, place or OpenStreetMap element changed.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"infrastructure_asset"``.
        location: The new point, if any.
        place_code: The new place, if any.
        osm_id: The new OpenStreetMap element, if any.
    """

    event_type: ClassVar[str] = "impacts.infrastructure_asset_relocated"

    aggregate_type: Literal["infrastructure_asset"] = (
        INFRASTRUCTURE_ASSET_AGGREGATE_TYPE
    )
    location: Coordinates | None
    place_code: PlaceCodeRef | None
    osm_id: OsmId | None


class DamageRecorded(DomainEvent):
    """Damage to an asset during an event was recorded.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"damage_record"``.
        hazard_event_id: The event that caused the damage.
        asset_id: The damaged asset.
        level: How badly it was hit.
        confidence: How far the source can be trusted.
        source_id: The source of the record.
        recorded_at: When the source recorded the damage.
        recorded_by: The account that entered it.
    """

    event_type: ClassVar[str] = "impacts.damage_recorded"

    aggregate_type: Literal["damage_record"] = DAMAGE_RECORD_AGGREGATE_TYPE
    hazard_event_id: EntityId
    asset_id: EntityId
    level: DamageLevel
    confidence: Confidence
    source_id: EntityId
    recorded_at: DateWithPrecision
    recorded_by: EntityId


class DamageRetracted(DomainEvent):
    """A damage record was retracted; it stays stored and no longer counts.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"damage_record"``.
        hazard_event_id: The event of the record.
        asset_id: The asset of the record.
        retracted_by: The account that retracted it.
    """

    event_type: ClassVar[str] = "impacts.damage_retracted"

    aggregate_type: Literal["damage_record"] = DAMAGE_RECORD_AGGREGATE_TYPE
    hazard_event_id: EntityId
    asset_id: EntityId
    retracted_by: EntityId
