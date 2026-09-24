"""HTTP routes of impact claims, infrastructure assets and damage, under ``/api/v1``.

``router`` serves the public reads: ``GET /events/{id}/impacts`` (the best figure
of each metric and every claim, active and retracted) follows the event's
visibility, so anyone but a moderator only reads published, verified events, and
``GET /infrastructure-assets/{id}`` is public reference data. The claim views
never name the moderator who recorded or retracted a claim and never show a
retraction reason or a note.

``moderation_router`` serves the append-only writes; every route requires
``CanModerate`` first. Claims are never edited: a correction records a new claim
and retracts the old one.

Errors are never mapped here: handlers and query services raise ``YakhnamaError``
subclasses and the exception handlers in ``main.py`` render Problem Details.

Patterns: none from the catalog (thin transport layer over commands and queries).
"""

from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, Header, Response, status

from yakhnama.modules.impacts.api.claims_dependencies import (
    ModeratorActor,
    OptionalActor,
    Services,
)
from yakhnama.modules.impacts.api.claims_schemas import (
    CorrectImpactClaimRequest,
    CreatedRecord,
    RecordDamageRequest,
    RecordImpactClaimRequest,
    RegisterInfrastructureAssetRequest,
    RetractImpactClaimRequest,
)
from yakhnama.modules.impacts.public import (
    CorrectImpactClaim,
    CorrectImpactClaimHandler,
    EventImpacts,
    GetEventImpacts,
    GetInfrastructureAsset,
    ImpactClaimSummary,
    InfrastructureAssetDetail,
    RecordDamage,
    RecordDamageHandler,
    RecordImpactClaim,
    RecordImpactClaimHandler,
    RegisterInfrastructureAsset,
    RegisterInfrastructureAssetHandler,
    RetractImpactClaim,
    RetractImpactClaimHandler,
)
from yakhnama.platform.etag import make_etag, set_etag
from yakhnama.shared_kernel.errors import NotFoundError
from yakhnama.shared_kernel.ids import EntityId

API_PREFIX: Final = "/api/v1"
ASSETS_PATH: Final = f"{API_PREFIX}/infrastructure-assets"
LOCATION_HEADER: Final = "Location"
CLAIM_NOT_VISIBLE_MESSAGE: Final = "The recorded claim could not be read back."

# Any: the value type of FastAPI's own ``responses`` argument.
_PROBLEM: Final[dict[str, Any]] = {
    "description": "RFC 9457 Problem Details",
    "content": {"application/problem+json": {}},
}
_COMMON_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_401_UNAUTHORIZED: _PROBLEM,
    status.HTTP_403_FORBIDDEN: _PROBLEM,
    status.HTTP_422_UNPROCESSABLE_CONTENT: _PROBLEM,
    status.HTTP_429_TOO_MANY_REQUESTS: _PROBLEM,
    status.HTTP_503_SERVICE_UNAVAILABLE: _PROBLEM,
}
_CREATE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_400_BAD_REQUEST: _PROBLEM,
    status.HTTP_404_NOT_FOUND: _PROBLEM,
    status.HTTP_409_CONFLICT: _PROBLEM,
}

router = APIRouter(prefix=API_PREFIX, tags=["impacts"], responses=_COMMON_RESPONSES)
moderation_router = APIRouter(
    prefix=f"{API_PREFIX}/moderation",
    tags=["moderation"],
    responses=_COMMON_RESPONSES,
)

IdempotencyKey = Annotated[
    UUID | None,
    Header(
        alias="Idempotency-Key",
        description=(
            "A UUID chosen by the client; replaying it with the same request "
            "returns the stored response."
        ),
    ),
]


@router.get(
    "/events/{event_id}/impacts", responses={status.HTTP_404_NOT_FOUND: _PROBLEM}
)
async def get_event_impacts(
    event_id: EntityId, actor: OptionalActor, services: Services
) -> EventImpacts:
    """Return an event's best figure per metric and every claim behind them.

    Args:
        event_id: The event.
        actor: The caller, anonymous or authenticated.
        services: Use cases bound by the composition root.

    Returns:
        The best figures and the claims, in recording order.

    Raises:
        NotFoundError: If the event does not exist or the caller may not see it.
    """
    return await services.event_impacts_queries.get_event_impacts(
        GetEventImpacts(actor=actor, event_id=event_id)
    )


@router.get(
    "/infrastructure-assets/{asset_id}",
    responses={status.HTTP_404_NOT_FOUND: _PROBLEM},
)
async def get_infrastructure_asset(
    asset_id: EntityId, response: Response, services: Services
) -> InfrastructureAssetDetail:
    """Return one infrastructure asset, public reference data, with its ``ETag``.

    Args:
        asset_id: The asset.
        response: Used to set the ``ETag`` header.
        services: Use cases bound by the composition root.

    Returns:
        The asset.
    """
    detail = await services.event_impacts_queries.get_asset(
        GetInfrastructureAsset(asset_id=asset_id)
    )
    set_etag(response, make_etag(detail.version, detail.id))
    return detail


