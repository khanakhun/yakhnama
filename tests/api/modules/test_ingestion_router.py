"""HTTP tests for the ingestion catalog, runs, observations, rasters and ``/admin``.

Every dataset, station and scene here is synthetic.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID

import httpx
import pytest

from tests.factories.ingestion import (
    DatasetTestFactory,
    DatasetVersionTestFactory,
    IngestionRunTestFactory,
    ObservationTestFactory,
    RasterAssetTestFactory,
    StationRefTestFactory,
)
from tests.fakes.api import TEST_ADAPTER_NAME, ApiHarness, auth_headers, build_test_app
from tests.fakes.ingestion import InMemoryIngestionUnitOfWork
from yakhnama.modules.ingestion.domain.entities import (
    Dataset,
    DatasetVersion,
    Observation,
    RasterAsset,
)
from yakhnama.modules.ingestion.public import INGESTION_RUN_TASK, DatasetStatus
from yakhnama.platform.etag import make_etag
from yakhnama.shared_kernel.value_objects import DatePrecision, DateWithPrecision

DATASETS: Final = "/api/v1/datasets"
RUNS: Final = "/api/v1/ingestion-runs"
OBSERVATIONS: Final = "/api/v1/observations"
RASTERS: Final = "/api/v1/raster-assets"
ADMIN: Final = "/api/v1/admin"
GEOJSON: Final = "application/geo+json"
CODE: Final = "api_test_temperature"
UNKNOWN_ID: Final = "01890000-0000-7000-8000-000000000000"
START: Final = datetime(2026, 1, 1, tzinfo=UTC)
WINDOW: Final = {"from": "2026-01-01T00:00:00Z", "to": "2026-01-02T00:00:00Z"}
TRIGGERED_BY: Final = UUID("01890000-0000-7000-8000-0000000000aa")
LICENCE: Final = {
    "spdx_id": "CC-BY-4.0",
    "url": "https://creativecommons.org/licenses/by/4.0/",
    "attribution": "Synthetic publisher",
}


def admin() -> dict[str, str]:
    """Return the ``Authorization`` header of a platform administrator."""
    return auth_headers(subject="ingestion-admin", roles=["admin"])


def citizen() -> dict[str, str]:
    """Return the ``Authorization`` header of a citizen."""
    return auth_headers(subject="ingestion-citizen")


def moderator() -> dict[str, str]:
    """Return the ``Authorization`` header of a moderator."""
    return auth_headers(subject="ingestion-moderator", roles=["moderator"])


def hourly(hours: int) -> DateWithPrecision:
    """Return ``START`` plus ``hours``, at hour precision."""
    return DateWithPrecision(
        value=START + timedelta(hours=hours), precision=DatePrecision.HOUR
    )


class World:
    """One dataset with a version, runs, hourly observations and rasters.

    Implements: Factory (test data).

    Attributes:
        dataset: The dataset, code ``CODE``.
        version: Its one version.
        observations: Three hourly readings at one station.
        rasters: Two scenes, the newer first.
    """

    def __init__(self) -> None:
        """Build the synthetic records."""
        self.dataset: Dataset = DatasetTestFactory.build(code=CODE)
        self.version: DatasetVersion = DatasetVersionTestFactory.build(
            dataset_id=self.dataset.id
        )
        station = StationRefTestFactory.build(code="API-0001")
        self.observations: list[Observation] = [
            ObservationTestFactory.build(
                dataset_version_id=self.version.id,
                station=station,
                observed_at=hourly(hours),
            )
            for hours in range(3)
        ]
        self.rasters: list[RasterAsset] = [
            RasterAssetTestFactory.build(
                dataset_version_id=self.version.id,
                acquired_at=DateWithPrecision(
                    value=START + timedelta(days=days), precision=DatePrecision.EXACT
                ),
            )
            for days in (2, 1)
        ]
        self.run = IngestionRunTestFactory.build(
            dataset_version_id=self.version.id, triggered_by=TRIGGERED_BY
        )

    def app(self, *extra: Dataset) -> ApiHarness:
        """Return the test app over these records."""
        return build_test_app(
            ingestion=InMemoryIngestionUnitOfWork(
                datasets=[self.dataset, *extra],
                versions=[self.version],
                runs=[self.run],
                observations=self.observations,
                assets=self.rasters,
            )
        )


def next_url(response: httpx.Response) -> str | None:
    """Return the ``rel="next"`` target of a response, if any."""
    link = response.headers.get("link")
    return None if link is None else link.split(">")[0].lstrip("<")


async def walk(
    client: httpx.AsyncClient, url: str, key: str = "items"
) -> list[dict[str, Any]]:
    """Follow ``Link`` headers from ``url`` and collect every item."""
    items: list[dict[str, Any]] = []
    following: str | None = url
    while following is not None:
        response = await client.get(following)
        assert response.status_code == 200, response.text
        items.extend(response.json()[key])
        following = next_url(response)
    return items


# --------------------------------------------------------------------------- #
# Catalog                                                                     #
# --------------------------------------------------------------------------- #


async def test_list_datasets_anonymous_walks_every_page_in_code_order() -> None:
    world = World()
    others = [DatasetTestFactory.build(code=f"api_test_{name}") for name in "bcd"]
    api = world.app(*others)

    async with api.client() as client:
        items = await walk(client, f"{DATASETS}?limit=2")

    codes = [item["code"] for item in items]
    assert codes == sorted(codes)
    assert set(codes) == {CODE, *(dataset.code for dataset in others)}


async def test_list_datasets_filtered_by_status_returns_only_that_status() -> None:
    world = World()
    retired = DatasetTestFactory.build(
        code="api_test_old", status=DatasetStatus.RETIRED
    )
    api = world.app(retired)

    async with api.client() as client:
        response = await client.get(f"{DATASETS}?status=retired")

    assert [item["code"] for item in response.json()["items"]] == ["api_test_old"]


async def test_list_datasets_with_rejected_token_returns_401() -> None:
    api = World().app()

    async with api.client() as client:
        response = await client.get(
            DATASETS, headers={"Authorization": "Bearer not-a-token"}
        )

    assert response.status_code == 401


async def test_get_dataset_anonymous_returns_detail_with_versions_and_etag() -> None:
    world = World()
    api = world.app()

    async with api.client() as client:
        response = await client.get(f"{DATASETS}/{CODE}")

    body = response.json()
    assert response.status_code == 200
    assert response.headers["etag"] == make_etag(
        world.dataset.version, world.dataset.id
    )
    assert body["licence"]["spdx_id"] == "CC-BY-4.0"
    assert [version["id"] for version in body["recent_versions"]] == [
        str(world.version.id)
    ]


async def test_get_unknown_dataset_returns_404_without_echo() -> None:
    api = World().app()

    async with api.client() as client:
        response = await client.get(f"{DATASETS}/api_test_missing")

    assert response.status_code == 404
    assert "api_test_missing" not in response.text


async def test_get_dataset_with_malformed_code_returns_422_without_echo() -> None:
    api = World().app()

    async with api.client() as client:
        response = await client.get(f"{DATASETS}/Bad Code!")

    assert response.status_code == 422
    assert "Bad Code" not in response.text


async def test_list_runs_of_dataset_omits_who_triggered_them() -> None:
    world = World()
    api = world.app()

    async with api.client() as client:
        response = await client.get(f"{DATASETS}/{CODE}/runs")

    items = response.json()["items"]
    assert [item["id"] for item in items] == [str(world.run.id)]
    assert "triggered_by" not in items[0]
    assert str(TRIGGERED_BY) not in response.text


async def test_list_runs_of_unknown_dataset_returns_404() -> None:
    api = World().app()

    async with api.client() as client:
        response = await client.get(f"{DATASETS}/api_test_missing/runs")

    assert response.status_code == 404


async def test_list_runs_walks_every_page() -> None:
    world = World()
    extra = [
        IngestionRunTestFactory.build(dataset_version_id=world.version.id)
        for _ in range(2)
    ]
    api = build_test_app(
        ingestion=InMemoryIngestionUnitOfWork(
            datasets=[world.dataset],
            versions=[world.version],
            runs=[world.run, *extra],
        )
    )

    async with api.client() as client:
        items = await walk(client, f"{DATASETS}/{CODE}/runs?limit=1")

    assert {item["id"] for item in items} == {
        str(run.id) for run in (world.run, *extra)
    }


async def test_get_run_anonymous_returns_report_without_trigger() -> None:
    world = World()
    api = world.app()

    async with api.client() as client:
        response = await client.get(f"{RUNS}/{world.run.id}")

    body = response.json()
    assert response.status_code == 200
    assert body["report"]["issues"] == []
    assert "triggered_by" not in body


async def test_get_unknown_run_returns_404() -> None:
    api = World().app()

    async with api.client() as client:
        response = await client.get(f"{RUNS}/{UNKNOWN_ID}")

    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Observations                                                                #
# --------------------------------------------------------------------------- #


async def test_query_observations_anonymous_walks_the_series_in_time_order() -> None:
    world = World()
    api = world.app()
    query = f"dataset={CODE}&variable=air_temperature&from={WINDOW['from']}"

    async with api.client() as client:
        items = await walk(client, f"{OBSERVATIONS}?{query}&to={WINDOW['to']}&limit=2")

    assert [item["observed_at"]["value"] for item in items] == [
        "2026-01-01T00:00:00Z",
        "2026-01-01T01:00:00Z",
        "2026-01-01T02:00:00Z",
    ]
    assert {item["site_ref"] for item in items} == {"station:API-0001"}
    assert {item["value"]["unit"] for item in items} == {"kelvin"}


async def test_query_observations_of_one_site_and_version_filters() -> None:
    world = World()
    api = world.app()

    async with api.client() as client:
        response = await client.get(
            OBSERVATIONS,
            params={
                "dataset": CODE,
                "variable": "air_temperature",
                **WINDOW,
                "site_ref": "station:OTHER",
                "version": str(world.version.id),
            },
        )

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


@pytest.mark.parametrize(
    "changes",
    [
        {"variable": "zz_secret_variable"},
        {"from": "2026-01-02T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
        {"from": "2026-01-01T00:00:00"},
        {"limit": "0"},
        {"cursor": "x" * 2000},
        {"unexpected": "1"},
    ],
)
async def test_query_observations_with_invalid_query_returns_422_without_echo(
    changes: dict[str, str],
) -> None:
    api = World().app()
    params = {"dataset": CODE, "variable": "air_temperature", **WINDOW} | changes

    async with api.client() as client:
        response = await client.get(OBSERVATIONS, params=params)

    assert response.status_code == 422
    assert "zz_secret_variable" not in response.text


async def test_query_observations_without_window_returns_422() -> None:
    api = World().app()

    async with api.client() as client:
        response = await client.get(
            OBSERVATIONS, params={"dataset": CODE, "variable": "air_temperature"}
        )

    assert response.status_code == 422


async def test_query_observations_of_unknown_dataset_returns_404() -> None:
    api = World().app()

    async with api.client() as client:
        response = await client.get(
            OBSERVATIONS,
            params={"dataset": "api_test_missing", "variable": "air_temperature"}
            | WINDOW,
        )

    assert response.status_code == 404


async def test_query_observations_with_tampered_cursor_returns_422() -> None:
    api = World().app()

    async with api.client() as client:
        response = await client.get(
            OBSERVATIONS,
            params={"dataset": CODE, "variable": "air_temperature", "cursor": "abc"}
            | WINDOW,
        )

    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Rasters                                                                     #
# --------------------------------------------------------------------------- #


async def test_list_raster_assets_anonymous_returns_json_newest_first() -> None:
    world = World()
    api = world.app()

    async with api.client() as client:
        response = await client.get(RASTERS)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["vary"] == "Accept"
    assert [item["id"] for item in response.json()["items"]] == [
        str(asset.id) for asset in world.rasters
    ]


@pytest.mark.parametrize(
    ("query", "headers"),
    [("?format=geojson", {}), ("", {"Accept": GEOJSON})],
)
async def test_list_raster_assets_negotiated_returns_stac_feature_collection(
    query: str, headers: dict[str, str]
) -> None:
    world = World()
    api = world.app()

    async with api.client() as client:
        response = await client.get(f"{RASTERS}{query}", headers=headers)

    body = response.json()
    item = body["features"][0]
    assert response.status_code == 200
    assert response.headers["content-type"] == GEOJSON
    assert response.headers["vary"] == "Accept"
    assert body["type"] == "FeatureCollection"
    assert item["type"] == "Feature"
    assert item["stac_version"].startswith("1.")
    assert item["id"] == world.rasters[0].stac_id
    assert item["geometry"]["type"] == "Polygon"
    assert len(item["bbox"]) == 4
    assert item["properties"]["datetime"].startswith("2026-01-03T00:00:00")
    assert item["properties"]["platform"] == "test-platform"
    assert set(item["assets"]) == {"data"}


async def test_list_raster_assets_format_json_wins_over_accept() -> None:
    api = World().app()

    async with api.client() as client:
        response = await client.get(
            f"{RASTERS}?format=json", headers={"Accept": GEOJSON}
        )

    assert response.headers["content-type"] == "application/json"


async def test_list_raster_assets_geojson_pages_through_link_only() -> None:
    world = World()
    api = world.app()

    async with api.client() as client:
        first = await client.get(f"{RASTERS}?format=geojson&limit=1")
        second = await client.get(str(next_url(first)))

    assert "next_cursor" not in first.json()
    assert "format=geojson" in first.headers["link"]
    assert [feature["id"] for feature in second.json()["features"]] == [
        world.rasters[1].stac_id
    ]
    assert "link" not in second.headers


async def test_list_raster_assets_filters_by_bbox_window_and_dataset() -> None:
    world = World()
    api = world.app()

    async with api.client() as client:
        outside = await client.get(f"{RASTERS}?bbox=10,10,11,11")
        early = await client.get(
            RASTERS,
            params={
                "from": "2026-01-01T00:00:00Z",
                "to": "2026-01-02T12:00:00Z",
                "dataset": CODE,
                "bbox": "74.2,36.2,74.8,36.8",
            },
        )

    assert outside.json()["items"] == []
    assert [item["id"] for item in early.json()["items"]] == [str(world.rasters[1].id)]


@pytest.mark.parametrize(
    "query",
    [
        "bbox=75,36,74,37",
        "bbox=1,2,3",
        "bbox=200,0,201,1",
        "from=2026-01-02T00:00:00Z&to=2026-01-01T00:00:00Z",
        "format=png",
    ],
)
async def test_list_raster_assets_with_invalid_query_returns_422(query: str) -> None:
    api = World().app()

    async with api.client() as client:
        response = await client.get(f"{RASTERS}?{query}")

    assert response.status_code == 422
    assert "png" not in response.text


async def test_list_raster_assets_of_unknown_dataset_returns_404() -> None:
    api = World().app()

    async with api.client() as client:
        response = await client.get(f"{RASTERS}?dataset=api_test_missing")

    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Administration                                                              #
# --------------------------------------------------------------------------- #


def dataset_body(**changes: object) -> dict[str, Any]:
    """Return a registration body with a licence."""
    details = {
        "code": "api_test_new",
        "title": "Synthetic new dataset",
        "publisher": "Synthetic publisher",
        "licence": LICENCE,
        "update_frequency": "daily",
    } | changes
    return {"details": details}


async def test_register_dataset_as_admin_returns_201_with_location_and_etag() -> None:
    api = World().app()

    async with api.client() as client:
        response = await client.post(
            f"{ADMIN}/datasets", json=dataset_body(), headers=admin()
        )
        listed = await client.get(f"{DATASETS}/api_test_new")

    body = response.json()
    assert response.status_code == 201
    assert response.headers["location"] == f"{DATASETS}/api_test_new"
    assert response.headers["etag"] == make_etag(body["version"], UUID(body["id"]))
    assert listed.status_code == 200


async def test_register_dataset_without_licence_returns_409_invariant() -> None:
    api = World().app()
    body = dataset_body()
    del body["details"]["licence"]

    async with api.client() as client:
        response = await client.post(f"{ADMIN}/datasets", json=body, headers=admin())

    assert response.status_code == 409
    assert response.json()["type"].endswith("/invariant-violation")


async def test_register_dataset_with_taken_code_returns_409() -> None:
    api = World().app()

    async with api.client() as client:
        response = await client.post(
            f"{ADMIN}/datasets", json=dataset_body(code=CODE), headers=admin()
        )

    assert response.status_code == 409


@pytest.mark.parametrize("headers", [{}, "citizen", "moderator"])
async def test_admin_routes_refuse_non_administrators(
    headers: dict[str, str] | str,
) -> None:
    api = World().app()
    resolved = {"citizen": citizen(), "moderator": moderator()}.get(str(headers), {})
    expected = 401 if resolved == {} else 403

    async with api.client() as client:
        responses = [
            await client.post(
                f"{ADMIN}/datasets", json=dataset_body(), headers=resolved
            ),
            await client.post(
                f"{ADMIN}/datasets/api_test_missing/status",
                json={"status": "retired"},
                headers=resolved,
            ),
        ]

    assert [response.status_code for response in responses] == [expected, expected]
    assert "api_test_missing" not in responses[1].text


async def test_register_dataset_with_invalid_body_returns_422_without_echo() -> None:
    api = World().app()

    async with api.client() as client:
        response = await client.post(
            f"{ADMIN}/datasets",
            json=dataset_body(code="<script>", extra_field="x"),
            headers=admin(),
        )

    assert response.status_code == 422
    assert "<script>" not in response.text


async def test_record_dataset_version_as_admin_returns_201() -> None:
    api = World().app()
    body = {
        "details": {
            "label": "v-api-2",
            "retrieved_at": {"value": "2026-09-01T00:00:00Z", "precision": "day"},
            "input_checksum": "b" * 64,
        }
    }

    async with api.client() as client:
        response = await client.post(
            f"{ADMIN}/datasets/{CODE}/versions", json=body, headers=admin()
        )
        detail = await client.get(f"{DATASETS}/{CODE}")

    assert response.status_code == 201
    assert response.json()["label"] == "v-api-2"
    assert "v-api-2" in {
        version["label"] for version in detail.json()["recent_versions"]
    }


async def test_record_dataset_version_of_unknown_dataset_returns_404() -> None:
    api = World().app()
    body = {
        "details": {
            "label": "v1",
            "retrieved_at": {"value": "2026-09-01T00:00:00Z", "precision": "day"},
            "input_checksum": "b" * 64,
        }
    }

    async with api.client() as client:
        response = await client.post(
            f"{ADMIN}/datasets/api_test_missing/versions", json=body, headers=admin()
        )

    assert response.status_code == 404


async def test_deprecate_dataset_with_current_if_match_returns_new_etag() -> None:
    world = World()
    api = world.app()
    current = make_etag(world.dataset.version, world.dataset.id)

    async with api.client() as client:
        response = await client.post(
            f"{ADMIN}/datasets/{CODE}/status",
            json={"status": "deprecated"},
            headers=admin() | {"If-Match": current},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "deprecated"
    assert response.headers["etag"] == make_etag(body["version"], world.dataset.id)
    assert response.headers["etag"] != current


async def test_retire_dataset_without_if_match_returns_retired() -> None:
    api = World().app()

    async with api.client() as client:
        response = await client.post(
            f"{ADMIN}/datasets/{CODE}/status",
            json={"status": "retired"},
            headers=admin(),
        )

    assert response.status_code == 200
    assert response.json()["status"] == "retired"


async def test_change_dataset_status_with_stale_if_match_returns_412() -> None:
    world = World()
    api = world.app()
    stale = make_etag(world.dataset.version + 5, world.dataset.id)

    async with api.client() as client:
        response = await client.post(
            f"{ADMIN}/datasets/{CODE}/status",
            json={"status": "retired"},
            headers=admin() | {"If-Match": stale},
        )

    assert response.status_code == 412


async def test_change_dataset_status_to_active_returns_422() -> None:
    api = World().app()

    async with api.client() as client:
        response = await client.post(
            f"{ADMIN}/datasets/{CODE}/status",
            json={"status": "active"},
            headers=admin(),
        )

    assert response.status_code == 422


async def test_request_run_as_admin_returns_202_with_location_and_task() -> None:
    world = World()
    api = world.app()

    async with api.client() as client:
        response = await client.post(
            f"{ADMIN}/datasets/{CODE}/runs",
            json={
                "version_id": str(world.version.id),
                "adapter_name": TEST_ADAPTER_NAME,
            },
            headers=admin(),
        )

    body = response.json()
    tasks = api.task_queue.of(INGESTION_RUN_TASK)
    assert response.status_code == 202
    assert response.headers["location"] == f"{RUNS}/{body['id']}"
    assert body["status"] == "pending"
    assert "triggered_by" not in body
    assert len(tasks) == 1
    assert str(body["id"]) in {str(value) for value in tasks[0].payload.values()}


async def test_request_run_with_unknown_adapter_returns_404() -> None:
    world = World()
    api = world.app()

    async with api.client() as client:
        response = await client.post(
            f"{ADMIN}/datasets/{CODE}/runs",
            json={"version_id": str(world.version.id), "adapter_name": "no_adapter"},
            headers=admin(),
        )

    assert response.status_code == 404
    assert api.task_queue.of(INGESTION_RUN_TASK) == []


async def test_request_run_of_version_of_another_dataset_is_refused() -> None:
    world = World()
    other = DatasetTestFactory.build(code="api_test_other")
    api = world.app(other)

    async with api.client() as client:
        response = await client.post(
            f"{ADMIN}/datasets/api_test_other/runs",
            json={
                "version_id": str(world.version.id),
                "adapter_name": TEST_ADAPTER_NAME,
            },
            headers=admin(),
        )

    assert response.status_code == 409
    assert api.task_queue.of(INGESTION_RUN_TASK) == []


def raster_body(world: World) -> dict[str, Any]:
    """Return a catalogue body for one synthetic scene."""
    return {
        "dataset": CODE,
        "version_id": str(world.version.id),
        "description": {
            "stac_id": "api-test-scene",
            "footprint": {
                "geojson": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [74.0, 36.0],
                            [75.0, 36.0],
                            [75.0, 37.0],
                            [74.0, 37.0],
                            [74.0, 36.0],
                        ]
                    ],
                }
            },
            "acquired_at": {"value": "2026-02-01T05:30:00Z", "precision": "exact"},
            "platform": "test-platform",
            "assets": [
                {
                    "key": "data",
                    "href": "s3://rasters/test/api.tif",
                    "media_type": (
                        "image/tiff; application=geotiff; profile=cloud-optimized"
                    ),
                    "roles": ["data"],
                }
            ],
        },
    }


async def test_catalogue_raster_asset_as_admin_returns_201_and_is_listed() -> None:
    world = World()
    api = world.app()

    async with api.client() as client:
        response = await client.post(
            f"{ADMIN}/raster-assets", json=raster_body(world), headers=admin()
        )
        listed = await client.get(RASTERS)

    assert response.status_code == 201
    assert response.json()["stac_id"] == "api-test-scene"
    assert response.json()["id"] in {item["id"] for item in listed.json()["items"]}


async def test_catalogue_raster_asset_with_javascript_href_returns_422() -> None:
    world = World()
    api = world.app()
    body = raster_body(world)
    body["description"]["assets"][0]["href"] = "javascript:alert(1)"

    async with api.client() as client:
        response = await client.post(
            f"{ADMIN}/raster-assets", json=body, headers=admin()
        )

    assert response.status_code == 422
    assert "javascript:" not in response.text


async def test_catalogue_raster_asset_of_unknown_dataset_returns_404() -> None:
    world = World()
    api = world.app()
    body = raster_body(world) | {"dataset": "api_test_missing"}

    async with api.client() as client:
        response = await client.post(
            f"{ADMIN}/raster-assets", json=body, headers=admin()
        )

    assert response.status_code == 404
