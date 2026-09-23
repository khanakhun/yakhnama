"""Unit tests for the geography command handlers, with in-memory fakes only."""

import pytest

from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.seed import AllowAllPolicy, DenyAllPolicy, FakeReferenceFileReader
from tests.unit.modules.geography.application.support import (
    NOW,
    country_and_region,
    entry,
    name,
    reference_file,
    retired,
    stored_places,
    unit_of_work,
)
from yakhnama.modules.geography.application.authorisation import AdminOnlyPolicy
from yakhnama.modules.geography.application.commands import (
    LoadReferencePlaces,
    MergePlace,
    RetirePlace,
)
from yakhnama.modules.geography.application.handlers import (
    LoadReferencePlacesHandler,
    MergePlaceHandler,
    RetirePlaceHandler,
)
from yakhnama.modules.geography.application.ports import GeographyUnitOfWorkFactory
from yakhnama.modules.geography.domain.errors import (
    PlaceNotFoundError,
    PlaceRetiredError,
)
from yakhnama.modules.geography.domain.events import (
    PlaceCentroidChanged,
    PlaceCreated,
    PlaceMerged,
    PlaceNameAdded,
    PlacePreferredNameChanged,
    PlaceRetired,
)
from yakhnama.modules.geography.domain.reference import PlaceReferenceFile
from yakhnama.shared_kernel.errors import InvariantViolationError, PermissionDeniedError
from yakhnama.shared_kernel.value_objects import Coordinates

ACTOR_ID = SequentialIdGenerator(seed=99).new_id()
REAL_PLACE_CODES = 15


def load_handler(
    factory: GeographyUnitOfWorkFactory, policy: AdminOnlyPolicy | None = None
) -> LoadReferencePlacesHandler:
    """Build the load handler with deterministic time and ids."""
    return LoadReferencePlacesHandler(
        factory, policy or AllowAllPolicy(), FrozenClock(NOW), SequentialIdGenerator()
    )


def load(file: PlaceReferenceFile, *, dry_run: bool = False) -> LoadReferencePlaces:
    """Return a load command for ``file``."""
    return LoadReferencePlaces(file=file, actor_id=ACTOR_ID, dry_run=dry_run)


# --------------------------------------------------------------------------- #
# LoadReferencePlaces                                                         #
# --------------------------------------------------------------------------- #


async def test_load_reference_places_with_fixture_file_creates_every_code() -> None:
    uow, factory = unit_of_work()
    file = FakeReferenceFileReader().read_places()

    report = await load_handler(factory)(load(file))

    assert len(report.created) == REAL_PLACE_CODES
    assert (report.updated, report.unchanged, report.skipped_with_reason) == (
        (),
        (),
        (),
    )
    assert report.data_version == file.data_version
    assert set(uow.places.committed_by_code()) == {e.code for e in file.entries}
    assert len(uow.committed_events) == REAL_PLACE_CODES
    assert all(isinstance(event, PlaceCreated) for event in uow.committed_events)


async def test_load_reference_places_twice_second_run_is_all_unchanged() -> None:
    uow, factory = unit_of_work()
    command = load(FakeReferenceFileReader().read_places())
    handler = load_handler(factory)
    await handler(command)
    state_after_first = dict(uow.places.committed)
    events_after_first = len(uow.committed_events)

    report = await handler(command)

    assert report.is_unchanged is True
    assert len(report.unchanged) == REAL_PLACE_CODES
    assert uow.places.committed == state_after_first
    assert len(uow.committed_events) == events_after_first


async def test_load_reference_places_creates_children_under_their_parent() -> None:
    uow, factory = unit_of_work()

    await load_handler(factory)(load(country_and_region()))

    by_code = uow.places.committed_by_code()
    assert by_code["xx.a"].parent_id == by_code["xx"].id


async def test_load_reference_places_when_denied_raises_permission_denied() -> None:
    uow, factory = unit_of_work()
    policy = DenyAllPolicy()

    with pytest.raises(PermissionDeniedError):
        await load_handler(factory, policy)(load(country_and_region()))

    assert policy.checked == [ACTOR_ID]
    assert factory.calls == 0
    assert uow.places.committed == {}


