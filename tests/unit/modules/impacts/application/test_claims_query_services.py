"""Unit tests for the impacts read side: best figures, claims and assets."""

import pytest

from tests.unit.modules.impacts.application.claims_support import (
    CITIZEN,
    MAX_METRIC,
    MODERATOR,
    REASON,
    SUM_METRIC,
    ImpactsWorld,
    day,
    new_id,
)
from yakhnama.modules.identity.public import Actor
from yakhnama.modules.impacts.application.claims_commands import (
    RecordImpactClaim,
    RegisterInfrastructureAsset,
    RetractImpactClaim,
)
from yakhnama.modules.impacts.application.claims_handlers import (
    RecordImpactClaimHandler,
    RegisterInfrastructureAssetHandler,
    RetractImpactClaimHandler,
)
from yakhnama.modules.impacts.application.claims_queries import (
    GetEventImpacts,
    GetInfrastructureAsset,
    ListClaims,
)
from yakhnama.modules.impacts.domain.errors import AssetNotFoundError
from yakhnama.modules.impacts.domain.value_objects import (
    AssetKind,
    ClaimScope,
    CountValue,
)
from yakhnama.shared_kernel.errors import NotFoundError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import PageRequest
from yakhnama.shared_kernel.value_objects import Confidence

ANONYMOUS = Actor.anonymous()


async def claim(
    world: ImpactsWorld,
    metric_code: str,
    count: int,
    source_id: EntityId,
    **fields: object,
) -> EntityId:
    return await RecordImpactClaimHandler(world.deps)(
        RecordImpactClaim.model_validate(
            {
                "actor": MODERATOR,
                "event_id": world.event_id,
                "metric_code": metric_code,
                "value": CountValue(count=count),
                "confidence": Confidence.MEDIUM,
                "source_id": source_id,
                "claimed_at": day(10),
                **fields,
            }
        )
    )


async def test_event_impacts_best_figure_per_metric_from_claims() -> None:
    world = ImpactsWorld()
    # Sum: one claim per (source, scope) counts, so two places add up.
    await claim(
        world, SUM_METRIC, 40, world.government, scope=ClaimScope(place_code="test.a")
    )
    await claim(
        world,
        SUM_METRIC,
        25,
        world.citizen_source,
        confidence=Confidence.LOW,
        scope=ClaimScope(place_code="test.b"),
    )
    # Max: the larger value wins.
    await claim(world, MAX_METRIC, 7, world.government)
    await claim(world, MAX_METRIC, 9, world.citizen_source)

    impacts = await world.service.get_event_impacts(
        GetEventImpacts(actor=ANONYMOUS, event_id=world.event_id)
    )

    figures = {figure.metric.code: figure for figure in impacts.best_figures}
    assert list(figures) == sorted([SUM_METRIC, MAX_METRIC])
    assert figures[SUM_METRIC].value == CountValue(count=65)
    assert figures[SUM_METRIC].basis == "sum"
    assert figures[SUM_METRIC].confidence is Confidence.LOW
    assert figures[MAX_METRIC].value == CountValue(count=9)
    assert figures[MAX_METRIC].basis == "max"
    assert len(impacts.claims) == 4
    assert "recorded_by" not in impacts.claims[0].model_dump()


async def test_event_impacts_all_claims_retracted_gives_basis_none() -> None:
    world = ImpactsWorld()
    claim_id = await claim(world, SUM_METRIC, 3, world.government)
    await RetractImpactClaimHandler(world.deps)(
        RetractImpactClaim(actor=MODERATOR, claim_id=claim_id, reason=REASON)
    )

    impacts = await world.service.get_event_impacts(
        GetEventImpacts(actor=MODERATOR, event_id=world.event_id)
    )

    (figure,) = impacts.best_figures
    assert figure.basis == "none"
    assert figure.value is None
    assert impacts.claims[0].status.value == "retracted"


