"""HTTP tests for ``/api/v1/impact-metrics``."""

from tests.factories.impacts import ImpactMetricTestFactory
from tests.fakes.api import build_test_app
from yakhnama.modules.impacts.public import MetricCategory
from yakhnama.platform.etag import make_etag

IMPACT_METRICS = "/api/v1/impact-metrics"


async def test_list_impact_metrics_anonymous_returns_metrics_by_code() -> None:
    api = build_test_app(
        impact_metrics=[
            ImpactMetricTestFactory.build(code="deaths"),
            ImpactMetricTestFactory.build(code="bridges_destroyed"),
        ]
    )

    async with api.client() as client:
        response = await client.get(IMPACT_METRICS)

    assert response.status_code == 200
    codes = [item["code"] for item in response.json()["items"]]
    assert codes == ["bridges_destroyed", "deaths"]


async def test_list_impact_metrics_by_category_returns_only_that_category() -> None:
    categories = list(MetricCategory)
    wanted, other = categories[0], categories[1]
    api = build_test_app(
        impact_metrics=[
            ImpactMetricTestFactory.build(code="metric_a", category=wanted),
            ImpactMetricTestFactory.build(code="metric_b", category=other),
        ]
    )

    async with api.client() as client:
        response = await client.get(IMPACT_METRICS, params={"category": wanted.value})

    assert [item["code"] for item in response.json()["items"]] == ["metric_a"]


async def test_list_impact_metrics_with_unknown_category_returns_422() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.get(IMPACT_METRICS, params={"category": "nonsense"})

    assert response.status_code == 422
    assert "nonsense" not in response.text


async def test_list_impact_metrics_walk_with_cursor_follows_link() -> None:
    api = build_test_app(
        impact_metrics=[
            ImpactMetricTestFactory.build(code=f"metric_{index}") for index in range(3)
        ]
    )

    async with api.client() as client:
        first = await client.get(IMPACT_METRICS, params={"limit": 2})
        link = first.headers["link"].split(";")[0].strip("<>")
        second = await client.get(link)

    seen = [item["code"] for page in (first, second) for item in page.json()["items"]]
    assert seen == ["metric_0", "metric_1", "metric_2"]
    assert "limit=2" in link
    assert second.json()["next_cursor"] is None


async def test_get_impact_metric_anonymous_returns_detail_with_etag() -> None:
    metric = ImpactMetricTestFactory.build(code="deaths")
    api = build_test_app(impact_metrics=[metric])

    async with api.client() as client:
        response = await client.get(f"{IMPACT_METRICS}/deaths")

    assert response.status_code == 200
    assert response.json()["id"] == str(metric.id)
    assert response.headers["etag"] == make_etag(metric.version, metric.id)


async def test_get_impact_metric_when_missing_returns_404_problem() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.get(f"{IMPACT_METRICS}/unknown_metric")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