async def test_load_reference_places_dry_run_commits_nothing() -> None:
    uow, factory = unit_of_work()

    report = await load_handler(factory)(load(country_and_region(), dry_run=True))

    assert report.created == ("xx", "xx.a")
    assert report.dry_run is True
    assert uow.committed is False
    assert uow.places.committed == {}
    assert uow.committed_events == ()


async def test_load_reference_places_with_new_name_adds_it() -> None:
    uow, factory = unit_of_work(stored_places(country_and_region()))
    file = reference_file(
        entry("xx", "country"),
        entry(
            "xx.a",
            "province_or_region",
            parent_code="xx",
            names=[name("Example xx.a"), name("مثال", language="ur")],
        ),
    )

    report = await load_handler(factory)(load(file))

    assert report.updated == ("xx.a",)
    assert report.unchanged == ("xx",)
    stored = uow.places.committed_by_code()["xx.a"]
    assert [n.text for n in stored.names] == ["Example xx.a", "مثال"]
    assert [e.event_type for e in uow.committed_events] == [
        PlaceNameAdded.event_type,
        PlacePreferredNameChanged.event_type,
    ]


async def test_load_reference_places_with_newly_preferred_name_prefers_it() -> None:
    before = reference_file(
        entry(
            "xx", "country", names=[name("First"), name("Second", is_preferred=False)]
        )
    )
    after = reference_file(
        entry(
            "xx", "country", names=[name("First", is_preferred=False), name("Second")]
        )
    )
    uow, factory = unit_of_work(stored_places(before))

    report = await load_handler(factory)(load(after))

    stored = uow.places.committed_by_code()["xx"]
    assert report.updated == ("xx",)
    assert stored.preferred_name("en") is not None
    assert stored.preferred_name("en") == stored.names[1]
    assert [e.event_type for e in uow.committed_events] == [
        PlacePreferredNameChanged.event_type
    ]


async def test_load_reference_places_with_new_centroid_sets_it() -> None:
    uow, factory = unit_of_work(stored_places(reference_file(entry("xx", "country"))))
    centroid = Coordinates(longitude=74.5, latitude=36.0)
    file = reference_file(entry("xx", "country", centroid=centroid.model_dump()))

    report = await load_handler(factory)(load(file))

    assert report.updated == ("xx",)
    assert uow.places.committed_by_code()["xx"].centroid == centroid
    assert [e.event_type for e in uow.committed_events] == [
        PlaceCentroidChanged.event_type
    ]


async def test_load_reference_places_with_other_stored_centroid_keeps_it() -> None:
    stored_centroid = Coordinates(longitude=74.5, latitude=36.0)
    file_centroid = Coordinates(longitude=75.0, latitude=35.5)
    uow, factory = unit_of_work(
        stored_places(
            reference_file(
                entry("xx", "country", centroid=stored_centroid.model_dump())
            )
        )
    )
    file = reference_file(entry("xx", "country", centroid=file_centroid.model_dump()))

    report = await load_handler(factory)(load(file))

    assert report.unchanged == ("xx",)
    assert [skip.reason for skip in report.skipped_with_reason] == [
        "centroid differs; it is not changed in place"
    ]
    assert uow.places.committed_by_code()["xx"].centroid == stored_centroid
    assert uow.committed_events == ()


async def test_load_reference_places_with_other_level_and_parent_skips_both() -> None:
    stored = reference_file(
        entry("xx", "country"),
        entry("xx.a", "province_or_region", parent_code="xx"),
        entry("xx.b", "province_or_region", parent_code="xx"),
        entry("xx.c", "district", parent_code="xx.a"),
    )
    changed = reference_file(
        entry("xx", "country"),
        entry("xx.a", "province_or_region", parent_code="xx"),
        entry("xx.b", "province_or_region", parent_code="xx"),
        entry("xx.c", "tehsil", parent_code="xx.b"),
    )
    uow, factory = unit_of_work(stored_places(stored))

    report = await load_handler(factory)(load(changed))

    reasons = [skip.reason for skip in report.skipped_with_reason]
    assert report.unchanged == ("xx", "xx.a", "xx.b", "xx.c")
    assert len(reasons) == 2  # level and parent
    assert reasons[0].startswith("level differs")
    assert reasons[1].startswith("parent differs")
    assert uow.committed_events == ()


