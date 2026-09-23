"""The SQL place query service against real PostGIS, including multilingual search."""

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories.geography import PlaceTestFactory
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.geography.application.dto import PlaceSummary
from yakhnama.modules.geography.application.queries import SearchPlaces
from yakhnama.modules.geography.application.specifications import (
    ActivePlaceSpecification,
    PlaceLevelSpecification,
    PlaceTextSpecification,
)
from yakhnama.modules.geography.domain.entities import Place
from yakhnama.modules.geography.domain.value_objects import (
    AdminLevel,
    PlaceName,
    ScriptCode,
)
from yakhnama.modules.geography.infrastructure.orm import PlaceRow
from yakhnama.modules.geography.infrastructure.queries import (
    PlaceSpecificationCompiler,
    SearchTextCollector,
    SqlAlchemyPlaceQueryService,
    decode_score,
    escape_like,
    search_forms_for,
)
from yakhnama.modules.geography.infrastructure.uow import (
    SqlAlchemyGeographyUnitOfWork,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.pagination import (
    CursorPayload,
    Page,
    PageRequest,
    encode_cursor,
)
from yakhnama.shared_kernel.specification import (
    FalseSpecification,
    Specification,
    TrueSpecification,
)

pytestmark = pytest.mark.integration

type GeographyFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyGeographyUnitOfWork]

# Stands in for the provenance record every non-English name needs (reference rule:
# only English names may ship without a source).
URDU_SOURCE_ID = UUID("0192a3b4-0000-7000-8000-0000000000aa")


class _UnknownSpecification(Specification[Place]):
    """A leaf the SQL compiler has never heard of.

    Implements: Specification.
    """

    def is_satisfied_by(self, candidate: Place) -> bool:
        """Accept everything; only its type matters here.

        Args:
            candidate: Ignored.

        Returns:
            Always ``True``.
        """
        return True


def _names_of(*names: PlaceName) -> tuple[PlaceName, ...]:
    return names


class _Region:
    """A small synthetic hierarchy stored for one test."""

    def __init__(self) -> None:
        self.country = PlaceTestFactory.build(
            level=AdminLevel.COUNTRY,
            names=_names_of(PlaceName(text="Test Country", language="en")),
        )
        self.hunza = PlaceTestFactory.build(
            level=AdminLevel.DISTRICT,
            parent_id=self.country.id,
            names=_names_of(
                PlaceName(text="Hunza", language="en", is_preferred=True),
                PlaceName(text="Hunzā", language="en", kind="alternative"),
                PlaceName(
                    text="ہنزہ",
                    language="ur",
                    script=ScriptCode.ARAB,
                    is_preferred=True,
                    source_id=URDU_SOURCE_ID,
                ),
            ),
        )
        self.nagar = PlaceTestFactory.build(
            level=AdminLevel.DISTRICT,
            parent_id=self.country.id,
            names=_names_of(PlaceName(text="Nagar", language="en", is_preferred=True)),
        )
        self.hunza_village = PlaceTestFactory.build(
            level=AdminLevel.VILLAGE,
            parent_id=self.hunza.id,
            names=_names_of(PlaceName(text="Test Hunza Village", language="en")),
        )

    @property
    def places(self) -> tuple[Place, ...]:
        return (self.country, self.hunza, self.nagar, self.hunza_village)


@pytest.fixture
async def region(geography_uow_factory: GeographyFactory) -> _Region:
    """Store the synthetic hierarchy and return it."""
    stored = _Region()
    async with geography_uow_factory() as uow:
        for place in stored.places:
            await uow.places.add(place)
        await uow.commit()
    return stored


@pytest.fixture
def service(
    session_factory: async_sessionmaker[AsyncSession],
) -> SqlAlchemyPlaceQueryService:
    """Return the query service on the test database."""
    return SqlAlchemyPlaceQueryService(session_factory)


async def _codes(
    service: SqlAlchemyPlaceQueryService, query: SearchPlaces
) -> list[str]:
    page = await service.search(query)
    return [item.code for item in page.items]


@pytest.mark.parametrize("text", ["hunza", "Hunzā", "HUNZA", "ہنزہ", "hun", "unz"])
async def test_search_places_with_spelling_variant_returns_hunza(
    service: SqlAlchemyPlaceQueryService, region: _Region, text: str
) -> None:
    codes = await _codes(service, SearchPlaces(text=text, level=AdminLevel.DISTRICT))

    assert codes == [region.hunza.code]


async def test_search_places_with_misspelling_matches_by_trigram_similarity(
    service: SqlAlchemyPlaceQueryService, region: _Region
) -> None:
    codes = await _codes(service, SearchPlaces(text="hunzza"))

    assert region.hunza.code in codes


