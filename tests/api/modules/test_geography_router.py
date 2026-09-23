"""HTTP tests for ``/api/v1/places``: search, detail and GeoJSON negotiation.

Place names are synthetic placeholders, never real local names.
"""

import pytest

from tests.factories.geography import PlaceNameFactory, PlaceTestFactory
from tests.fakes.api import build_test_app
from yakhnama.modules.geography.api.router import wants_geojson
from yakhnama.modules.geography.api.schemas import PlaceFormat
from yakhnama.modules.geography.domain.entities import Place
from yakhnama.modules.geography.public import AdminLevel
from yakhnama.platform.etag import make_etag
from yakhnama.shared_kernel.value_objects import Coordinates

PLACES = "/api/v1/places"
GEOJSON = "application/geo+json"
CENTROID = Coordinates(longitude=74.5, latitude=36.25)


def _country(text: str, *, centroid: Coordinates | None = CENTROID) -> Place:
    return PlaceTestFactory.build(
        level=AdminLevel.COUNTRY,
        names=(PlaceNameFactory.build(text=text, is_preferred=True),),
        centroid=centroid,
    )


def _district(text: str, parent: Place) -> Place:
    return PlaceTestFactory.build(
        level=AdminLevel.DISTRICT,
        parent_id=parent.id,
        names=(PlaceNameFactory.build(text=text, is_preferred=True),),
    )


async def test_list_places_anonymous_returns_json_page_of_matches() -> None:
    alpha = _country("Alphatest")
    api = build_test_app(places=[alpha, _country("Betatest")])

    async with api.client() as client:
        response = await client.get(PLACES, params={"q": "alpha"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert [item["id"] for item in response.json()["items"]] == [str(alpha.id)]
    assert response.headers["vary"] == "Accept"


async def test_list_places_with_level_returns_only_that_level() -> None:
    country = _country("Gammatest land")
    district = _district("Gammatest district", country)
    api = build_test_app(places=[country, district])

    async with api.client() as client:
        response = await client.get(
            PLACES, params={"q": "gammatest", "level": "district"}
        )

    items = response.json()["items"]
    assert [item["id"] for item in items] == [str(district.id)]
    assert items[0]["parent_code"] == country.code


async def test_list_places_without_q_returns_422_problem() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.get(PLACES)

    assert response.status_code == 422
    assert response.json()["errors"][0]["loc"][-1] == "q"


async def test_list_places_with_invalid_language_returns_422_without_echo() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.get(PLACES, params={"q": "x", "language": "!!bad!!"})

    assert response.status_code == 422
    assert "!!bad!!" not in response.text


async def test_list_places_accepting_geojson_returns_feature_collection() -> None:
    alpha = _country("Alphatest")
    api = build_test_app(places=[alpha])

    async with api.client() as client:
        response = await client.get(
            PLACES, params={"q": "alpha"}, headers={"Accept": GEOJSON}
        )

    body = response.json()
    assert response.headers["content-type"] == GEOJSON
    assert body["type"] == "FeatureCollection"
    feature = body["features"][0]
    assert feature["geometry"] == {"type": "Point", "coordinates": [74.5, 36.25]}
    assert feature["properties"] == {
        "id": str(alpha.id),
        "code": alpha.code,
        "level": "country",
        "display_name": "Alphatest",
    }


async def test_list_places_format_geojson_without_centroid_has_null_geometry() -> None:
    api = build_test_app(places=[_country("Deltatest", centroid=None)])

    async with api.client() as client:
        response = await client.get(PLACES, params={"q": "delta", "format": "geojson"})

    assert response.headers["content-type"] == GEOJSON
    assert response.json()["features"][0]["geometry"] is None


async def test_list_places_format_json_overrides_geojson_accept() -> None:
    api = build_test_app(places=[_country("Alphatest")])

    async with api.client() as client:
        response = await client.get(
            PLACES, params={"q": "alpha", "format": "json"}, headers={"Accept": GEOJSON}
        )

    assert response.headers["content-type"] == "application/json"
    assert "items" in response.json()


async def test_list_places_geojson_walk_keeps_format_in_next_link() -> None:
    places = [_country(f"Epsilontest {index}") for index in range(3)]
    api = build_test_app(places=places)

    async with api.client() as client:
        first = await client.get(
            PLACES, params={"q": "epsilontest", "format": "geojson", "limit": 2}
        )
        second = await client.get(first.headers["link"].split(";")[0].strip("<>"))

    seen = [
        feature["id"] for page in (first, second) for feature in page.json()["features"]
    ]
    assert sorted(seen) == sorted(str(place.id) for place in places)
    assert "format=geojson" in first.headers["link"]
    assert "link" not in second.headers


async def test_get_place_anonymous_returns_detail_with_etag() -> None:
    alpha = _country("Alphatest")
    api = build_test_app(places=[alpha])

    async with api.client() as client:
        response = await client.get(f"{PLACES}/{alpha.id}")

    assert response.status_code == 200
    assert response.json()["code"] == alpha.code
    assert response.headers["etag"] == make_etag(alpha.version, alpha.id)


async def test_get_place_accepting_geojson_returns_feature_with_geometry() -> None:
    alpha = _country("Alphatest")
    api = build_test_app(places=[alpha])

    async with api.client() as client:
        response = await client.get(f"{PLACES}/{alpha.id}", headers={"Accept": GEOJSON})

    body = response.json()
    assert response.headers["content-type"] == GEOJSON
    assert response.headers["etag"] == make_etag(alpha.version, alpha.id)
    assert body["type"] == "Feature"
    assert body["id"] == str(alpha.id)
    assert body["geometry"]["coordinates"] == [74.5, 36.25]
    assert body["properties"]["names"][0]["text"] == "Alphatest"


async def test_get_place_when_missing_returns_404_problem() -> None:
    api = build_test_app()
    missing = PlaceTestFactory.build().id

    async with api.client() as client:
        response = await client.get(f"{PLACES}/{missing}")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_get_place_with_non_uuid7_id_returns_422() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.get(f"{PLACES}/00000000-0000-4000-8000-000000000001")

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("output_format", "accept", "expected"),
    [
        (None, None, False),
        (None, "application/json", False),
        (None, "Application/GEO+JSON;q=0.9", True),
        (PlaceFormat.GEOJSON, None, True),
        (PlaceFormat.JSON, GEOJSON, False),
    ],
)
def test_wants_geojson_format_and_accept_combinations_decide(
    *, output_format: PlaceFormat | None, accept: str | None, expected: bool
) -> None:
    decision = wants_geojson(output_format, accept)

    assert decision is expected
