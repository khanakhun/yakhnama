"""Read models of impact claims, best figures and infrastructure assets.

Public claim views leave out who recorded or retracted a claim, its note and its
retraction reason: those are moderation records and may hold personal details, and
the claim's provenance is its ``source_id``. ``from_entity`` builds the views for
in-memory implementations; the SQL query service builds them from selected columns
with the same field meanings.

Patterns: DTO.
"""

from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from yakhnama.modules.impacts.domain.assets import InfrastructureAsset
from yakhnama.modules.impacts.domain.best_figure import BestFigure
from yakhnama.modules.impacts.domain.claims import ImpactClaim
from yakhnama.modules.impacts.domain.value_objects import (
    AssetKind,
    AssetName,
    ClaimScope,
    ClaimStatus,
    ClaimValue,
    MetricCode,
    OsmId,
    PlaceCodeRef,
    SourceTypeName,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.value_objects import (
    Confidence,
    Coordinates,
    DateWithPrecision,
)


class ImpactClaimSummary(BaseModel):
    """One impact claim as the open dataset shows it.

    Implements: DTO.

    Attributes:
        id: The claim.
        event_id: The hazard event it is about.
        metric_code: The metric.
        value: The claimed value.
        confidence: How far the source's figure can be trusted.
        source_id: The source the figure comes from.
        source_type: That source's type.
        claimed_at: When the source made the claim.
        scope: The part of the event it covers.
        status: ``active`` or ``retracted``.
        supersedes_id: The claim it corrects, if any.
        created_at: When it was recorded in Yakhnama, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    event_id: EntityId
    metric_code: MetricCode
    value: ClaimValue
    confidence: Confidence
    source_id: EntityId
    source_type: SourceTypeName
    claimed_at: DateWithPrecision
    scope: ClaimScope
    status: ClaimStatus
    supersedes_id: EntityId | None
    created_at: AwareDatetime

    @classmethod
    def from_entity(cls, claim: ImpactClaim) -> Self:
        """Build the public view of a claim.

        Args:
            claim: The aggregate.

        Returns:
            Its view.
        """
        return cls(
            id=claim.id,
            event_id=claim.event_id,
            metric_code=claim.metric.code,
            value=claim.value,
            confidence=claim.confidence,
            source_id=claim.source_id,
            source_type=claim.source_type,
            claimed_at=claim.claimed_at,
            scope=claim.scope,
            status=claim.status,
            supersedes_id=claim.supersedes_id,
            created_at=claim.created_at,
        )


class EventImpacts(BaseModel):
    """Every claim of one event and the best figure of each metric claimed.

    Implements: DTO.

    Attributes:
        event_id: The hazard event.
        best_figures: One figure per metric with at least one claim, by code; a
            metric whose claims are all retracted has basis ``none``.
        claims: Every claim, active and retracted, in recording order.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: EntityId
    best_figures: tuple[BestFigure, ...]
    claims: tuple[ImpactClaimSummary, ...]


class InfrastructureAssetDetail(BaseModel):
    """One infrastructure asset.

    Implements: DTO.

    Attributes:
        id: The asset.
        kind: What it is.
        name: Display name.
        osm_id: Its OpenStreetMap element, if known.
        location: A representative WGS84 point, if known.
        place_code: The place it lies in, if known.
        source_id: The source describing it.
        version: Optimistic-concurrency version.
        created_at: When it was registered, UTC.
        updated_at: When it last changed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    kind: AssetKind
    name: AssetName
    osm_id: OsmId | None
    location: Coordinates | None
    place_code: PlaceCodeRef | None
    source_id: EntityId
    version: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @classmethod
    def from_entity(cls, asset: InfrastructureAsset) -> Self:
        """Build the detail view of an asset.

        Args:
            asset: The aggregate.

        Returns:
            Its view.
        """
        return cls(
            id=asset.id,
            kind=asset.kind,
            name=asset.name,
            osm_id=asset.osm_id,
            location=asset.location,
            place_code=asset.place_code,
            source_id=asset.source_id,
            version=asset.version,
            created_at=asset.created_at,
            updated_at=asset.updated_at,
        )