async def test_search_places_ranks_exact_name_above_containing_name(
    service: SqlAlchemyPlaceQueryService, region: _Region
) -> None:
    codes = await _codes(service, SearchPlaces(text="hunza"))

    assert codes == [region.hunza.code, region.hunza_village.code]


async def test_search_places_for_other_place_does_not_return_hunza(
    service: SqlAlchemyPlaceQueryService, region: _Region
) -> None:
    codes = await _codes(service, SearchPlaces(text="Nagar"))

    assert codes == [region.nagar.code]


async def test_search_places_with_no_match_returns_empty_page(
    service: SqlAlchemyPlaceQueryService, region: _Region
) -> None:
    page = await service.search(SearchPlaces(text="Skardu"))

    assert page == Page[PlaceSummary](items=(), next_cursor=None)


async def test_search_places_with_language_matches_only_names_in_it(
    service: SqlAlchemyPlaceQueryService, region: _Region
) -> None:
    latin_in_urdu = await _codes(service, SearchPlaces(text="hunza", language="ur"))
    urdu_in_urdu = await service.search(SearchPlaces(text="ہنزہ", language="ur"))

    assert latin_in_urdu == []
    assert [item.code for item in urdu_in_urdu.items] == [region.hunza.code]
    assert urdu_in_urdu.items[0].name.text == "ہنزہ"


async def test_search_places_summary_uses_display_name_and_parent_code(
    service: SqlAlchemyPlaceQueryService, region: _Region
) -> None:
    page = await service.search(SearchPlaces(text="Hunzā", level=AdminLevel.DISTRICT))

    assert page.items == (
        PlaceSummary.from_entity(
            region.hunza, parent_code=region.country.code, language=None
        ),
    )


async def test_search_places_with_level_returns_only_that_level(
    service: SqlAlchemyPlaceQueryService, region: _Region
) -> None:
    codes = await _codes(service, SearchPlaces(text="hunza", level=AdminLevel.VILLAGE))

    assert codes == [region.hunza_village.code]


