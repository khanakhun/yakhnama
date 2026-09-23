"""Unit tests for the claim, asset and damage command handlers, with fakes only."""

import pytest

from tests.fakes.identity import AllowAllPolicy
from tests.unit.modules.impacts.application.claims_support import (
    CITIZEN,
    MAX_METRIC,
    MODERATOR,
    MODERATOR_ID,
    REASON,
    RETIRED_METRIC,
    SUM_METRIC,
    ImpactsWorld,
    day,
    new_id,
)
from yakhnama.modules.identity.public import Actor
from yakhnama.modules.impacts.application.claims_commands import (
    CorrectImpactClaim,
    RecordDamage,
    RecordImpactClaim,
    RegisterInfrastructureAsset,
    RetractDamage,
    RetractImpactClaim,
)
from yakhnama.modules.impacts.application.claims_handlers import (
    CorrectImpactClaimHandler,
    RecordDamageHandler,
    RecordImpactClaimHandler,
    RegisterInfrastructureAssetHandler,
    RetractDamageHandler,
    RetractImpactClaimHandler,
)
from yakhnama.modules.impacts.domain.errors import (
    AssetNotFoundError,
    ClaimImmutableError,
    ClaimValueKindMismatchError,
    DamageRecordNotFoundError,
    ImpactClaimNotFoundError,
    ImpactMetricNotFoundError,
    ImpactMetricRetiredError,
)
from yakhnama.modules.impacts.domain.events import (
    DamageRecorded,
    DamageRetracted,
    ImpactClaimCorrected,
    ImpactClaimRecorded,
    ImpactClaimRetracted,
    InfrastructureAssetRegistered,
)
from yakhnama.modules.impacts.domain.value_objects import (
    AssetKind,
    ClaimScope,
    ClaimStatus,
    CountValue,
    DamageLevel,
    MeasurementValue,
)
from yakhnama.shared_kernel.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.value_objects import Confidence, Measurement


def record(world: ImpactsWorld, **fields: object) -> RecordImpactClaim:
    return RecordImpactClaim.model_validate(
        {
            "actor": MODERATOR,
            "event_id": world.event_id,
            "metric_code": SUM_METRIC,
            "value": CountValue(count=12),
            "confidence": Confidence.MEDIUM,
            "source_id": world.government,
            "claimed_at": day(10),
            **fields,
        }
    )


async def register_asset(world: ImpactsWorld, osm_id: str | None = None) -> EntityId:
    return await RegisterInfrastructureAssetHandler(world.deps)(
        RegisterInfrastructureAsset(
            actor=MODERATOR,
            kind=AssetKind.BRIDGE,
            name="Test suspension bridge",
            source_id=world.government,
            osm_id=osm_id,
        )
    )


# --------------------------------------------------------------------------- #
# RecordImpactClaim                                                           #
# --------------------------------------------------------------------------- #


async def test_record_claim_stores_claim_with_source_type_and_marks_source() -> None:
    world = ImpactsWorld()

    claim_id = await RecordImpactClaimHandler(world.deps)(record(world))

    claim = world.uow.impact_claims.committed[claim_id]
    assert claim.value == CountValue(count=12)
    assert claim.source_type == "government"
    assert claim.recorded_by == MODERATOR_ID
    assert world.sources.marked == [world.government]
    (recorded,) = world.uow.committed_events
    assert isinstance(recorded, ImpactClaimRecorded)


async def test_record_claim_scoped_to_existing_asset_is_stored() -> None:
    world = ImpactsWorld()
    asset_id = await register_asset(world)

    claim_id = await RecordImpactClaimHandler(world.deps)(
        record(world, scope=ClaimScope(asset_id=asset_id))
    )

    assert world.uow.impact_claims.committed[claim_id].scope.asset_id == asset_id


async def test_record_claim_scoped_to_missing_asset_raises_not_found() -> None:
    world = ImpactsWorld()

    with pytest.raises(AssetNotFoundError):
        await RecordImpactClaimHandler(world.deps)(
            record(world, scope=ClaimScope(asset_id=new_id()))
        )

    assert world.sources.marked == []


async def test_record_claim_citizen_is_denied_and_nothing_happens() -> None:
    world = ImpactsWorld()

    with pytest.raises(PermissionDeniedError):
        await RecordImpactClaimHandler(world.deps)(record(world, actor=CITIZEN))

    assert world.uow.impact_claims.committed == {}
    assert world.sources.marked == []


async def test_record_claim_anonymous_under_permissive_policy_is_denied() -> None:
    world = ImpactsWorld(policy=AllowAllPolicy())

    with pytest.raises(PermissionDeniedError):
        await RecordImpactClaimHandler(world.deps)(
            record(world, actor=Actor.anonymous())
        )


async def test_record_claim_missing_event_raises_not_found() -> None:
    world = ImpactsWorld()

    with pytest.raises(NotFoundError):
        await RecordImpactClaimHandler(world.deps)(record(world, event_id=new_id()))

    assert world.uow.rollback_count == 0