async def test_event_impacts_without_claims_is_empty() -> None:
    world = ImpactsWorld()

    impacts = await world.service.get_event_impacts(
        GetEventImpacts(actor=ANONYMOUS, event_id=world.event_id)
    )

    assert impacts.best_figures == ()
    assert impacts.claims == ()


@pytest.mark.parametrize("actor", [ANONYMOUS, CITIZEN])
async def test_event_impacts_hidden_event_looks_missing_to_public(
    actor: Actor,
) -> None:
    world = ImpactsWorld()
    await claim(world, SUM_METRIC, 3, world.government, event_id=world.hidden_event_id)

    with pytest.raises(NotFoundError):
        await world.service.get_event_impacts(
            GetEventImpacts(actor=actor, event_id=world.hidden_event_id)
        )


async def test_event_impacts_moderator_reads_hidden_event() -> None:
    world = ImpactsWorld()
    await claim(world, SUM_METRIC, 3, world.government, event_id=world.hidden_event_id)

    impacts = await world.service.get_event_impacts(
        GetEventImpacts(actor=MODERATOR, event_id=world.hidden_event_id)
    )

    assert len(impacts.claims) == 1


async def test_event_impacts_moderator_missing_event_raises_not_found() -> None:
    world = ImpactsWorld()

    with pytest.raises(NotFoundError):
        await world.service.get_event_impacts(
            GetEventImpacts(actor=MODERATOR, event_id=new_id())
        )


async def test_list_claims_filters_metric_and_retracted() -> None:
    world = ImpactsWorld()
    kept = await claim(world, SUM_METRIC, 3, world.government)
    retracted = await claim(world, SUM_METRIC, 4, world.citizen_source)
    await claim(world, MAX_METRIC, 5, world.government)
    await RetractImpactClaimHandler(world.deps)(
        RetractImpactClaim(actor=MODERATOR, claim_id=retracted, reason=REASON)
    )

    page = await world.service.list_claims(
        ListClaims(
            actor=ANONYMOUS,
            event_id=world.event_id,
            metric_code=SUM_METRIC,
            include_retracted=False,
        )
    )

    assert [item.id for item in page.items] == [kept]


async def test_list_claims_pages_in_recording_order() -> None:
    world = ImpactsWorld()
    ids = [
        await claim(world, SUM_METRIC, count, world.government) for count in (1, 2, 3)
    ]

    first = await world.service.list_claims(
        ListClaims(actor=ANONYMOUS, event_id=world.event_id, page=PageRequest(limit=2))
    )
    second = await world.service.list_claims(
        ListClaims(
            actor=ANONYMOUS,
            event_id=world.event_id,
            page=PageRequest(limit=2, cursor=first.next_cursor),
        )
    )

    assert [item.id for item in (*first.items, *second.items)] == ids
    assert second.next_cursor is None


async def test_list_claims_hidden_event_looks_missing() -> None:
    world = ImpactsWorld()

    with pytest.raises(NotFoundError):
        await world.service.list_claims(
            ListClaims(actor=ANONYMOUS, event_id=world.hidden_event_id)
        )


async def test_get_asset_returns_registered_asset() -> None:
    world = ImpactsWorld()
    asset_id = await RegisterInfrastructureAssetHandler(world.deps)(
        RegisterInfrastructureAsset(
            actor=MODERATOR,
            kind=AssetKind.WATER_CHANNEL,
            name="Test irrigation channel",
            source_id=world.government,
        )
    )

    asset = await world.service.get_asset(GetInfrastructureAsset(asset_id=asset_id))

    assert asset.id == asset_id
    assert asset.kind is AssetKind.WATER_CHANNEL


async def test_get_asset_missing_raises_not_found() -> None:
    world = ImpactsWorld()

    with pytest.raises(AssetNotFoundError):
        await world.service.get_asset(GetInfrastructureAsset(asset_id=new_id()))
