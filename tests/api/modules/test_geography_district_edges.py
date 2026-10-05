"""HTTP tests for ``GET /api/v1/boundaries/district-edges`` (ADR 0021).

The last test loads boundaries and checks that the places routes then serve each
linked district's representative point as its centroid (Q239).

The edges are synthetic lines between synthetic districts; no real boundary is used.
"""

from datetime import UTC, datetime
from typing import Any

from tests.factories.boundaries import (
    SYNTHETIC_SHA256,
    attribution,
    boundary_source,
    grid_boundary_set,
    line,
)
from tests.factories.geography import PlaceTestFactory
from tests.fakes.api import ApiHarness, auth_headers, build_test_app
from tests.fakes.geography import StaticBoundaryLoader, StaticSharedEdgeCalculator
from tests.fakes.identity import actor_with
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.modules.geography.application.authorisation import (
    reference_data_policy,
)
from yakhnama.modules.geography.application.commands import LoadDistrictBoundaries
from yakhnama.modules.geography.application.dto import (
    DistrictEdgeFeatureCollection,
    SharedEdgeComputation,
)
from yakhnama.modules.geography.application.handlers import (
    LoadDistrictBoundariesHandler,
)
from yakhnama.modules.geography.domain.boundaries import DistrictEdge, DistrictEdgeSet
from yakhnama.modules.geography.domain.value_objects import AdminLevel, PlaceName
from yakhnama.modules.identity.public import Role
from yakhnama.platform.etag import make_etag
from yakhnama.shared_kernel.ids import EntityId

PATH = "/api/v1/boundaries/district-edges"
GEOJSON = "application/geo+json"
EDGE_SET_ID = EntityId("0192a3b4-0000-7000-8000-0000000000e1")
EDGES = (
    DistrictEdge(
        source_codes=("XX101", "XX102"),
        place_codes=("pk.gb.gilgit", "pk.gb.hunza"),
        geometry=line((11.0, 20.0005), (11.0, 21.0)),
    ),
    DistrictEdge(
        source_codes=("XX102", "XX103"),
        place_codes=("pk.gb.hunza", None),
        geometry=line((12.0, 20.0005), (12.0, 21.0)),
    ),
)


def _with_edges() -> ApiHarness:
    api = build_test_app()
    api.geography.district_edge_sets.committed.append(
        DistrictEdgeSet(
            id=EDGE_SET_ID,
            region_code="XX1",
            attribution=attribution(),
            sha256=SYNTHETIC_SHA256,
            fingerprint=DistrictEdgeSet.fingerprint_of("XX1", SYNTHETIC_SHA256, EDGES),
            edges=EDGES,
            created_at=datetime(2026, 10, 5, 13, 0, tzinfo=UTC),
        )
    )
    return api


async def test_district_edges_without_boundaries_returns_empty_collection() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.get(PATH)

    assert response.status_code == 200
    assert response.headers["content-type"] == GEOJSON
    assert response.json() == {
        "type": "FeatureCollection",
        "features": [],
        "attribution": None,
    }
    assert response.headers["cache-control"] == "public, max-age=300"
    assert "etag" not in response.headers


async def test_district_edges_returns_lines_with_attribution_and_cache_headers() -> (
    None
):
    api = _with_edges()

    async with api.client() as client:
        response = await client.get(PATH)

    assert response.status_code == 200
    assert response.headers["content-type"] == GEOJSON
    assert response.headers["cache-control"] == "public, max-age=86400"
    assert response.headers["etag"] == make_etag(1, EDGE_SET_ID)
    body = response.json()
    assert body["type"] == "FeatureCollection"
    assert [feature["properties"] for feature in body["features"]] == [
        {
            "districts": ["pk.gb.gilgit", "pk.gb.hunza"],
            "source_districts": ["XX101", "XX102"],
        },
        {"districts": ["pk.gb.hunza", None], "source_districts": ["XX102", "XX103"]},
    ]
    assert {feature["geometry"]["type"] for feature in body["features"]} == {
        "LineString"
    }
    assert body["attribution"]["licence"] == attribution().licence
    assert body["attribution"]["retrieved_at"] == "2026-10-05T12:00:00Z"
    assert DistrictEdgeFeatureCollection.model_validate(body).features