async def test_record_claim_unknown_metric_raises_not_found() -> None:
    world = ImpactsWorld()

    with pytest.raises(ImpactMetricNotFoundError):
        await RecordImpactClaimHandler(world.deps)(
            record(world, metric_code="test_unknown_metric")
        )


async def test_record_claim_retired_metric_is_refused_before_marking_source() -> None:
    world = ImpactsWorld()

    with pytest.raises(ImpactMetricRetiredError):
        await RecordImpactClaimHandler(world.deps)(
            record(world, metric_code=RETIRED_METRIC)
        )

    assert world.sources.marked == []


async def test_record_claim_wrong_value_kind_is_refused_before_marking() -> None:
    world = ImpactsWorld()
    command = RecordImpactClaim(
        actor=MODERATOR,
        event_id=world.event_id,
        metric_code=SUM_METRIC,
        value=MeasurementValue(measurement=Measurement(value=3.0, unit="metre")),
        confidence=Confidence.LOW,
        source_id=world.government,
        claimed_at=day(10),
    )

    with pytest.raises(ClaimValueKindMismatchError):
        await RecordImpactClaimHandler(world.deps)(command)

    assert world.sources.marked == []


async def test_record_claim_unknown_source_raises_and_nothing_committed() -> None:
    world = ImpactsWorld()

    with pytest.raises(NotFoundError):
        await RecordImpactClaimHandler(world.deps)(record(world, source_id=new_id()))

    assert world.uow.commit_count == 0


# --------------------------------------------------------------------------- #
# RetractImpactClaim and CorrectImpactClaim                                   #
# --------------------------------------------------------------------------- #


async def test_retract_claim_keeps_claim_as_retracted() -> None:
    world = ImpactsWorld()
    claim_id = await RecordImpactClaimHandler(world.deps)(record(world))

    await RetractImpactClaimHandler(world.deps)(
        RetractImpactClaim(actor=MODERATOR, claim_id=claim_id, reason=REASON)
    )

    claim = world.uow.impact_claims.committed[claim_id]
    assert claim.status is ClaimStatus.RETRACTED
    assert claim.retraction_reason == REASON
    assert isinstance(world.uow.committed_events[-1], ImpactClaimRetracted)


async def test_retract_claim_twice_raises_immutable() -> None:
    world = ImpactsWorld()
    claim_id = await RecordImpactClaimHandler(world.deps)(record(world))
    handler = RetractImpactClaimHandler(world.deps)
    await handler(RetractImpactClaim(actor=MODERATOR, claim_id=claim_id, reason=REASON))

    with pytest.raises(ClaimImmutableError):
        await handler(
            RetractImpactClaim(actor=MODERATOR, claim_id=claim_id, reason=REASON)
        )


async def test_retract_claim_missing_raises_not_found() -> None:
    world = ImpactsWorld()

    with pytest.raises(ImpactClaimNotFoundError):
        await RetractImpactClaimHandler(world.deps)(
            RetractImpactClaim(actor=MODERATOR, claim_id=new_id(), reason=REASON)
        )


async def test_retract_claim_citizen_is_denied() -> None:
    world = ImpactsWorld()
    claim_id = await RecordImpactClaimHandler(world.deps)(record(world))

    with pytest.raises(PermissionDeniedError):
        await RetractImpactClaimHandler(world.deps)(
            RetractImpactClaim(actor=CITIZEN, claim_id=claim_id, reason=REASON)
        )

    assert world.uow.impact_claims.committed[claim_id].is_active


async def test_correct_claim_supersedes_and_retracts_old_claim() -> None:
    world = ImpactsWorld()
    old_id = await RecordImpactClaimHandler(world.deps)(
        record(world, value=CountValue(count=12))
    )

    new_claim_id = await CorrectImpactClaimHandler(world.deps)(
        CorrectImpactClaim(
            actor=MODERATOR,
            claim_id=old_id,
            value=CountValue(count=15),
            reason=REASON,
            confidence=Confidence.HIGH,
        )
    )

    committed = world.uow.impact_claims.committed
    assert committed[old_id].status is ClaimStatus.RETRACTED
    replacement = committed[new_claim_id]
    assert replacement.supersedes_id == old_id
    assert replacement.value == CountValue(count=15)
    assert replacement.confidence is Confidence.HIGH
    assert replacement.source_id == world.government
    kinds = [type(event) for event in world.uow.committed_events[-2:]]
    assert kinds == [ImpactClaimRetracted, ImpactClaimCorrected]


async def test_correct_claim_missing_raises_not_found() -> None:
    world = ImpactsWorld()

    with pytest.raises(ImpactClaimNotFoundError):
        await CorrectImpactClaimHandler(world.deps)(
            CorrectImpactClaim(
                actor=MODERATOR,
                claim_id=new_id(),
                value=CountValue(count=1),
                reason=REASON,
            )
        )


