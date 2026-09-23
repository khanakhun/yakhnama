"""Unit tests for the geography queries, specifications, DTOs and read-side fakes."""

import typing

import pydantic
import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.fakes.geography import (
    InMemoryGeographyUnitOfWork,
    InMemoryPlaceQueryService,
    InMemoryPlaceRepository,
)
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.unit.modules.geography.application.support import (
    entry,
    name,
    reference_file,
    retired,
    stored_places,
)
from yakhnama.modules.geography.application.dto import (
    PlaceDetail,
    PlaceSummary,
    display_name,
)
from yakhnama.modules.geography.application.ports import (
    GeographyUnitOfWork,
    GeographyUnitOfWorkFactory,
    PlaceQueryService,
    PlaceRepository,
)
from yakhnama.modules.geography.application.queries import (
    SEARCH_TEXT_MAX_LENGTH,
    GetPlace,
    SearchPlaces,
)
from yakhnama.modules.geography.application.specifications import (
    ActivePlaceSpecification,
    PlaceLevelSpecification,
    PlaceTextSpecification,
    fold_search_text,
)
from yakhnama.modules.geography.domain.entities import Place
from yakhnama.modules.geography.domain.value_objects import AdminLevel
from yakhnama.shared_kernel.errors import ConflictError, NotFoundError
from yakhnama.shared_kernel.pagination import PageRequest
from yakhnama.shared_kernel.specification import AndSpecification


def hierarchy() -> tuple[Place, ...]:
    """Return a country, two regions (one retired) and a district.

    ``xx.a`` has an English and an Urdu name; ``xx.b`` is retired.
    """
    country, region_a, region_b, district = stored_places(
        reference_file(
            entry("xx", "country", names=[name("Countryland")]),
            entry(
                "xx.a",
                "province_or_region",
                parent_code="xx",
                names=[name("Hunzā Example"), name("ہنزہ", language="ur")],
            ),
            entry("xx.b", "province_or_region", parent_code="xx"),
            entry("xx.c", "district", parent_code="xx.a"),
        )
    )
    return country, region_a, retired(region_b), district


# --------------------------------------------------------------------------- #
# Queries                                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text", ["", "   ", "x" * (SEARCH_TEXT_MAX_LENGTH + 1)])
def test_search_places_with_out_of_bounds_text_raises_validation_error(
    text: str,
) -> None:
    with pytest.raises(pydantic.ValidationError):
        SearchPlaces(text=text)


def test_search_places_strips_text_and_normalises_language() -> None:
    query = SearchPlaces(text="  hunza  ", language="UR-arab")

    assert query.text == "hunza"
    assert query.language == "ur-Arab"


def test_search_places_with_invalid_language_raises_validation_error() -> None:
    with pytest.raises(pydantic.ValidationError):
        SearchPlaces(text="hunza", language="not a language")


def test_search_places_to_specification_combines_text_level_and_active() -> None:
    specification = SearchPlaces(
        text="example", level=AdminLevel.DISTRICT
    ).to_specification()

    assert isinstance(specification, AndSpecification)
    assert isinstance(specification.right, ActivePlaceSpecification)
    assert isinstance(specification.left, AndSpecification)
    assert isinstance(specification.left.right, PlaceLevelSpecification)


def test_search_places_including_inactive_uses_only_the_text_match() -> None:
    specification = SearchPlaces(
        text="example", include_inactive=True
    ).to_specification()

    assert isinstance(specification, PlaceTextSpecification)
    assert specification.text == "example"
    assert specification.language is None


def test_get_place_with_non_uuid7_id_raises_validation_error() -> None:
    with pytest.raises(pydantic.ValidationError):
        GetPlace.model_validate({"place_id": "00000000-0000-4000-8000-000000000000"})


# --------------------------------------------------------------------------- #
# Specifications                                                              #
# --------------------------------------------------------------------------- #


def test_fold_search_text_removes_marks_and_case() -> None:
    assert fold_search_text("  Hunzā ") == "hunza"


@given(st.text(max_size=50))
def test_fold_search_text_is_idempotent(text: str) -> None:
    once = fold_search_text(text)

    assert fold_search_text(once) == once


def test_place_text_specification_matches_accent_insensitively() -> None:
    _, region_a, _, _ = hierarchy()

    assert PlaceTextSpecification("hunza").is_satisfied_by(region_a) is True
    assert PlaceTextSpecification("HUNZĀ").is_satisfied_by(region_a) is True
    assert PlaceTextSpecification("gilgit").is_satisfied_by(region_a) is False


def test_place_text_specification_with_language_matches_only_that_language() -> None:
    _, region_a, _, _ = hierarchy()

    assert PlaceTextSpecification("ہنزہ", "ur").is_satisfied_by(region_a) is True
    assert PlaceTextSpecification("hunza", "ur").is_satisfied_by(region_a) is False