async def test_search_places_excludes_retired_places_unless_asked(
    service: SqlAlchemyPlaceQueryService,
    region: _Region,
    geography_uow_factory: GeographyFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    async with geography_uow_factory() as uow:
        retired = region.nagar.retire("Test retirement", clock=clock, ids=ids).state
        await uow.places.save(retired)
        await uow.commit()

    active_only = await _codes(service, SearchPlaces(text="nagar"))
    everything = await service.search(SearchPlaces(text="nagar", include_inactive=True))

    assert active_only == []
    assert [item.code for item in everything.items] == [region.nagar.code]
    assert everything.items[0].status == "retired"


async def test_search_places_pages_are_stable_and_exhaustive(
    service: SqlAlchemyPlaceQueryService, geography_uow_factory: GeographyFactory
) -> None:
    country = PlaceTestFactory.build(level=AdminLevel.COUNTRY)
    # Names of different lengths give different scores; the repeated ones tie and
    # are ordered by id.
    texts = [
        "Test Peak",
        "Test Peak",
        "Test Peak North",
        "Test Peak North",
        "Test Peak South Ridge",
        "Test Peak",
        "Test Peak East Face Camp",
    ]
    places = [
        PlaceTestFactory.build(
            level=AdminLevel.DISTRICT,
            parent_id=country.id,
            names=_names_of(PlaceName(text=text, language="en")),
        )
        for text in texts
    ]
    async with geography_uow_factory() as uow:
        for place in (country, *places):
            await uow.places.add(place)
        await uow.commit()

    async def collect() -> list[str]:
        codes: list[str] = []
        cursor: str | None = None
        while True:
            page = await service.search(
                SearchPlaces(text="test peak", page=PageRequest(limit=3, cursor=cursor))
            )
            codes.extend(item.code for item in page.items)
            if page.next_cursor is None:
                return codes
            cursor = page.next_cursor

    first_run = await collect()
    second_run = await collect()
    one_page = await _codes(
        service, SearchPlaces(text="test peak", page=PageRequest(limit=10))
    )

    assert first_run == second_run == one_page
    assert sorted(first_run) == sorted(place.code for place in places)


async def test_search_places_with_malformed_cursor_score_raises_validation_error(
    service: SqlAlchemyPlaceQueryService, region: _Region
) -> None:
    cursor = encode_cursor(
        CursorPayload(sort_key="not-a-number", last_id=region.hunza.id)
    )

    with pytest.raises(ValidationError):
        await service.search(
            SearchPlaces(text="hunza", page=PageRequest(cursor=cursor))
        )


@pytest.mark.parametrize("sort_key", ["nan", "inf", ""])
def test_decode_score_with_non_finite_or_empty_value_raises_validation_error(
    sort_key: str,
) -> None:
    with pytest.raises(ValidationError):
        decode_score(sort_key)


def test_decode_score_with_repr_of_float_returns_same_float() -> None:
    score = 0.571_428_596_973_419_2

    decoded = decode_score(repr(score))

    assert decoded == score


def test_escape_like_escapes_wildcards_and_escape_character() -> None:
    escaped = escape_like("50%_off\\")

    assert escaped == "50\\%\\_off\\\\"


async def test_search_places_treats_like_wildcards_literally(
    service: SqlAlchemyPlaceQueryService, region: _Region
) -> None:
    codes = await _codes(service, SearchPlaces(text="%"))

    assert codes == []


async def test_search_forms_for_latin_text_adds_unaccent_form(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        latin = await search_forms_for(session, "Łake")
        plain = await search_forms_for(session, "Lake")
        arabic = await search_forms_for(session, "ہنزہ")

    assert latin == ("łake", "lake")
    assert plain == ("lake",)
    assert arabic == ("ہنزہ",)


async def test_search_places_with_unaccentable_letter_matches_both_ways(
    service: SqlAlchemyPlaceQueryService, geography_uow_factory: GeographyFactory
) -> None:
    country = PlaceTestFactory.build(
        level=AdminLevel.COUNTRY,
        names=_names_of(PlaceName(text="Test Łake", language="en")),
    )
    async with geography_uow_factory() as uow:
        await uow.places.add(country)
        await uow.commit()

    with_letter = await _codes(service, SearchPlaces(text="łake"))
    without_letter = await _codes(service, SearchPlaces(text="lake"))

    assert with_letter == without_letter == [country.code]


async def test_place_specification_compiler_constant_and_combined_leaves_run(
    session_factory: async_sessionmaker[AsyncSession], region: _Region
) -> None:
    compiler = PlaceSpecificationCompiler({"nagar": ("nagar",)})
    everything: Specification[Place] = TrueSpecification()
    nothing: Specification[Place] = FalseSpecification()
    villages_or_nagar = PlaceLevelSpecification(AdminLevel.VILLAGE).or_(
        PlaceTextSpecification("nagar")
    )
    not_country = PlaceLevelSpecification(AdminLevel.COUNTRY).not_()

    async def codes_for(specification: Specification[Place]) -> set[str]:
        async with session_factory() as session:
            rows = await session.execute(
                select(PlaceRow.code).where(specification.accept(compiler))
            )
            return set(rows.scalars())

    all_codes = await codes_for(everything.and_(ActivePlaceSpecification()))
    no_codes = await codes_for(nothing)
    either = await codes_for(villages_or_nagar)
    not_countries = await codes_for(not_country)

    assert all_codes == {place.code for place in region.places}
    assert no_codes == set()
    assert either == {region.hunza_village.code, region.nagar.code}
    assert not_countries == {
        region.hunza.code,
        region.nagar.code,
        region.hunza_village.code,
    }


def test_place_specification_compiler_with_unknown_leaf_raises_type_error() -> None:
    compiler = PlaceSpecificationCompiler({})

    with pytest.raises(TypeError, match="_UnknownSpecification"):
        _UnknownSpecification().accept(compiler)


def test_place_specification_compiler_without_prepared_forms_raises_key_error() -> None:
    compiler = PlaceSpecificationCompiler({})

    with pytest.raises(KeyError):
        PlaceTextSpecification("hunza").accept(compiler)


async def test_place_query_get_returns_detail_with_parent_code(
    service: SqlAlchemyPlaceQueryService, region: _Region
) -> None:
    detail = await service.get(region.hunza_village.id)

    assert detail is not None
    assert detail.parent_code == region.hunza.code
    assert detail.names == region.hunza_village.names
    assert detail.version == region.hunza_village.version


async def test_place_query_get_country_has_no_parent_code(
    service: SqlAlchemyPlaceQueryService, region: _Region
) -> None:
    detail = await service.get(region.country.id)

    assert detail is not None
    assert detail.parent_code is None


async def test_place_query_get_when_missing_returns_none(
    service: SqlAlchemyPlaceQueryService, region: _Region
) -> None:
    detail = await service.get(URDU_SOURCE_ID)

    assert detail is None


def test_search_text_collector_finds_text_leaves_under_every_combinator() -> None:
    first = PlaceTextSpecification("first")
    second = PlaceTextSpecification("second")
    tree = first.or_(second.not_()).and_(ActivePlaceSpecification())

    leaves = tree.accept(SearchTextCollector())

    assert leaves == (first, second)