async def test_correct_claim_citizen_is_denied() -> None:
    world = ImpactsWorld()
    claim_id = await RecordImpactClaimHandler(world.deps)(
        record(world, metric_code=MAX_METRIC)
    )

    with pytest.raises(PermissionDeniedError):
        await CorrectImpactClaimHandler(world.deps)(
            CorrectImpactClaim(
                actor=CITIZEN,
                claim_id=claim_id,
                value=CountValue(count=1),
                reason=REASON,
            )
        )


# --------------------------------------------------------------------------- #
# Assets and damage                                                           #
# --------------------------------------------------------------------------- #


async def test_register_asset_stores_asset_and_marks_source() -> None:
    world = ImpactsWorld()

    asset_id = await register_asset(world, osm_id="way/123456")

    asset = world.uow.infrastructure_assets.committed[asset_id]
    assert asset.osm_id == "way/123456"
    assert world.sources.marked == [world.government]
    (registered,) = world.uow.committed_events
    assert isinstance(registered, InfrastructureAssetRegistered)


async def test_register_asset_same_osm_id_raises_conflict() -> None:
    world = ImpactsWorld()
    await register_asset(world, osm_id="way/123456")

    with pytest.raises(ConflictError):
        await register_asset(world, osm_id="way/123456")

    assert len(world.uow.infrastructure_assets.committed) == 1


async def test_register_asset_unknown_source_raises_not_found() -> None:
    world = ImpactsWorld()

    with pytest.raises(NotFoundError):
        await RegisterInfrastructureAssetHandler(world.deps)(
            RegisterInfrastructureAsset(
                actor=MODERATOR,
                kind=AssetKind.ROAD_SEGMENT,
                name="Test road",
                source_id=new_id(),
            )
        )

    assert world.uow.infrastructure_assets.committed == {}


async def test_register_asset_citizen_is_denied() -> None:
    world = ImpactsWorld()

    with pytest.raises(PermissionDeniedError):
        await RegisterInfrastructureAssetHandler(world.deps)(
            RegisterInfrastructureAsset(
                actor=CITIZEN,
                kind=AssetKind.BRIDGE,
                name="Test bridge",
                source_id=world.government,
            )
        )


def damage(
    world: ImpactsWorld,
    asset_id: EntityId,
    *,
    actor: Actor = MODERATOR,
    event_id: EntityId | None = None,
) -> RecordDamage:
    return RecordDamage(
        actor=actor,
        event_id=event_id or world.event_id,
        asset_id=asset_id,
        level=DamageLevel.WASHED_AWAY,
        confidence=Confidence.HIGH,
        source_id=world.citizen_source,
        recorded_at=day(11),
    )


async def test_record_damage_stores_record_and_marks_source() -> None:
    world = ImpactsWorld()
    asset_id = await register_asset(world)

    damage_id = await RecordDamageHandler(world.deps)(damage(world, asset_id))

    record_ = world.uow.damage_records.committed[damage_id]
    assert record_.level is DamageLevel.WASHED_AWAY
    assert world.sources.marked[-1] == world.citizen_source
    assert isinstance(world.uow.committed_events[-1], DamageRecorded)


async def test_record_damage_missing_asset_raises_not_found() -> None:
    world = ImpactsWorld()

    with pytest.raises(AssetNotFoundError):
        await RecordDamageHandler(world.deps)(damage(world, new_id()))


async def test_record_damage_missing_event_raises_not_found() -> None:
    world = ImpactsWorld()
    asset_id = await register_asset(world)

    with pytest.raises(NotFoundError):
        await RecordDamageHandler(world.deps)(
            damage(world, asset_id, event_id=new_id())
        )


async def test_record_damage_citizen_is_denied() -> None:
    world = ImpactsWorld()
    asset_id = await register_asset(world)

    with pytest.raises(PermissionDeniedError):
        await RecordDamageHandler(world.deps)(damage(world, asset_id, actor=CITIZEN))


async def test_retract_damage_keeps_record_as_retracted() -> None:
    world = ImpactsWorld()
    asset_id = await register_asset(world)
    damage_id = await RecordDamageHandler(world.deps)(damage(world, asset_id))

    await RetractDamageHandler(world.deps)(
        RetractDamage(actor=MODERATOR, damage_id=damage_id, reason=REASON)
    )

    assert world.uow.damage_records.committed[damage_id].is_active is False
    assert isinstance(world.uow.committed_events[-1], DamageRetracted)


async def test_retract_damage_missing_raises_not_found() -> None:
    world = ImpactsWorld()

    with pytest.raises(DamageRecordNotFoundError):
        await RetractDamageHandler(world.deps)(
            RetractDamage(actor=MODERATOR, damage_id=new_id(), reason=REASON)
        )


async def test_retract_damage_citizen_is_denied() -> None:
    world = ImpactsWorld()

    with pytest.raises(PermissionDeniedError):
        await RetractDamageHandler(world.deps)(
            RetractDamage(actor=CITIZEN, damage_id=new_id(), reason=REASON)
        )
