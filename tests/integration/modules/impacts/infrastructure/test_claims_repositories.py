"""The SQLAlchemy claim, asset and damage repositories and claims unit of work.

Runs against real PostGIS: every ``ClaimValue`` variant must reload as itself, the
database must refuse a second correction of one claim and a second asset for one
OpenStreetMap element, and retractions must be checked for concurrent changes.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final

import pytest
from sqlalchemy.exc import IntegrityError

from tests.factories.base import FACTORY_IDS
from tests.factories.impacts import (
    CLAIM_METRIC_CODE,
    DamageRecordTestFactory,
    ImpactClaimTestFactory,
    ImpactMetricTestFactory,
    InfrastructureAssetTestFactory,
)
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.impacts.domain.claims import ImpactClaim
from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.errors import (
    DamageRecordNotFoundError,
    ImpactClaimNotFoundError,
)
from yakhnama.modules.impacts.domain.value_objects import (
    ClaimScope,
    ClaimValue,
    CountValue,
    ImpactMetricRef,
    MeasurementValue,
    MonetaryValue,
    ValueKind,
)
from yakhnama.modules.impacts.infrastructure.claims_uow import (
    SqlAlchemyImpactClaimsUnitOfWork,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import ConflictError
from yakhnama.shared_kernel.value_objects import (
    Coordinates,
    DatePrecision,
    DateWithPrecision,
    Measurement,
)

pytestmark = pytest.mark.integration

type ClaimsFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyImpactClaimsUnitOfWork]

CREATED: Final = datetime(2026, 9, 1, 12, 0, 0, 123456, tzinfo=UTC)
MODERATOR: Final = FACTORY_IDS.new_id()

METRICS: Final[dict[ValueKind, ImpactMetric]] = {
    ValueKind.COUNT: ImpactMetricTestFactory.build(
        code=CLAIM_METRIC_CODE, value_kind=ValueKind.COUNT
    ),
    ValueKind.MEASUREMENT: ImpactMetricTestFactory.build(
        code="test_metric_area", value_kind=ValueKind.MEASUREMENT, unit="square_metre"
    ),
    ValueKind.MONETARY: ImpactMetricTestFactory.build(
        code="test_metric_loss", value_kind=ValueKind.MONETARY
    ),
}
VALUES: Final[dict[ValueKind, ClaimValue]] = {
    ValueKind.COUNT: CountValue(count=17),
    ValueKind.MEASUREMENT: MeasurementValue(
        measurement=Measurement(value=12500.25, unit="square_metre")
    ),
    ValueKind.MONETARY: MonetaryValue(
        amount=Decimal("1234567.89"),
        currency=METRICS[ValueKind.MONETARY].currency or "PKR",
        price_year=2024,
    ),
}


@pytest.fixture
async def metrics(impact_claims_uow_factory: ClaimsFactory) -> None:
    """Store one metric per value kind; claims reference ``impact_metrics.code``."""
    async with impact_claims_uow_factory() as uow:
        for metric in METRICS.values():
            await uow.impact_metrics.add(metric)
        await uow.commit()


def _claim(**fields: object) -> ImpactClaim:
    fields.setdefault("created_at", CREATED)
    return ImpactClaimTestFactory.build(factory_use_construct=False, **fields)


async def _store_claims(factory: ClaimsFactory, *claims: ImpactClaim) -> None:
    async with factory() as uow:
        for claim in claims:
            await uow.impact_claims.add(claim)
        await uow.commit()


async def _get_claim(factory: ClaimsFactory, claim: ImpactClaim) -> object:
    async with factory() as uow:
        return await uow.impact_claims.get(claim.id)


@pytest.mark.usefixtures("metrics")
@pytest.mark.parametrize("kind", list(ValueKind))
async def test_claim_repository_add_then_get_returns_equal_claim_per_value_kind(
    impact_claims_uow_factory: ClaimsFactory, kind: ValueKind
) -> None:
    claim = _claim(
        metric=ImpactMetricRef(code=METRICS[kind].code),
        value=VALUES[kind],
        # After the monetary value's price year, which a claim may not predate.
        claimed_at=DateWithPrecision(
            value=datetime(2025, 3, 4, 5, 6, 7, tzinfo=UTC),
            precision=DatePrecision.EXACT,
        ),
        note="Counted at the relief camp.\nSecond line.",
    )

    await _store_claims(impact_claims_uow_factory, claim)
    loaded = await _get_claim(impact_claims_uow_factory, claim)

    assert loaded == claim
    assert isinstance(loaded, ImpactClaim)
    assert type(loaded.value) is type(VALUES[kind])


@pytest.mark.usefixtures("metrics")
async def test_claim_repository_scope_with_place_and_asset_round_trips(
    impact_claims_uow_factory: ClaimsFactory,
) -> None:
    asset = InfrastructureAssetTestFactory.build(created_at=CREATED)
    claim = _claim(scope=ClaimScope(place_code="test.place-a", asset_id=asset.id))
    async with impact_claims_uow_factory() as uow:
        await uow.infrastructure_assets.add(asset)
        await uow.impact_claims.add(claim)
        await uow.commit()

    assert await _get_claim(impact_claims_uow_factory, claim) == claim


@pytest.mark.usefixtures("metrics")
async def test_claim_repository_add_with_unknown_metric_raises_integrity_error(
    impact_claims_uow_factory: ClaimsFactory,
) -> None:
    claim = _claim(metric=ImpactMetricRef(code="test_metric_unknown"))

    async with impact_claims_uow_factory() as uow:
        with pytest.raises(IntegrityError):
            await uow.impact_claims.add(claim)


@pytest.mark.usefixtures("metrics")
async def test_claim_repository_add_duplicate_id_raises_conflict(
    impact_claims_uow_factory: ClaimsFactory,
) -> None:
    claim = _claim()
    await _store_claims(impact_claims_uow_factory, claim)

    async with impact_claims_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.impact_claims.add(claim)


@pytest.mark.usefixtures("metrics")
async def test_claim_repository_correction_saves_both_claims_in_one_unit_of_work(
    impact_claims_uow_factory: ClaimsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    original = _claim()
    await _store_claims(impact_claims_uow_factory, original)

    async with impact_claims_uow_factory() as uow:
        loaded = await uow.impact_claims.get(original.id)
        assert loaded is not None
        correction = loaded.correct(
            CountValue(count=21),
            metric=METRICS[ValueKind.COUNT],
            reason="Recount by the district office.",
            recorded_by=MODERATOR,
            clock=clock,
            id_generator=ids,
        )
        await uow.impact_claims.save(correction.superseded.state)
        await uow.impact_claims.add(correction.replacement.state)
        await uow.commit()

    assert (
        await _get_claim(impact_claims_uow_factory, original)
        == correction.superseded.state
    )
    assert (
        await _get_claim(impact_claims_uow_factory, correction.replacement.state)
        == correction.replacement.state
    )


@pytest.mark.usefixtures("metrics")
async def test_claim_repository_second_correction_of_one_claim_raises_conflict(
    impact_claims_uow_factory: ClaimsFactory,
) -> None:
    original = _claim()
    first = _claim(supersedes_id=original.id)
    second = _claim(supersedes_id=original.id)
    await _store_claims(impact_claims_uow_factory, original, first)

    async with impact_claims_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.impact_claims.add(second)


@pytest.mark.usefixtures("metrics")
async def test_claim_repository_list_for_event_orders_by_recording_time(
    impact_claims_uow_factory: ClaimsFactory,
) -> None:
    event_id = FACTORY_IDS.new_id()
    later = _claim(event_id=event_id, created_at=CREATED + timedelta(hours=1))
    tied = sorted(
        (_claim(event_id=event_id), _claim(event_id=event_id)),
        key=lambda claim: claim.id,
    )
    other_event = _claim()
    await _store_claims(impact_claims_uow_factory, later, *tied, other_event)

    async with impact_claims_uow_factory() as uow:
        listed = await uow.impact_claims.list_for_event(event_id)

    assert list(listed) == [*tied, later]


@pytest.mark.usefixtures("metrics")
async def test_claim_repository_retract_after_concurrent_retract_raises_conflict(
    impact_claims_uow_factory: ClaimsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    claim = _claim()
    await _store_claims(impact_claims_uow_factory, claim)

    async with (
        impact_claims_uow_factory() as first,
        impact_claims_uow_factory() as second,
    ):
        mine = await first.impact_claims.get(claim.id)
        theirs = await second.impact_claims.get(claim.id)
        assert mine is not None
        assert theirs is not None
        await second.impact_claims.save(
            theirs.retract(
                "Wrong event.", retracted_by=MODERATOR, clock=clock, id_generator=ids
            ).state
        )
        await second.commit()

        with pytest.raises(ConflictError):
            await first.impact_claims.save(
                mine.retract(
                    "Duplicate.", retracted_by=MODERATOR, clock=clock, id_generator=ids
                ).state
            )


@pytest.mark.usefixtures("metrics")
async def test_claim_repository_save_unknown_claim_raises_not_found(
    impact_claims_uow_factory: ClaimsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    retracted = _claim().retract(
        "Never stored.", retracted_by=MODERATOR, clock=clock, id_generator=ids
    )

    async with impact_claims_uow_factory() as uow:
        with pytest.raises(ImpactClaimNotFoundError):
            await uow.impact_claims.save(retracted.state)


@pytest.mark.usefixtures("metrics")
async def test_claim_repository_save_unchanged_version_raises_conflict(
    impact_claims_uow_factory: ClaimsFactory,
) -> None:
    claim = _claim()
    await _store_claims(impact_claims_uow_factory, claim)

    async with impact_claims_uow_factory() as uow:
        loaded = await uow.impact_claims.get(claim.id)
        assert loaded is not None
        with pytest.raises(ConflictError):
            await uow.impact_claims.save(loaded)


# --------------------------------------------------------------------------- #
# Assets                                                                      #
# --------------------------------------------------------------------------- #


async def test_asset_repository_add_then_get_and_get_by_osm_id_return_asset(
    impact_claims_uow_factory: ClaimsFactory,
) -> None:
    asset = InfrastructureAssetTestFactory.build(
        created_at=CREATED,
        osm_id="way/123456",
        location=Coordinates(longitude=74.63651234567891, latitude=36.31234567891234),
        place_code="test.place-a",
    )
    async with impact_claims_uow_factory() as uow:
        await uow.infrastructure_assets.add(asset)
        await uow.commit()

    async with impact_claims_uow_factory() as uow:
        by_id = await uow.infrastructure_assets.get(asset.id)
        by_osm_id = await uow.infrastructure_assets.get_by_osm_id("way/123456")
        missing = await uow.infrastructure_assets.get_by_osm_id("node/1")

    assert by_id == asset
    assert by_osm_id == asset
    assert missing is None


async def test_asset_repository_minimal_asset_round_trips(
    impact_claims_uow_factory: ClaimsFactory,
) -> None:
    asset = InfrastructureAssetTestFactory.build(created_at=CREATED)
    async with impact_claims_uow_factory() as uow:
        await uow.infrastructure_assets.add(asset)
        await uow.commit()

    async with impact_claims_uow_factory() as uow:
        loaded = await uow.infrastructure_assets.get(asset.id)
        unknown = await uow.infrastructure_assets.get(FACTORY_IDS.new_id())

    assert loaded == asset
    assert unknown is None


async def test_asset_repository_second_asset_for_osm_element_raises_conflict(
    impact_claims_uow_factory: ClaimsFactory,
) -> None:
    first = InfrastructureAssetTestFactory.build(
        created_at=CREATED, osm_id="relation/42"
    )
    second = InfrastructureAssetTestFactory.build(
        created_at=CREATED, osm_id="relation/42"
    )
    async with impact_claims_uow_factory() as uow:
        await uow.infrastructure_assets.add(first)
        await uow.commit()

    async with impact_claims_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.infrastructure_assets.add(second)


async def test_asset_repository_assets_without_osm_id_do_not_conflict(
    impact_claims_uow_factory: ClaimsFactory,
) -> None:
    assets = [InfrastructureAssetTestFactory.build(created_at=CREATED) for _ in "ab"]

    async with impact_claims_uow_factory() as uow:
        for asset in assets:
            await uow.infrastructure_assets.add(asset)
        await uow.commit()

    async with impact_claims_uow_factory() as uow:
        loaded = [await uow.infrastructure_assets.get(asset.id) for asset in assets]
    assert loaded == assets


# --------------------------------------------------------------------------- #
# Damage records                                                              #
# --------------------------------------------------------------------------- #


async def test_damage_repository_add_get_and_retract_round_trip(
    impact_claims_uow_factory: ClaimsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    asset = InfrastructureAssetTestFactory.build(created_at=CREATED)
    record = DamageRecordTestFactory.build(
        created_at=CREATED, asset_id=asset.id, note="Deck gone."
    )
    async with impact_claims_uow_factory() as uow:
        await uow.infrastructure_assets.add(asset)
        await uow.damage_records.add(record)
        await uow.commit()

    async with impact_claims_uow_factory() as uow:
        loaded = await uow.damage_records.get(record.id)
        assert loaded == record
        retracted = loaded.retract(
            "Other bridge.", retracted_by=MODERATOR, clock=clock, id_generator=ids
        ).state
        await uow.damage_records.save(retracted)
        await uow.commit()

    async with impact_claims_uow_factory() as uow:
        assert await uow.damage_records.get(record.id) == retracted
        assert await uow.damage_records.get(FACTORY_IDS.new_id()) is None


async def test_damage_repository_add_for_unknown_asset_raises_integrity_error(
    impact_claims_uow_factory: ClaimsFactory,
) -> None:
    record = DamageRecordTestFactory.build(created_at=CREATED)

    async with impact_claims_uow_factory() as uow:
        with pytest.raises(IntegrityError):
            await uow.damage_records.add(record)


async def test_damage_repository_add_duplicate_id_raises_conflict(
    impact_claims_uow_factory: ClaimsFactory,
) -> None:
    asset = InfrastructureAssetTestFactory.build(created_at=CREATED)
    record = DamageRecordTestFactory.build(created_at=CREATED, asset_id=asset.id)
    async with impact_claims_uow_factory() as uow:
        await uow.infrastructure_assets.add(asset)
        await uow.damage_records.add(record)
        await uow.commit()

    async with impact_claims_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.damage_records.add(record)


async def test_damage_repository_save_unknown_record_raises_not_found(
    impact_claims_uow_factory: ClaimsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    retracted = (
        DamageRecordTestFactory.build(created_at=CREATED)
        .retract("Never stored.", retracted_by=MODERATOR, clock=clock, id_generator=ids)
        .state
    )

    async with impact_claims_uow_factory() as uow:
        with pytest.raises(DamageRecordNotFoundError):
            await uow.damage_records.save(retracted)


async def test_damage_repository_save_after_concurrent_change_raises_conflict(
    impact_claims_uow_factory: ClaimsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    asset = InfrastructureAssetTestFactory.build(created_at=CREATED)
    record = DamageRecordTestFactory.build(created_at=CREATED, asset_id=asset.id)
    async with impact_claims_uow_factory() as uow:
        await uow.infrastructure_assets.add(asset)
        await uow.damage_records.add(record)
        await uow.commit()

    async with (
        impact_claims_uow_factory() as first,
        impact_claims_uow_factory() as second,
    ):
        mine = await first.damage_records.get(record.id)
        theirs = await second.damage_records.get(record.id)
        assert mine is not None
        assert theirs is not None
        await second.damage_records.save(
            theirs.retract(
                "Wrong asset.", retracted_by=MODERATOR, clock=clock, id_generator=ids
            ).state
        )
        await second.commit()

        with pytest.raises(ConflictError):
            await first.damage_records.save(
                mine.retract(
                    "Duplicate.", retracted_by=MODERATOR, clock=clock, id_generator=ids
                ).state
            )


async def test_claims_unit_of_work_rollback_discards_writes(
    impact_claims_uow_factory: ClaimsFactory,
) -> None:
    asset = InfrastructureAssetTestFactory.build(created_at=CREATED)

    async with impact_claims_uow_factory() as uow:
        await uow.infrastructure_assets.add(asset)
        await uow.rollback()

    async with impact_claims_uow_factory() as uow:
        assert await uow.infrastructure_assets.get(asset.id) is None
