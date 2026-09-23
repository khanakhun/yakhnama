"""Unit tests for ``yakhnama.modules.geography.domain.entities``."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from tests.fakes.clock import SteppingClock
from yakhnama.modules.geography.domain.entities import PLACE_MAX_NAMES, Place
from yakhnama.modules.geography.domain.errors import (
    DuplicatePlaceNameError,
    PlaceNameNotFoundError,
    PlaceRetiredError,
)
from yakhnama.modules.geography.domain.events import (
    PlaceCentroidChanged,
    PlaceGeometryChanged,
    PlaceMerged,
    PlaceNameAdded,
    PlacePreferredNameChanged,
    PlaceRetired,
)
from yakhnama.modules.geography.domain.value_objects import (
    PLACE_VERSION_MAX,
    AdminLevel,
    PlaceGeometry,
    PlaceName,
    ScriptCode,
)
from yakhnama.shared_kernel.errors import InvariantViolationError, ValidationError
from yakhnama.shared_kernel.ids import Uuid7Generator
from yakhnama.shared_kernel.value_objects import BoundingBox, Coordinates

CREATED_AT = datetime(2026, 9, 1, tzinfo=UTC)
ids = Uuid7Generator()
PARENT_ID = ids.new_id()
GB_BOX = BoundingBox(
    min_longitude=72.0, min_latitude=34.5, max_longitude=77.9, max_latitude=37.1
)
HUNZA_CENTROID = Coordinates(longitude=74.65, latitude=36.32)
EN_HUNZA = PlaceName(text="Hunza", language="en", is_preferred=True)


def _stepping_clock() -> SteppingClock:
    # One second per call from a day after CREATED_AT, so every change is later.
    return SteppingClock(CREATED_AT + timedelta(days=1), timedelta(seconds=1))


def _place(**overrides: object) -> Place:
    fields: dict[str, object] = {
        "id": ids.new_id(),
        "code": "pk.gb.hunza",
        "level": AdminLevel.DISTRICT,
        "parent_id": PARENT_ID,
        "names": (EN_HUNZA,),
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
    }
    fields.update(overrides)
    return Place.model_validate(fields)


# --------------------------------------------------------------------------- #
# Construction invariants                                                     #
# --------------------------------------------------------------------------- #


def test_place_minimal_fields_builds_active_place_at_version_one() -> None:
    place = _place()

    assert (place.status, place.version, place.is_active) == ("active", 1, True)
    assert (place.geometry, place.centroid, place.merged_into_id) == (None, None, None)


def test_place_timestamps_are_normalised_to_utc() -> None:
    pakistan = timezone(timedelta(hours=5))
    local = datetime(2026, 9, 1, 5, 0, tzinfo=pakistan)

    place = _place(created_at=local, updated_at=local)

    assert place.created_at.tzinfo is UTC
    assert place.created_at == CREATED_AT


def test_place_naive_timestamp_raises_validation_error() -> None:
    naive = datetime(2026, 9, 1)  # noqa: DTZ001  # reason: asserting that naive datetimes are rejected

    with pytest.raises(PydanticValidationError):
        _place(created_at=naive, updated_at=naive)


def test_place_updated_before_created_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError, match="updated_at"):
        _place(updated_at=CREATED_AT - timedelta(seconds=1))


def test_place_without_names_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        _place(names=())


def test_place_more_names_than_the_bound_raises_validation_error() -> None:
    names = tuple(
        PlaceName(text=f"Name {i}", language="en") for i in range(PLACE_MAX_NAMES + 1)
    )

    with pytest.raises(PydanticValidationError):
        _place(names=names)


def test_place_duplicate_name_raises_validation_error() -> None:
    names = (EN_HUNZA, EN_HUNZA.with_preference(is_preferred=False))

    with pytest.raises(PydanticValidationError, match="duplicate"):
        _place(names=names)


def test_place_two_preferred_names_in_one_language_raises_validation_error() -> None:
    names = (EN_HUNZA, PlaceName(text="Kanjut", language="en", is_preferred=True))

    with pytest.raises(PydanticValidationError, match="preferred"):
        _place(names=names)


def test_place_same_text_in_other_script_is_not_a_duplicate() -> None:
    names = (EN_HUNZA, PlaceName(text="Hunza", language="en", script=ScriptCode.LATN))

    place = _place(names=names)

    assert len(place.names) == 2


def test_place_preferred_names_in_different_languages_are_accepted() -> None:
    urdu = PlaceName(
        text="ہنزہ", language="ur", script=ScriptCode.ARAB, is_preferred=True
    )

    place = _place(names=(EN_HUNZA, urdu))

    assert [name.is_preferred for name in place.names] == [True, True]


NAME_TEXTS = st.sampled_from(["Hunza", "Nagar", "Gilgit"])
NAME_LANGUAGES = st.sampled_from(["en", "ur", "bsk"])
NAMES = st.builds(
    PlaceName,
    text=NAME_TEXTS,
    language=NAME_LANGUAGES,
    script=st.none() | st.just(ScriptCode.ARAB),
    is_preferred=st.booleans(),
)


@given(names=st.lists(NAMES, min_size=1, max_size=8))
def test_place_names_are_accepted_exactly_when_invariants_hold(
    names: list[PlaceName],
) -> None:
    keys = [name.key for name in names]
    preferred = [name.language for name in names if name.is_preferred]
    is_valid = len(set(keys)) == len(keys) and len(set(preferred)) == len(preferred)

    try:
        _place(names=tuple(names))
        accepted = True
    except PydanticValidationError:
        accepted = False

    assert accepted == is_valid


def test_place_country_with_parent_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError, match="parent_id"):
        _place(level=AdminLevel.COUNTRY, parent_id=PARENT_ID)


def test_place_country_without_parent_is_accepted() -> None:
    place = _place(code="pk", level=AdminLevel.COUNTRY, parent_id=None)

    assert place.parent_id is None


def test_place_non_country_without_parent_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError, match="parent_id"):
        _place(parent_id=None)


def test_place_own_parent_raises_validation_error() -> None:
    place_id = ids.new_id()

    with pytest.raises(PydanticValidationError, match="own parent"):
        _place(id=place_id, parent_id=place_id)


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "merged", "status_reason": "boundary change"},
        {"status": "active", "merged_into_id": PARENT_ID},
        {"status": "retired"},
        {"status": "active", "status_reason": "no reason needed"},
    ],
    ids=[
        "merged-without-target",
        "active-with-target",
        "retired-without-reason",
        "active-with-reason",
    ],
)
def test_place_inconsistent_status_fields_raise_validation_error(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(PydanticValidationError, match="status"):
        _place(**overrides)


def test_place_version_out_of_bounds_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        _place(version=0)


def test_place_non_uuid7_id_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        _place(id=UUID(int=1))


def test_place_assignment_raises_validation_error() -> None:
    place = _place()

    with pytest.raises(PydanticValidationError):
        place.code = "pk.gb.nagar"  # type: ignore[misc]  # reason: proves the model is frozen


def test_place_round_trip_through_json_returns_equal_place() -> None:
    place = _place(
        geometry=PlaceGeometry.from_bounding_box(GB_BOX), centroid=HUNZA_CENTROID
    )

    restored = Place.model_validate_json(place.model_dump_json())

    assert restored == place


# --------------------------------------------------------------------------- #
# Queries                                                                     #
# --------------------------------------------------------------------------- #


def test_place_names_in_normalises_the_language_code() -> None:
    urdu = PlaceName(text="ہنزہ", language="ur-Arab")
    place = _place(names=(EN_HUNZA, urdu))

    result = place.names_in("UR-arab")

    assert result == (urdu,)


def test_place_names_in_invalid_language_raises_validation_error() -> None:
    place = _place()

    with pytest.raises(PydanticValidationError):
        place.names_in("not a language")


def test_place_preferred_name_returns_preferred_over_earlier_names() -> None:
    first = PlaceName(text="Kanjut", language="en", kind="historical")
    place = _place(names=(first, EN_HUNZA))

    result = place.preferred_name("en")

    assert result == EN_HUNZA


def test_place_preferred_name_without_preferred_returns_first_recorded() -> None:
    first = PlaceName(text="Hunza", language="ur")
    second = PlaceName(text="Hunzā", language="ur")
    place = _place(names=(EN_HUNZA, first, second))

    result = place.preferred_name("ur")

    assert result == first


def test_place_preferred_name_missing_language_uses_fallback_order() -> None:
    place = _place()

    result = place.preferred_name("bsk", fallback_order=["shi", "en"])

    assert result == EN_HUNZA


def test_place_preferred_name_no_requested_language_returns_none() -> None:
    place = _place()

    result = place.preferred_name("bsk", fallback_order=["shi"])

    assert result is None


def test_place_is_within_uses_centroid_when_present() -> None:
    far_polygon = PlaceGeometry.from_bounding_box(
        BoundingBox(min_longitude=0, min_latitude=0, max_longitude=1, max_latitude=1)
    )
    place = _place(centroid=HUNZA_CENTROID, geometry=far_polygon)

    result = place.is_within(GB_BOX)

    assert result is True


def test_place_is_within_centroid_outside_box_returns_false() -> None:
    place = _place(centroid=Coordinates(longitude=10.0, latitude=10.0))

    result = place.is_within(GB_BOX)

    assert result is False


SMALL_BOX = BoundingBox(
    min_longitude=74.2, min_latitude=36.1, max_longitude=74.8, max_latitude=36.8
)


@pytest.mark.parametrize(
    ("west", "south", "east", "north", "expected"),
    [
        (74.0, 36.0, 75.0, 37.0, True),
        (74.2, 36.1, 74.8, 36.8, True),
        (74.0, 36.0, 74.5, 37.0, False),
        (74.0, 36.0, 75.0, 36.5, False),
    ],
    ids=["inside", "edges-touch", "sticks-out-east", "sticks-out-north"],
)
def test_place_is_within_without_centroid_requires_whole_geometry_box(
    west: float, south: float, east: float, north: float, *, expected: bool
) -> None:
    box = BoundingBox(
        min_longitude=west, min_latitude=south, max_longitude=east, max_latitude=north
    )
    place = _place(geometry=PlaceGeometry.from_bounding_box(SMALL_BOX))

    result = place.is_within(box)

    assert result is expected


def test_place_is_within_without_location_returns_false() -> None:
    place = _place()

    result = place.is_within(GB_BOX)

    assert result is False


@pytest.mark.parametrize(
    ("other", "expected"),
    [
        (AdminLevel.DIVISION, True),
        (AdminLevel.DISTRICT, False),
        (AdminLevel.TEHSIL, False),
    ],
)
def test_place_level_is_below_compares_with_a_level(
    other: AdminLevel, *, expected: bool
) -> None:
    place = _place()

    result = place.level_is_below(other)

    assert result is expected


def test_place_level_is_below_compares_with_another_place() -> None:
    district = _place()
    village = _place(code="pk.gb.hunza.karimabad", level=AdminLevel.VILLAGE)

    result = (village.level_is_below(district), district.level_is_below(village))

    assert result == (True, False)


# --------------------------------------------------------------------------- #
# add_name                                                                    #
# --------------------------------------------------------------------------- #


def test_place_add_name_appends_name_and_bumps_version() -> None:
    place = _place()
    clock = _stepping_clock()
    name = PlaceName(text="Kanjut", language="en", kind="historical")

    change = place.add_name(name, clock=clock, ids=ids)

    assert change.state.names == (EN_HUNZA, name)
    assert change.state.version == 2
    assert change.state.updated_at == CREATED_AT + timedelta(days=1)
    assert place.names == (EN_HUNZA,)
    assert [type(event) for event in change.events] == [PlaceNameAdded]


def test_place_add_name_event_identifies_the_place_and_new_version() -> None:
    place = _place()
    name = PlaceName(text="Kanjut", language="en")

    event = place.add_name(name, clock=_stepping_clock(), ids=ids).events[0]

    assert isinstance(event, PlaceNameAdded)
    assert (event.aggregate_id, event.aggregate_type) == (place.id, "place")
    assert (event.place_code, event.version, event.name) == (place.code, 2, name)
    assert event.occurred_at == CREATED_AT + timedelta(days=1)
    assert event.event_type == "geography.place_name_added"


def test_place_add_name_preferred_demotes_previous_preferred() -> None:
    place = _place()
    name = PlaceName(text="Hunza Valley", language="en", is_preferred=True)

    change = place.add_name(name, clock=_stepping_clock(), ids=ids)

    assert change.state.names == (EN_HUNZA.with_preference(is_preferred=False), name)
    changed = change.events[1]
    assert isinstance(changed, PlacePreferredNameChanged)
    assert (changed.previous_text, changed.text, changed.language) == (
        "Hunza",
        "Hunza Valley",
        "en",
    )


def test_place_add_name_first_preferred_in_language_reports_no_previous() -> None:
    place = _place()
    name = PlaceName(
        text="ہنزہ", language="ur", script=ScriptCode.ARAB, is_preferred=True
    )

    change = place.add_name(name, clock=_stepping_clock(), ids=ids)

    changed = change.events[1]
    assert isinstance(changed, PlacePreferredNameChanged)
    assert (changed.previous_text, changed.script) == (None, ScriptCode.ARAB)
    assert change.events[0].event_id != changed.event_id


def test_place_add_name_duplicate_raises_duplicate_place_name_error() -> None:
    place = _place()
    duplicate = PlaceName(text=" Hunza ", language="en", kind="alternative")

    with pytest.raises(DuplicatePlaceNameError) as raised:
        place.add_name(duplicate, clock=_stepping_clock(), ids=ids)

    assert raised.value.details["code"] == "pk.gb.hunza"


def test_place_add_name_beyond_the_bound_raises_validation_error() -> None:
    names = tuple(
        PlaceName(text=f"Name {i}", language="en") for i in range(PLACE_MAX_NAMES)
    )
    place = _place(names=names)

    with pytest.raises(PydanticValidationError):
        place.add_name(
            PlaceName(text="One more", language="en"), clock=_stepping_clock(), ids=ids
        )


def test_place_add_name_at_max_version_raises_validation_error() -> None:
    place = _place(version=PLACE_VERSION_MAX)

    with pytest.raises(PydanticValidationError):
        place.add_name(
            PlaceName(text="Kanjut", language="en"), clock=_stepping_clock(), ids=ids
        )


@given(names=st.lists(NAMES, max_size=10))
def test_place_add_name_sequence_keeps_name_invariants(
    names: list[PlaceName],
) -> None:
    place = _place()
    clock = _stepping_clock()

    for name in names:
        try:
            place = place.add_name(name, clock=clock, ids=ids).state
        except DuplicatePlaceNameError:
            continue

    keys = [name.key for name in place.names]
    preferred = [name.language for name in place.names if name.is_preferred]
    assert len(set(keys)) == len(keys)
    assert len(set(preferred)) == len(preferred)


# --------------------------------------------------------------------------- #
# set_preferred_name                                                          #
# --------------------------------------------------------------------------- #


def test_place_set_preferred_name_switches_preference_within_language() -> None:
    other = PlaceName(text="Kanjut", language="en")
    place = _place(names=(EN_HUNZA, other))

    change = place.set_preferred_name(
        "EN", " Kanjut ", clock=_stepping_clock(), ids=ids
    )

    assert change.state.names == (
        EN_HUNZA.with_preference(is_preferred=False),
        other.with_preference(is_preferred=True),
    )
    assert change.state.version == 2
    event = change.events[0]
    assert isinstance(event, PlacePreferredNameChanged)
    assert (event.previous_text, event.text) == ("Hunza", "Kanjut")


def test_place_set_preferred_name_without_previous_reports_none() -> None:
    urdu = PlaceName(text="ہنزہ", language="ur")
    place = _place(names=(EN_HUNZA, urdu))

    change = place.set_preferred_name("ur", "ہنزہ", clock=_stepping_clock(), ids=ids)

    event = change.events[0]
    assert isinstance(event, PlacePreferredNameChanged)
    assert event.previous_text is None
    assert change.state.names[1].is_preferred is True


def test_place_set_preferred_name_already_preferred_returns_unchanged_place() -> None:
    place = _place()

    change = place.set_preferred_name("en", "Hunza", clock=_stepping_clock(), ids=ids)

    assert change.state is place
    assert change.events == ()


def test_place_set_preferred_name_unknown_name_raises_not_found() -> None:
    place = _place()

    with pytest.raises(PlaceNameNotFoundError):
        place.set_preferred_name("en", "Nagar", clock=_stepping_clock(), ids=ids)


def test_place_set_preferred_name_ambiguous_script_raises_validation_error() -> None:
    names = (
        PlaceName(text="Hunza", language="en"),
        PlaceName(text="Hunza", language="en", script=ScriptCode.LATN),
    )
    place = _place(names=names)

    with pytest.raises(ValidationError, match="script"):
        place.set_preferred_name("en", "Hunza", clock=_stepping_clock(), ids=ids)


def test_place_set_preferred_name_script_chooses_between_scripts() -> None:
    names = (
        PlaceName(text="Hunza", language="en"),
        PlaceName(text="Hunza", language="en", script=ScriptCode.LATN),
    )
    place = _place(names=names)

    change = place.set_preferred_name(
        "en", "Hunza", clock=_stepping_clock(), ids=ids, script=ScriptCode.LATN
    )

    assert [name.is_preferred for name in change.state.names] == [False, True]


# --------------------------------------------------------------------------- #
# set_geometry and set_centroid                                               #
# --------------------------------------------------------------------------- #


def test_place_set_geometry_sets_geometry_and_reports_bounding_box() -> None:
    place = _place()
    geometry = PlaceGeometry.from_bounding_box(GB_BOX)

    change = place.set_geometry(geometry, clock=_stepping_clock(), ids=ids)

    assert change.state.geometry == geometry
    event = change.events[0]
    assert isinstance(event, PlaceGeometryChanged)
    assert (event.previous_geometry_type, event.geometry_type, event.bounding_box) == (
        None,
        "Polygon",
        GB_BOX,
    )


def test_place_set_geometry_none_removes_geometry() -> None:
    place = _place(geometry=PlaceGeometry.from_coordinates(HUNZA_CENTROID))

    change = place.set_geometry(None, clock=_stepping_clock(), ids=ids)

    assert change.state.geometry is None
    event = change.events[0]
    assert isinstance(event, PlaceGeometryChanged)
    assert (event.previous_geometry_type, event.geometry_type, event.bounding_box) == (
        "Point",
        None,
        None,
    )


def test_place_set_geometry_same_geometry_returns_unchanged_place() -> None:
    place = _place(geometry=PlaceGeometry.from_bounding_box(GB_BOX))

    change = place.set_geometry(
        PlaceGeometry.from_bounding_box(GB_BOX), clock=_stepping_clock(), ids=ids
    )

    assert (change.state, change.events) == (place, ())


def test_place_set_centroid_sets_and_reports_previous() -> None:
    place = _place()

    change = place.set_centroid(HUNZA_CENTROID, clock=_stepping_clock(), ids=ids)

    assert change.state.centroid == HUNZA_CENTROID
    event = change.events[0]
    assert isinstance(event, PlaceCentroidChanged)
    assert (event.previous_centroid, event.centroid) == (None, HUNZA_CENTROID)


def test_place_set_centroid_same_centroid_returns_unchanged_place() -> None:
    place = _place(centroid=HUNZA_CENTROID)

    change = place.set_centroid(HUNZA_CENTROID, clock=_stepping_clock(), ids=ids)

    assert (change.state, change.events) == (place, ())


# --------------------------------------------------------------------------- #
# retire and merge_into                                                       #
# --------------------------------------------------------------------------- #


def test_place_retire_sets_status_and_stripped_reason() -> None:
    place = _place()

    change = place.retire("  district abolished  ", clock=_stepping_clock(), ids=ids)

    assert (change.state.status, change.state.status_reason) == (
        "retired",
        "district abolished",
    )
    assert change.state.is_active is False
    event = change.events[0]
    assert isinstance(event, PlaceRetired)
    assert (event.reason, event.version) == ("district abolished", 2)


@pytest.mark.parametrize("reason", ["", "   ", "x" * 501])
def test_place_retire_reason_out_of_bounds_raises_validation_error(
    reason: str,
) -> None:
    place = _place()

    with pytest.raises(PydanticValidationError):
        place.retire(reason, clock=_stepping_clock(), ids=ids)


def test_place_merge_into_sets_status_target_and_reason() -> None:
    place = _place()
    target_id = ids.new_id()

    change = place.merge_into(
        target_id, "split recorded twice", clock=_stepping_clock(), ids=ids
    )

    assert (change.state.status, change.state.merged_into_id) == ("merged", target_id)
    event = change.events[0]
    assert isinstance(event, PlaceMerged)
    assert (event.target_id, event.reason, event.aggregate_id) == (
        target_id,
        "split recorded twice",
        place.id,
    )


def test_place_merge_into_itself_raises_invariant_violation() -> None:
    place = _place()

    with pytest.raises(InvariantViolationError):
        place.merge_into(place.id, "mistake", clock=_stepping_clock(), ids=ids)


def _retired() -> Place:
    return _place().retire("abolished", clock=_stepping_clock(), ids=ids).state


def _merged() -> Place:
    return (
        _place()
        .merge_into(ids.new_id(), "duplicate", clock=_stepping_clock(), ids=ids)
        .state
    )


@pytest.mark.parametrize("final_place", [_retired, _merged], ids=["retired", "merged"])
@pytest.mark.parametrize(
    "operation",
    [
        lambda place: place.add_name(
            PlaceName(text="Kanjut", language="en"), clock=_stepping_clock(), ids=ids
        ),
        lambda place: place.set_preferred_name(
            "en", "Hunza", clock=_stepping_clock(), ids=ids
        ),
        lambda place: place.set_geometry(None, clock=_stepping_clock(), ids=ids),
        lambda place: place.set_centroid(
            HUNZA_CENTROID, clock=_stepping_clock(), ids=ids
        ),
        lambda place: place.retire("again", clock=_stepping_clock(), ids=ids),
        lambda place: place.merge_into(
            ids.new_id(), "again", clock=_stepping_clock(), ids=ids
        ),
    ],
    ids=[
        "add_name",
        "set_preferred_name",
        "set_geometry",
        "set_centroid",
        "retire",
        "merge_into",
    ],
)
def test_place_change_after_final_status_raises_place_retired_error(
    final_place: Callable[[], Place], operation: Callable[[Place], object]
) -> None:
    place = final_place()

    with pytest.raises(PlaceRetiredError) as raised:
        operation(place)

    assert raised.value.details["status"] == place.status