@moderation_router.post(
    "/events/{event_id}/impact-claims",
    status_code=status.HTTP_201_CREATED,
    responses=_CREATE_RESPONSES,
)
async def record_impact_claim(
    event_id: EntityId,
    body: RecordImpactClaimRequest,
    actor: ModeratorActor,
    services: Services,
    idempotency_key: IdempotencyKey = None,
) -> ImpactClaimSummary:
    """Record one metric value for an event from one source.

    Args:
        event_id: The event.
        body: Metric, value, confidence, source, time, scope and note.
        actor: The moderator.
        services: Use cases bound by the composition root.
        idempotency_key: Documented here; the idempotency middleware acts on it.

    Returns:
        The new claim as the open dataset shows it.

    Raises:
        NotFoundError: If the claim cannot be read back (a wiring bug).
    """
    del idempotency_key
    claim_id = await RecordImpactClaimHandler(
        services.impact_claim_handler_dependencies
    )(
        RecordImpactClaim(
            actor=actor,
            event_id=event_id,
            metric_code=body.metric_code,
            value=body.value,
            confidence=body.confidence,
            source_id=body.source_id,
            claimed_at=body.claimed_at,
            scope=body.scope,
            note=body.note,
        )
    )
    impacts = await services.event_impacts_queries.get_event_impacts(
        GetEventImpacts(actor=actor, event_id=event_id)
    )
    for claim in impacts.claims:
        if claim.id == claim_id:
            return claim
    raise NotFoundError(CLAIM_NOT_VISIBLE_MESSAGE)


@moderation_router.post(
    "/impact-claims/{claim_id}/retraction",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_404_NOT_FOUND: _PROBLEM,
        status.HTTP_409_CONFLICT: _PROBLEM,
    },
)
async def retract_impact_claim(
    claim_id: EntityId,
    body: RetractImpactClaimRequest,
    actor: ModeratorActor,
    services: Services,
) -> None:
    """Retract a claim; it stays stored and stops counting towards best figures.

    Args:
        claim_id: The claim.
        body: Why.
        actor: The moderator.
        services: Use cases bound by the composition root.
    """
    await RetractImpactClaimHandler(services.impact_claim_handler_dependencies)(
        RetractImpactClaim(actor=actor, claim_id=claim_id, reason=body.reason)
    )


@moderation_router.post(
    "/impact-claims/{claim_id}/correction",
    status_code=status.HTTP_201_CREATED,
    responses=_CREATE_RESPONSES,
)
async def correct_impact_claim(
    claim_id: EntityId,
    body: CorrectImpactClaimRequest,
    actor: ModeratorActor,
    services: Services,
    idempotency_key: IdempotencyKey = None,
) -> CreatedRecord:
    """Record a corrected claim from the same source and retract the old one.

    Args:
        claim_id: The claim being corrected.
        body: The corrected value and the reason.
        actor: The moderator.
        services: Use cases bound by the composition root.
        idempotency_key: Documented here; the idempotency middleware acts on it.

    Returns:
        The id of the new claim.
    """
    del idempotency_key
    new_id = await CorrectImpactClaimHandler(
        services.impact_claim_handler_dependencies
    )(
        CorrectImpactClaim(
            actor=actor,
            claim_id=claim_id,
            value=body.value,
            reason=body.reason,
            confidence=body.confidence,
            claimed_at=body.claimed_at,
            note=body.note,
        )
    )
    return CreatedRecord(id=new_id)


@moderation_router.post(
    "/infrastructure-assets",
    status_code=status.HTTP_201_CREATED,
    responses=_CREATE_RESPONSES,
)
async def register_infrastructure_asset(
    body: RegisterInfrastructureAssetRequest,
    actor: ModeratorActor,
    response: Response,
    services: Services,
    idempotency_key: IdempotencyKey = None,
) -> InfrastructureAssetDetail:
    """Register a bridge, road segment or other asset hazards can damage.

    Args:
        body: Kind, name, source and the optional OSM id, point and place.
        actor: The moderator.
        response: Used to set ``Location`` and ``ETag``.
        services: Use cases bound by the composition root.
        idempotency_key: Documented here; the idempotency middleware acts on it.

    Returns:
        The registered asset.
    """
    del idempotency_key
    asset_id = await RegisterInfrastructureAssetHandler(
        services.impact_claim_handler_dependencies
    )(
        RegisterInfrastructureAsset(
            actor=actor,
            kind=body.kind,
            name=body.name,
            source_id=body.source_id,
            osm_id=body.osm_id,
            location=body.location,
            place_code=body.place_code,
        )
    )
    detail = await services.event_impacts_queries.get_asset(
        GetInfrastructureAsset(asset_id=asset_id)
    )
    response.headers[LOCATION_HEADER] = f"{ASSETS_PATH}/{asset_id}"
    set_etag(response, make_etag(detail.version, detail.id))
    return detail


@moderation_router.post(
    "/events/{event_id}/damage-records",
    status_code=status.HTTP_201_CREATED,
    responses=_CREATE_RESPONSES,
)
async def record_damage(
    event_id: EntityId,
    body: RecordDamageRequest,
    actor: ModeratorActor,
    services: Services,
    idempotency_key: IdempotencyKey = None,
) -> CreatedRecord:
    """Record one source's statement that an asset was damaged during an event.

    Args:
        event_id: The event.
        body: Asset, level, confidence, source, time and note.
        actor: The moderator.
        services: Use cases bound by the composition root.
        idempotency_key: Documented here; the idempotency middleware acts on it.

    Returns:
        The id of the damage record.
    """
    del idempotency_key
    damage_id = await RecordDamageHandler(services.impact_claim_handler_dependencies)(
        RecordDamage(
            actor=actor,
            event_id=event_id,
            asset_id=body.asset_id,
            level=body.level,
            confidence=body.confidence,
            source_id=body.source_id,
            recorded_at=body.recorded_at,
            note=body.note,
        )
    )
    return CreatedRecord(id=damage_id)
