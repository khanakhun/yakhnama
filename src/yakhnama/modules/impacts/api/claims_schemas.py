"""Request bodies and small responses of the impact claims HTTP API.

Responses are otherwise the application's DTOs as they are: ``EventImpacts`` (best
figures and every claim), ``ImpactClaimSummary`` and
``InfrastructureAssetDetail``. The claim views never carry the moderator who
recorded or retracted a claim, the retraction reason or the moderator's note.
Request bodies reuse the domain's constrained types (claim values told apart by
``kind``, safe text, codes, WGS84 points).

Patterns: API Schema.
"""

from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, StringConstraints

from yakhnama.modules.impacts.domain.value_objects import (
    AssetName,
    ClaimNote,
    OsmId,
    PlaceCodeRef,
    RetractionReason,
)
from yakhnama.modules.impacts.public import (
    AssetKind,
    ClaimScope,
    ClaimValue,
    DamageLevel,
    MetricCode,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.value_objects import (
    Confidence,
    Coordinates,
    DateWithPrecision,
)

CODE_MAX_LENGTH: Final = 64
OSM_ID_MAX_LENGTH: Final = 32

BoundedMetricCode = Annotated[MetricCode, StringConstraints(max_length=CODE_MAX_LENGTH)]
BoundedPlaceCode = Annotated[
    PlaceCodeRef, StringConstraints(max_length=CODE_MAX_LENGTH)
]
BoundedOsmId = Annotated[OsmId, StringConstraints(max_length=OSM_ID_MAX_LENGTH)]


class RecordImpactClaimRequest(BaseModel):
    """Body of ``POST /api/v1/moderation/events/{event_id}/impact-claims``.

    Implements: API Schema.

    Attributes:
        metric_code: An active metric of the registry.
        value: The claimed value, of the metric's kind and unit or currency.
        confidence: How far the source's figure can be trusted.
        source_id: The provenance source; it becomes referenced.
        claimed_at: When the source made the claim, with its precision.
        scope: The part of the event covered; the whole event if omitted.
        note: A moderator's note, never published.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_code: BoundedMetricCode
    value: ClaimValue
    confidence: Confidence
    source_id: EntityId
    claimed_at: DateWithPrecision
    scope: ClaimScope | None = None
    note: ClaimNote | None = None


class RetractImpactClaimRequest(BaseModel):
    """Body of ``POST /api/v1/moderation/impact-claims/{claim_id}/retraction``.

    Implements: API Schema.

    Attributes:
        reason: Why, as safe text; kept on the claim, never published.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: RetractionReason


class CorrectImpactClaimRequest(BaseModel):
    """Body of ``POST /api/v1/moderation/impact-claims/{claim_id}/correction``.

    Implements: API Schema.

    Attributes:
        value: The corrected value.
        reason: Why the old claim is retracted.
        confidence: The corrected confidence; the old one if omitted.
        claimed_at: When the source made the corrected claim; the old time if
            omitted.
        note: A note for the new claim, if any.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: ClaimValue
    reason: RetractionReason
    confidence: Confidence | None = None
    claimed_at: DateWithPrecision | None = None
    note: ClaimNote | None = None


class RegisterInfrastructureAssetRequest(BaseModel):
    """Body of ``POST /api/v1/moderation/infrastructure-assets``.

    Implements: API Schema.

    Attributes:
        kind: What the asset is.
        name: Display name, 1 to 200 characters of safe text.
        source_id: The provenance source describing it.
        osm_id: Its OpenStreetMap element, for example ``way/123456``.
        location: A representative WGS84 point, if known.
        place_code: The geography place it lies in, if known.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: AssetKind
    name: AssetName
    source_id: EntityId
    osm_id: BoundedOsmId | None = None
    location: Coordinates | None = None
    place_code: BoundedPlaceCode | None = None


class RecordDamageRequest(BaseModel):
    """Body of ``POST /api/v1/moderation/events/{event_id}/damage-records``.

    Implements: API Schema.

    Attributes:
        asset_id: The damaged asset.
        level: How badly it was hit.
        confidence: How far the source can be trusted.
        source_id: The provenance source; it becomes referenced.
        recorded_at: When the source recorded the damage.
        note: A moderator's note, if any.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    asset_id: EntityId
    level: DamageLevel
    confidence: Confidence
    source_id: EntityId
    recorded_at: DateWithPrecision
    note: ClaimNote | None = None


class CreatedRecord(BaseModel):
    """The id of an append-only record the API has no read route for yet.

    Implements: API Schema.

    Attributes:
        id: The new record.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
