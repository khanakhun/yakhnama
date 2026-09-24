"""Write requests for impact claims, infrastructure assets and damage records.

Every command carries the ``actor`` it runs as; the handler asks its
``AuthorisationPolicy`` (``CanModerate``) about that actor before anything else.
Claims and damage records are append-only: the only changes are retraction with a
reason and, for claims, correction by a new claim that supersedes the old one.

Patterns: Command.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.identity.public import Actor
from yakhnama.modules.impacts.domain.value_objects import (
    AssetKind,
    AssetName,
    ClaimNote,
    ClaimScope,
    ClaimValue,
    DamageLevel,
    MetricCode,
    OsmId,
    PlaceCodeRef,
    RetractionReason,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.value_objects import (
    Confidence,
    Coordinates,
    DateWithPrecision,
)


class RecordImpactClaim(BaseModel):
    """Record one metric value for one event from one source.

    Implements: Command.

    Attributes:
        actor: The moderator recording it.
        event_id: The hazard event the claim is about; must exist.
        metric_code: The metric; must be active in the registry.
        value: The claimed value, of the metric's kind and unit or currency.
        confidence: How far the source's figure can be trusted.
        source_id: The provenance source; it becomes referenced.
        claimed_at: When the source made the claim, with its precision.
        scope: The part of the event covered; the whole event if omitted.
        note: A moderator's note, if any; never published in events.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    event_id: EntityId
    metric_code: MetricCode
    value: ClaimValue
    confidence: Confidence
    source_id: EntityId
    claimed_at: DateWithPrecision
    scope: ClaimScope | None = None
    note: ClaimNote | None = None


class RetractImpactClaim(BaseModel):
    """Retract a claim; it stays stored and stops counting.

    Implements: Command.

    Attributes:
        actor: The moderator.
        claim_id: The claim.
        reason: Why.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    claim_id: EntityId
    reason: RetractionReason


class CorrectImpactClaim(BaseModel):
    """Replace a claim by a new one from the same source, retracting the old one.

    Implements: Command.

    Attributes:
        actor: The moderator.
        claim_id: The claim being corrected.
        value: The corrected value.
        reason: Why the old claim is retracted.
        confidence: The corrected confidence; the old one if omitted.
        claimed_at: When the source made the corrected claim; the old time if
            omitted.
        note: A note for the new claim, if any.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    claim_id: EntityId
    value: ClaimValue
    reason: RetractionReason
    confidence: Confidence | None = None
    claimed_at: DateWithPrecision | None = None
    note: ClaimNote | None = None


class RegisterInfrastructureAsset(BaseModel):
    """Register a bridge, road segment or other asset hazards can damage.

    Implements: Command.

    Attributes:
        actor: The moderator.
        kind: What the asset is.
        name: Display name.
        source_id: The provenance source describing it; it becomes referenced.
        osm_id: Its OpenStreetMap element, if known; unique among assets.
        location: A representative WGS84 point, if known.
        place_code: The geography place it lies in, if known.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    kind: AssetKind
    name: AssetName
    source_id: EntityId
    osm_id: OsmId | None = None
    location: Coordinates | None = None
    place_code: PlaceCodeRef | None = None


class RecordDamage(BaseModel):
    """Record one source's statement that an asset was damaged during an event.

    Implements: Command.

    Attributes:
        actor: The moderator.
        event_id: The hazard event; must exist.
        asset_id: The damaged asset; must exist.
        level: How badly it was hit.
        confidence: How far the source can be trusted.
        source_id: The provenance source; it becomes referenced.
        recorded_at: When the source recorded the damage.
        note: A moderator's note, if any.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    event_id: EntityId
    asset_id: EntityId
    level: DamageLevel
    confidence: Confidence
    source_id: EntityId
    recorded_at: DateWithPrecision
    note: ClaimNote | None = None


class RetractDamage(BaseModel):
    """Retract a damage record; it stays stored and no longer stands.

    Implements: Command.

    Attributes:
        actor: The moderator.
        damage_id: The damage record.
        reason: Why.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    damage_id: EntityId
    reason: RetractionReason
