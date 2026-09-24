"""Unit tests for ``impacts.domain.assets``, ``impacts.domain.damage`` and factories."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from tests.factories.impacts import (
    DamageRecordTestFactory,
    InfrastructureAssetTestFactory,
)
from tests.fakes.clock import SteppingClock
from yakhnama.modules.impacts.domain.assets import InfrastructureAsset
from yakhnama.modules.impacts.domain.damage import DamageRecord
from yakhnama.modules.impacts.domain.errors import ClaimImmutableError
from yakhnama.modules.impacts.domain.events import (
    DamageRecorded,
    DamageRetracted,
    InfrastructureAssetRegistered,
    InfrastructureAssetRelocated,
    InfrastructureAssetRenamed,
)
from yakhnama.modules.impacts.domain.factories import (
    DamageRecordFactory,
    InfrastructureAssetFactory,
)
from yakhnama.modules.impacts.domain.value_objects import (
    AssetKind,
    ClaimStatus,
    DamageLevel,
)
from yakhnama.shared_kernel.ids import Uuid7Generator
from yakhnama.shared_kernel.value_objects import (
    Confidence,
    Coordinates,
    DatePrecision,
    DateWithPrecision,
)

START = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
ONE_MINUTE = timedelta(minutes=1)
IDS = Uuid7Generator()
PASSU = Coordinates(longitude=74.8963, latitude=36.4870)
RECORDED_AT = DateWithPrecision(
    value=datetime(2022, 8, 20, tzinfo=UTC), precision=DatePrecision.DAY
)


def _clock() -> SteppingClock:
    return SteppingClock(START, ONE_MINUTE)


def _asset() -> InfrastructureAsset:
    return InfrastructureAssetTestFactory.build(created_at=START, updated_at=START)


# --------------------------------------------------------------------------- #
# InfrastructureAsset                                                         #
# --------------------------------------------------------------------------- #


def test_infrastructure_asset_factory_register_returns_asset_and_event() -> None:
    source_id = IDS.new_id()
    factory = InfrastructureAssetFactory(clock=_clock(), id_generator=IDS)

    change = factory.register(
        kind=AssetKind.BRIDGE,
        name=" Passu suspension bridge ",
        source_id=source_id,
        osm_id="way/123456",
        location=PASSU,
        place_code="pk.gb.hunza",
    )

    asset, (event,) = change.state, change.events
    assert isinstance(event, InfrastructureAssetRegistered)
    assert (asset.name, asset.version, asset.created_at) == (
        "Passu suspension bridge",
        1,
        START,
    )
    assert (
        event.aggregate_id,
        event.kind,
        event.osm_id,
        event.place_code,
        event.source_id,
    ) == (asset.id, AssetKind.BRIDGE, "way/123456", "pk.gb.hunza", source_id)


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"name": "x" * 201},
        {"osm_id": "way/abc"},
        {"place_code": "Hunza"},
        {"updated_at": START - ONE_MINUTE},
    ],
)
def test_infrastructure_asset_invalid_field_is_rejected(
    overrides: dict[str, object],
) -> None:
    fields = _asset().model_dump()
    fields.update(overrides)

    with pytest.raises(PydanticValidationError):
        InfrastructureAsset.model_validate(fields)


def test_infrastructure_asset_offset_timestamps_are_normalised_to_utc() -> None:
    local = START.astimezone(timezone(timedelta(hours=5)))

    asset = InfrastructureAssetTestFactory.build(created_at=local, updated_at=local)

    assert asset.updated_at.utcoffset() == timedelta(0)


def test_infrastructure_asset_rename_new_name_returns_renamed_asset_and_event() -> None:
    asset = _asset()

    change = asset.rename("Hussaini bridge", clock=_clock(), id_generator=IDS)

    state, (event,) = change.state, change.events
    assert isinstance(event, InfrastructureAssetRenamed)
    assert (state.name, state.version, state.kind, event.aggregate_id) == (
        "Hussaini bridge",
        2,
        asset.kind,
        asset.id,
    )


def test_infrastructure_asset_rename_same_name_is_a_no_op() -> None:
    asset = _asset()

    change = asset.rename(f"  {asset.name} ", clock=_clock(), id_generator=IDS)

    assert (change.state, change.events) == (asset, ())


def test_infrastructure_asset_relocate_new_location_returns_event() -> None:
    asset = _asset()

    change = asset.relocate(
        location=PASSU,
        place_code="pk.gb.hunza",
        osm_id="node/42",
        clock=_clock(),
        id_generator=IDS,
    )

    state, (event,) = change.state, change.events
    assert isinstance(event, InfrastructureAssetRelocated)
    assert (state.location, state.place_code, state.osm_id, state.version) == (
        PASSU,
        "pk.gb.hunza",
        "node/42",
        2,
    )
    assert (event.location, event.place_code, event.osm_id) == (
        PASSU,
        "pk.gb.hunza",
        "node/42",
    )


def test_infrastructure_asset_relocate_same_values_is_a_no_op() -> None:
    asset = _asset()

    change = asset.relocate(
        location=None, place_code=None, osm_id=None, clock=_clock(), id_generator=IDS
    )

    assert (change.state, change.events) == (asset, ())


# --------------------------------------------------------------------------- #
# DamageRecord                                                                #
# --------------------------------------------------------------------------- #


def test_damage_record_factory_record_returns_active_record_and_event() -> None:
    event_id, asset_id, source_id, recorder = (IDS.new_id() for _ in range(4))
    factory = DamageRecordFactory(clock=_clock(), id_generator=IDS)

    change = factory.record(
        event_id=event_id,
        asset_id=asset_id,
        level=DamageLevel.WASHED_AWAY,
        confidence=Confidence.HIGH,
        source_id=source_id,
        recorded_at=RECORDED_AT,
        recorded_by=recorder,
        note="seen from the Karakoram Highway",
    )

    record, (event,) = change.state, change.events
    assert isinstance(event, DamageRecorded)
    assert (record.status, record.version, record.note, record.is_active) == (
        ClaimStatus.ACTIVE,
        1,
        "seen from the Karakoram Highway",
        True,
    )
    assert (
        event.aggregate_id,
        event.hazard_event_id,
        event.asset_id,
        event.level,
        event.confidence,
        event.source_id,
        event.recorded_at,
        event.recorded_by,
    ) == (
        record.id,
        event_id,
        asset_id,
        DamageLevel.WASHED_AWAY,
        Confidence.HIGH,
        source_id,
        RECORDED_AT,
        recorder,
    )


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"status": ClaimStatus.RETRACTED}, "retraction reason"),
        ({"retraction_reason": "wrong"}, "retraction reason"),
        ({"retracted_by": IDS.new_id()}, "retracted_by"),
        ({"updated_at": START - ONE_MINUTE}, "updated_at"),
    ],
)
def test_damage_record_broken_invariant_is_rejected(
    overrides: dict[str, object], fragment: str
) -> None:
    fields = DamageRecordTestFactory.build(created_at=START).model_dump()
    fields.update(overrides)

    with pytest.raises(PydanticValidationError, match=fragment):
        DamageRecord.model_validate(fields)


def test_damage_record_offset_timestamps_are_normalised_to_utc() -> None:
    local = START.astimezone(timezone(timedelta(hours=5)))

    record = DamageRecordTestFactory.build(created_at=local, updated_at=local)

    assert record.created_at.utcoffset() == timedelta(0)


@given(level=st.sampled_from(list(DamageLevel)))
def test_damage_record_retract_active_record_returns_retracted_state_and_event(
    level: DamageLevel,
) -> None:
    record = DamageRecordTestFactory.build(level=level, created_at=START)
    moderator = IDS.new_id()

    change = record.retract(
        "wrong bridge", retracted_by=moderator, clock=_clock(), id_generator=IDS
    )

    state, (event,) = change.state, change.events
    assert isinstance(event, DamageRetracted)
    assert (
        state.status,
        state.retraction_reason,
        state.retracted_by,
        state.level,
        state.version,
    ) == (ClaimStatus.RETRACTED, "wrong bridge", moderator, level, 2)
    assert (
        event.aggregate_id,
        event.hazard_event_id,
        event.asset_id,
        event.retracted_by,
    ) == (record.id, record.event_id, record.asset_id, moderator)


def test_damage_record_retract_retracted_record_raises_claim_immutable() -> None:
    retracted = (
        DamageRecordTestFactory.build(created_at=START)
        .retract("first", retracted_by=IDS.new_id(), clock=_clock(), id_generator=IDS)
        .state
    )

    with pytest.raises(ClaimImmutableError):
        retracted.retract(
            "second", retracted_by=IDS.new_id(), clock=_clock(), id_generator=IDS
        )