async def test_load_reference_places_with_other_name_kind_skips_it() -> None:
    uow, factory = unit_of_work(stored_places(reference_file(entry("xx", "country"))))
    file = reference_file(
        entry("xx", "country", names=[name("Example xx", kind="historical")])
    )

    report = await load_handler(factory)(load(file))

    assert report.unchanged == ("xx",)
    (skip,) = report.skipped_with_reason
    assert "kinds are not changed" in skip.reason
    assert uow.places.committed_by_code()["xx"].names[0].kind == "official"


async def test_load_reference_places_on_retired_place_skips_additions() -> None:
    (country,) = stored_places(reference_file(entry("xx", "country")))
    uow, factory = unit_of_work((retired(country),))
    file = reference_file(
        entry("xx", "country", centroid={"longitude": 74.0, "latitude": 36.0})
    )

    report = await load_handler(factory)(load(file))

    assert report.unchanged == ("xx",)
    (skip,) = report.skipped_with_reason
    assert "place is retired" in skip.reason
    assert uow.places.committed_by_code()["xx"].centroid is None
    assert uow.committed_events == ()


async def test_load_reference_places_on_retired_place_without_additions_is_quiet() -> (
    None
):
    (country,) = stored_places(reference_file(entry("xx", "country")))
    _, factory = unit_of_work((retired(country),))

    report = await load_handler(factory)(load(reference_file(entry("xx", "country"))))

    assert report.unchanged == ("xx",)
    assert report.skipped_with_reason == ()


async def test_load_reference_places_new_preferred_flag_on_retired_is_skipped() -> None:
    before = reference_file(
        entry(
            "xx", "country", names=[name("First"), name("Second", is_preferred=False)]
        )
    )
    after = reference_file(
        entry(
            "xx", "country", names=[name("First", is_preferred=False), name("Second")]
        )
    )
    (country,) = stored_places(before)
    _, factory = unit_of_work((retired(country),))

    report = await load_handler(factory)(load(after))

    assert [skip.code for skip in report.skipped_with_reason] == ["xx"]


async def test_load_reference_places_under_stored_retired_parent_raises() -> None:
    (country,) = stored_places(reference_file(entry("xx", "country")))
    uow, factory = unit_of_work((retired(country),))

    with pytest.raises(PlaceRetiredError):
        await load_handler(factory)(load(country_and_region()))

    assert uow.committed is False
    assert set(uow.places.committed_by_code()) == {"xx"}
    assert uow.committed_events == ()


# --------------------------------------------------------------------------- #
# RetirePlace                                                                 #
# --------------------------------------------------------------------------- #


def retire_handler(
    factory: GeographyUnitOfWorkFactory, policy: AdminOnlyPolicy | None = None
) -> RetirePlaceHandler:
    """Build the retire handler with deterministic time and ids."""
    return RetirePlaceHandler(
        factory, policy or AllowAllPolicy(), FrozenClock(NOW), SequentialIdGenerator()
    )


async def test_retire_place_when_active_commits_retired_place_and_event() -> None:
    _, region = stored_places(country_and_region())
    uow, factory = unit_of_work(stored_places(country_and_region()))

    await retire_handler(factory)(
        RetirePlace(place_id=region.id, reason="abolished", actor_id=ACTOR_ID)
    )

    stored = uow.places.committed[region.id]
    assert stored.status == "retired"
    assert stored.status_reason == "abolished"
    assert uow.collected_events == ()
    (event,) = uow.committed_events
    assert isinstance(event, PlaceRetired)
    assert event.occurred_at == NOW


async def test_retire_place_when_denied_raises_before_reading() -> None:
    places = stored_places(country_and_region())
    uow, factory = unit_of_work(places)

    with pytest.raises(PermissionDeniedError):
        await retire_handler(factory, DenyAllPolicy())(
            RetirePlace(place_id=places[1].id, reason="abolished", actor_id=ACTOR_ID)
        )

    assert factory.calls == 0
    assert uow.places.committed[places[1].id].is_active is True


