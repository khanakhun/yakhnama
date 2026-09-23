"""Unit tests for ``yakhnama.modules.geography.domain.factories``."""

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from tests.fakes.clock import FrozenClock
from yakhnama.modules.geography.domain.entities import Place
from yakhnama.modules.geography.domain.errors import (
    InvalidPlaceHierarchyError,
    PlaceRetiredError,
)
from yakhnama.modules.geography.domain.events import PlaceCreated
from yakhnama.modules.geography.domain.factories import PlaceDraft, PlaceFactory
from yakhnama.modules.geography.domain.value_objects import (
    AdminLevel,
    PlaceGeometry,
    PlaceName,
)
from yakhnama.shared_kernel.ids import Uuid7Generator, extract_timestamp
from yakhnama.shared_kernel.value_objects import Coordinates

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
LEVELS = st.sampled_from(list(AdminLevel))
factory = PlaceFactory()


clock = FrozenClock(NOW)
ids = Uuid7Generator(clock=clock)


def _draft(
    code: str, level: AdminLevel, parent_code: str | None = None, **extra: object
) -> PlaceDraft:
    return PlaceDraft.model_validate(
        {
            "code": code,
            "level": level,
            "parent_code": parent_code,
            "names": (PlaceName(text=code, language="en", is_preferred=True),),
            **extra,
        }
    )


def _create(draft: PlaceDraft, parent: Place | None = None) -> Place:
    return factory.create(draft, parent=parent, ids=ids, clock=clock).state


def test_place_factory_create_country_returns_version_one_place_and_event() -> None:
    centroid = Coordinates(longitude=69.3, latitude=30.4)
    draft = _draft("pk", AdminLevel.COUNTRY, centroid=centroid)

    change = factory.create(draft, parent=None, ids=ids, clock=clock)

    place = change.state
    assert (place.code, place.level, place.parent_id, place.version) == (
        "pk",
        AdminLevel.COUNTRY,
        None,
        1,
    )
    assert place.created_at == place.updated_at == NOW
    assert extract_timestamp(place.id) == NOW
    event = change.events[0]
    assert isinstance(event, PlaceCreated)
    assert (event.aggregate_id, event.place_code, event.version) == (place.id, "pk", 1)
    assert (event.names, event.centroid, event.geometry_type) == (
        place.names,
        centroid,
        None,
    )
    assert event.event_id != place.id


def test_place_factory_create_with_geometry_reports_geometry_type() -> None:
    geometry = PlaceGeometry.from_coordinates(Coordinates(longitude=69, latitude=30))
    draft = _draft("pk", AdminLevel.COUNTRY, geometry=geometry)

    change = factory.create(draft, parent=None, ids=ids, clock=clock)

    event = change.events[0]
    assert isinstance(event, PlaceCreated)
    assert event.geometry_type == "Point"
    assert change.state.geometry == geometry


def test_place_factory_create_child_links_parent_id() -> None:
    country = _create(_draft("pk", AdminLevel.COUNTRY))

    region = _create(_draft("pk.gb", AdminLevel.PROVINCE_OR_REGION, "pk"), country)

    assert region.parent_id == country.id


def test_place_factory_create_allows_gaps_in_the_hierarchy() -> None:
    district = _create(
        _draft("pk.gb.hunza", AdminLevel.DISTRICT, "pk.gb"),
        _create(
            _draft("pk.gb", AdminLevel.PROVINCE_OR_REGION, "pk"),
            _create(_draft("pk", AdminLevel.COUNTRY)),
        ),
    )

    village = _create(
        _draft("pk.gb.hunza.karimabad", AdminLevel.VILLAGE, "pk.gb.hunza"), district
    )

    assert village.parent_id == district.id


@given(level=LEVELS, parent_level=LEVELS)
def test_place_factory_create_accepts_parent_exactly_when_level_is_higher(
    level: AdminLevel, parent_level: AdminLevel
) -> None:
    parent = Place.model_validate(
        {
            "id": ids.new_id(),
            "code": "parent",
            "level": parent_level,
            "parent_id": None if parent_level is AdminLevel.COUNTRY else ids.new_id(),
            "names": (PlaceName(text="Parent", language="en"),),
            "created_at": NOW,
            "updated_at": NOW,
        }
    )
    draft = _draft("child", level, "parent")

    try:
        _create(draft, parent)
        accepted = True
    except InvalidPlaceHierarchyError:
        accepted = False

    assert accepted == level.is_below(parent_level)


def test_place_factory_create_non_country_without_parent_raises_hierarchy_error() -> (
    None
):
    draft = _draft("pk.gb", AdminLevel.PROVINCE_OR_REGION)

    with pytest.raises(InvalidPlaceHierarchyError) as raised:
        _create(draft)

    assert raised.value.details["parent_level"] is None


def test_place_factory_create_country_under_parent_raises_hierarchy_error() -> None:
    other = _create(_draft("pk", AdminLevel.COUNTRY))
    draft = _draft("in", AdminLevel.COUNTRY, "pk")

    with pytest.raises(InvalidPlaceHierarchyError) as raised:
        _create(draft, other)

    assert raised.value.details["parent_level"] == "country"


def test_place_factory_create_parent_code_mismatch_raises_hierarchy_error() -> None:
    country = _create(_draft("pk", AdminLevel.COUNTRY))
    draft = _draft("pk.gb", AdminLevel.PROVINCE_OR_REGION, "pk.other")

    with pytest.raises(InvalidPlaceHierarchyError, match="names parent"):
        _create(draft, country)


def test_place_factory_create_parent_given_but_not_named_raises_hierarchy_error() -> (
    None
):
    country = _create(_draft("pk", AdminLevel.COUNTRY))
    draft = _draft("pk.gb", AdminLevel.PROVINCE_OR_REGION)

    with pytest.raises(InvalidPlaceHierarchyError, match="names parent"):
        _create(draft, country)


def test_place_factory_create_under_retired_parent_raises_place_retired_error() -> None:
    country = _create(_draft("pk", AdminLevel.COUNTRY))
    retired = country.retire("test", clock=clock, ids=ids).state
    draft = _draft("pk.gb", AdminLevel.PROVINCE_OR_REGION, "pk")

    with pytest.raises(PlaceRetiredError):
        _create(draft, retired)


def test_place_draft_without_names_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        PlaceDraft(code="pk", level=AdminLevel.COUNTRY, names=())


def test_place_draft_with_invalid_names_raises_on_create() -> None:
    names = (
        PlaceName(text="Pakistan", language="en", is_preferred=True),
        PlaceName(text="Pakistan", language="en"),
    )
    draft = PlaceDraft(code="pk", level=AdminLevel.COUNTRY, names=names)

    with pytest.raises(PydanticValidationError, match="duplicate"):
        _create(draft)