async def test_district_edges_never_returns_a_polygon() -> None:
    api = _with_edges()

    async with api.client() as client:
        body = (await client.get(PATH)).json()

    types = {feature["geometry"]["type"] for feature in body["features"]}
    assert types <= {"LineString", "MultiLineString"}


async def test_district_edges_with_current_etag_returns_304_without_body() -> None:
    api = _with_edges()
    etag = make_etag(1, EDGE_SET_ID)

    async with api.client() as client:
        strong = await client.get(PATH, headers={"If-None-Match": etag})
        weak = await client.get(PATH, headers={"If-None-Match": f"W/{etag}"})

    for response in (strong, weak):
        assert response.status_code == 304
        assert response.content == b""
        assert response.headers["etag"] == etag
        assert response.headers["cache-control"] == "public, max-age=86400"


async def test_district_edges_with_stale_etag_returns_200() -> None:
    api = _with_edges()

    async with api.client() as client:
        response = await client.get(PATH, headers={"If-None-Match": '"stale:1"'})

    assert response.status_code == 200


async def test_district_edges_with_rejected_token_returns_401() -> None:
    api = _with_edges()

    async with api.client() as client:
        response = await client.get(
            PATH, headers={"Authorization": "Bearer not-a-valid-token"}
        )

    assert response.status_code == 401


async def test_district_edges_with_valid_token_is_not_stored_in_shared_caches() -> None:
    api = _with_edges()

    async with api.client() as client:
        response = await client.get(PATH, headers=auth_headers())

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_district_edges_openapi_types_the_geojson_body() -> None:
    api = build_test_app()

    document: dict[str, Any] = api.app.openapi()

    operation = document["paths"][PATH]["get"]
    content = operation["responses"]["200"]["content"]
    assert list(content) == [GEOJSON]
    assert content[GEOJSON]["schema"] == {
        "$ref": "#/components/schemas/DistrictEdgeFeatureCollection"
    }
    assert "304" in operation["responses"]
    assert "security" not in operation
    schemas = document["components"]["schemas"]
    assert set(schemas["DistrictEdgeFeatureCollection"]["required"]) == {
        "type",
        "features",
        "attribution",
    }


async def test_places_after_boundary_load_serve_the_representative_point() -> None:
    country = PlaceTestFactory.build(level=AdminLevel.COUNTRY, code="xx")
    region = PlaceTestFactory.build(
        level=AdminLevel.PROVINCE_OR_REGION, code="xx.gb", parent_id=country.id
    )
    district = PlaceTestFactory.build(
        level=AdminLevel.DISTRICT,
        code="xx.gb.a",
        parent_id=region.id,
        names=(
            PlaceName(text="Synthetic Alphavale", language="en", is_preferred=True),
        ),
        centroid=None,
        geometry=None,
    )
    api = build_test_app(places=[country, region, district])
    handler = LoadDistrictBoundariesHandler(
        uow_factory=InMemoryUnitOfWorkFactory(api.geography),
        policy=reference_data_policy(),
        loader=StaticBoundaryLoader(grid_boundary_set(columns=1, rows=1)),
        calculator=StaticSharedEdgeCalculator(
            SharedEdgeComputation(edges=(), is_coverage_valid=True, dropped_parts=0)
        ),
        clock=api.clock,
        ids=SequentialIdGenerator(seed=12),
    )
    await handler(
        LoadDistrictBoundaries(
            source=boundary_source([("XX101", "xx.gb.a")], region_place_code="xx.gb"),
            actor=actor_with(
                {Role.ADMIN}, user_id=SequentialIdGenerator(seed=13).new_id()
            ),
        )
    )
    point = {"type": "Point", "coordinates": [10.5, 20.5]}

    async with api.client() as client:
        listed = await client.get(
            "/api/v1/places", params={"q": "alphavale", "format": "geojson"}
        )
        detail = await client.get(
            f"/api/v1/places/{district.id}", params={"format": "geojson"}
        )
        detail_json = await client.get(f"/api/v1/places/{district.id}")

    assert listed.json()["features"][0]["geometry"] == point
    assert detail.json()["geometry"] == point
    assert detail_json.json()["centroid"] == {"longitude": 10.5, "latitude": 20.5}
