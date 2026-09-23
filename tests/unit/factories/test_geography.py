"""Unit tests for ``tests.factories.geography``."""

from datetime import UTC

from tests.factories.geography import PlaceNameFactory, PlaceTestFactory
from tests.factories.shared_kernel import TEST_REGION_ENVELOPE
from yakhnama.modules.geography.domain.entities import Place
from yakhnama.modules.geography.domain.value_objects import AdminLevel, PlaceName
from yakhnama.shared_kernel.ids import is_uuid7

BUILDS = 50


def test_place_name_factory_builds_valid_unique_english_names() -> None:
    names = [PlaceNameFactory.build() for _ in range(BUILDS)]

    assert all(PlaceName.model_validate(name.model_dump()) == name for name in names)
    assert all(name.language == "en" for name in names)
    assert len({name.key for name in names}) == BUILDS


def test_place_test_factory_builds_valid_places_with_distinct_ids_and_codes() -> None:
    places = [PlaceTestFactory.build() for _ in range(BUILDS)]

    assert all(Place.model_validate(place.model_dump()) == place for place in places)
    assert len({place.id for place in places}) == BUILDS
    assert len({place.code for place in places}) == BUILDS
    assert all(is_uuid7(place.id) for place in places)


def test_place_test_factory_parent_matches_level() -> None:
    places = [PlaceTestFactory.build() for _ in range(BUILDS)]

    assert all(
        (place.parent_id is None) == (place.level is AdminLevel.COUNTRY)
        for place in places
    )
    assert all(place.parent_id != place.id for place in places)


def test_place_test_factory_builds_active_version_one_utc_places() -> None:
    places = [PlaceTestFactory.build() for _ in range(BUILDS)]

    assert all(place.is_active and place.version == 1 for place in places)
    assert all(place.created_at.tzinfo is UTC for place in places)
    assert all(place.updated_at == place.created_at for place in places)
    assert all(
        place.centroid is not None and TEST_REGION_ENVELOPE.contains(place.centroid)
        for place in places
    )
    assert all(place.preferred_name("en") is not None for place in places)


def test_place_test_factory_level_override_sets_the_parent() -> None:
    country = PlaceTestFactory.build(level=AdminLevel.COUNTRY)
    village = PlaceTestFactory.build(level=AdminLevel.VILLAGE)

    assert country.parent_id is None
    assert village.parent_id is not None