def test_place_level_and_active_specifications_compose() -> None:
    _, region_a, region_b, district = hierarchy()
    specification = PlaceLevelSpecification(AdminLevel.PROVINCE_OR_REGION).and_(
        ActivePlaceSpecification()
    )

    matches = [
        p.code
        for p in (region_a, region_b, district)
        if specification.is_satisfied_by(p)
    ]

    assert matches == ["xx.a"]
    assert PlaceLevelSpecification(AdminLevel.DISTRICT).level is AdminLevel.DISTRICT


# --------------------------------------------------------------------------- #
# DTOs                                                                        #
# --------------------------------------------------------------------------- #


def test_display_name_prefers_requested_language_then_english() -> None:
    _, region_a, _, _ = hierarchy()

    assert display_name(region_a, "ur").text == "ہنزہ"
    assert display_name(region_a, "bsk").text == "Hunzā Example"
    assert display_name(region_a, None).text == "Hunzā Example"


def test_display_name_without_english_falls_back_to_first_name() -> None:
    (country,) = stored_places(
        reference_file(entry("xx", "country", names=[name("ملک", language="ur")]))
    )

    assert display_name(country, "en").text == "ملک"


def test_place_summary_and_detail_from_entity_carry_parent_code() -> None:
    _, region_a, _, _ = hierarchy()

    summary = PlaceSummary.from_entity(region_a, parent_code="xx", language="ur")
    detail = PlaceDetail.from_entity(region_a, parent_code="xx")

    assert summary.name.text == "ہنزہ"
    assert summary.parent_code == "xx"
    assert detail.names == region_a.names
    assert detail.parent_id == region_a.parent_id
    assert detail.version == region_a.version


# --------------------------------------------------------------------------- #
# Fakes honour the ports                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("protocol", "fake"),
    [
        (PlaceRepository, InMemoryPlaceRepository),
        (PlaceQueryService, InMemoryPlaceQueryService),
        (GeographyUnitOfWork, InMemoryGeographyUnitOfWork),
    ],
)
def test_fake_defines_every_protocol_member(protocol: type, fake: type) -> None:
    members = typing.get_protocol_members(protocol)

    missing = sorted(member for member in members if not hasattr(fake, member))

    # ``places`` is an instance attribute of the unit of work fake.
    assert missing in ([], ["places"])


def test_fakes_satisfy_ports_statically() -> None:
    uow = InMemoryGeographyUnitOfWork()

    repository: PlaceRepository = uow.places
    unit_of_work: GeographyUnitOfWork = uow
    factory: GeographyUnitOfWorkFactory = InMemoryUnitOfWorkFactory(uow)
    service: PlaceQueryService = InMemoryPlaceQueryService(uow.places)

    assert unit_of_work.places is repository
    assert factory() is uow
    assert service is not None


async def test_fake_repository_add_with_taken_code_raises_conflict() -> None:
    (country,) = stored_places(reference_file(entry("xx", "country")))
    repository = InMemoryPlaceRepository([country])

    with pytest.raises(ConflictError):
        await repository.add(country)


async def test_fake_repository_save_of_unknown_place_raises_not_found() -> None:
    (country,) = stored_places(reference_file(entry("xx", "country")))

    with pytest.raises(NotFoundError):
        await InMemoryPlaceRepository().save(country)


async def test_fake_repository_get_and_get_by_code_find_the_same_place() -> None:
    (country,) = stored_places(reference_file(entry("xx", "country")))
    repository = InMemoryPlaceRepository([country])

    assert await repository.get(country.id) == country
    assert await repository.get_by_code("xx") == country
    assert await repository.get_by_code("yy") is None


async def test_fake_query_service_search_pages_active_matches_by_code() -> None:
    service = InMemoryPlaceQueryService(InMemoryPlaceRepository(hierarchy()))

    first = await service.search(
        SearchPlaces(text="example", page=PageRequest(limit=1))
    )
    second = await service.search(
        SearchPlaces(
            text="example", page=PageRequest(limit=1, cursor=first.next_cursor)
        )
    )
    with_inactive = await service.search(
        SearchPlaces(text="example", include_inactive=True)
    )

    assert [item.code for item in first.items] == ["xx.a"]
    assert [item.code for item in second.items] == ["xx.c"]
    assert first.next_cursor is not None
    assert second.next_cursor is None
    assert [item.code for item in with_inactive.items] == ["xx.a", "xx.b", "xx.c"]
    assert second.items[0].parent_code == "xx.a"


async def test_fake_query_service_get_returns_detail_or_none() -> None:
    country, region_a, _, _ = hierarchy()
    service = InMemoryPlaceQueryService(InMemoryPlaceRepository(hierarchy()))

    found = await service.get(region_a.id)
    root = await service.get(country.id)
    missing = await service.get(SequentialIdGenerator(seed=4242).new_id())

    assert found is not None
    assert found.parent_code == "xx"
    assert root is not None
    assert root.parent_code is None
    assert missing is None


def test_search_places_text_of_combining_marks_only_raises_validation_error() -> None:
    marks_only = "\u0301\u0308"

    with pytest.raises(pydantic.ValidationError):
        SearchPlaces(text=marks_only)