async def test_retire_place_when_missing_raises_not_found() -> None:
    uow, factory = unit_of_work()

    with pytest.raises(PlaceNotFoundError):
        await retire_handler(factory)(
            RetirePlace(place_id=ACTOR_ID, reason="abolished", actor_id=ACTOR_ID)
        )

    assert uow.committed is False


async def test_retire_place_when_already_retired_raises_invalid_transition() -> None:
    (country,) = stored_places(reference_file(entry("xx", "country")))
    uow, factory = unit_of_work((retired(country),))

    with pytest.raises(PlaceRetiredError):
        await retire_handler(factory)(
            RetirePlace(place_id=country.id, reason="again", actor_id=ACTOR_ID)
        )

    assert uow.committed is False
    assert uow.committed_events == ()


# --------------------------------------------------------------------------- #
# MergePlace                                                                  #
# --------------------------------------------------------------------------- #


def merge_handler(
    factory: GeographyUnitOfWorkFactory, policy: AdminOnlyPolicy | None = None
) -> MergePlaceHandler:
    """Build the merge handler with deterministic time and ids."""
    return MergePlaceHandler(
        factory, policy or AllowAllPolicy(), FrozenClock(NOW), SequentialIdGenerator()
    )


def two_regions() -> PlaceReferenceFile:
    """Return a country with regions ``xx.a`` and ``xx.b``."""
    return reference_file(
        entry("xx", "country"),
        entry("xx.a", "province_or_region", parent_code="xx"),
        entry("xx.b", "province_or_region", parent_code="xx"),
    )


async def test_merge_place_into_active_target_commits_merged_place() -> None:
    _, source, target = stored_places(two_regions())
    uow, factory = unit_of_work(stored_places(two_regions()))

    await merge_handler(factory)(
        MergePlace(
            place_id=source.id, target_id=target.id, reason="merged", actor_id=ACTOR_ID
        )
    )

    stored = uow.places.committed[source.id]
    assert stored.status == "merged"
    assert stored.merged_into_id == target.id
    (event,) = uow.committed_events
    assert isinstance(event, PlaceMerged)
    assert event.target_id == target.id


async def test_merge_place_when_denied_raises_permission_denied() -> None:
    _, source, target = stored_places(two_regions())
    uow, factory = unit_of_work(stored_places(two_regions()))

    with pytest.raises(PermissionDeniedError):
        await merge_handler(factory, DenyAllPolicy())(
            MergePlace(
                place_id=source.id,
                target_id=target.id,
                reason="merged",
                actor_id=ACTOR_ID,
            )
        )

    assert factory.calls == 0
    assert uow.places.committed[source.id].is_active is True


async def test_merge_place_with_missing_target_raises_not_found() -> None:
    _, source, _ = stored_places(two_regions())
    uow, factory = unit_of_work(stored_places(two_regions()))

    with pytest.raises(PlaceNotFoundError):
        await merge_handler(factory)(
            MergePlace(
                place_id=source.id,
                target_id=ACTOR_ID,
                reason="merged",
                actor_id=ACTOR_ID,
            )
        )

    assert uow.committed is False


async def test_merge_place_into_retired_target_raises_invalid_transition() -> None:
    country, source, target = stored_places(two_regions())
    uow, factory = unit_of_work((country, source, retired(target)))

    with pytest.raises(PlaceRetiredError):
        await merge_handler(factory)(
            MergePlace(
                place_id=source.id,
                target_id=target.id,
                reason="merged",
                actor_id=ACTOR_ID,
            )
        )

    assert uow.committed is False
    assert uow.places.committed[source.id].is_active is True


async def test_merge_place_into_itself_raises_invariant_violation() -> None:
    _, source, _ = stored_places(two_regions())
    uow, factory = unit_of_work(stored_places(two_regions()))

    with pytest.raises(InvariantViolationError):
        await merge_handler(factory)(
            MergePlace(
                place_id=source.id,
                target_id=source.id,
                reason="merged",
                actor_id=ACTOR_ID,
            )
        )

    assert uow.committed is False
