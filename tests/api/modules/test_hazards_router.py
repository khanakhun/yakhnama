"""HTTP tests for ``/api/v1/hazard-types``."""

from tests.factories.hazards import HazardTypeTestFactory
from tests.fakes.api import auth_headers, build_test_app
from tests.fakes.auth import DEFAULT_ISSUED_AT
from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.hazards.domain.entities import HazardType
from yakhnama.modules.hazards.domain.value_objects import RetirementReason
from yakhnama.platform.etag import make_etag

HAZARD_TYPES = "/api/v1/hazard-types"


def _retired(hazard_type: HazardType) -> HazardType:
    # After every factory timestamp, so the retirement is never before creation.
    change = hazard_type.retire(
        RetirementReason(text="Test retirement"),
        clock=FrozenClock(DEFAULT_ISSUED_AT),
        ids=SequentialIdGenerator(seed=7),
    )
    return change.state


async def test_list_hazard_types_anonymous_returns_active_types_by_code() -> None:
    glof = HazardTypeTestFactory.build(code="glof")
    avalanche = HazardTypeTestFactory.build(code="avalanche")
    api = build_test_app(hazard_types=[glof, avalanche])

    async with api.client() as client:
        response = await client.get(HAZARD_TYPES)

    assert response.status_code == 200
    assert [item["code"] for item in response.json()["items"]] == ["avalanche", "glof"]
    assert response.json()["next_cursor"] is None
    assert "link" not in response.headers


async def test_list_hazard_types_include_retired_returns_retired_type() -> None:
    retired = _retired(HazardTypeTestFactory.build(code="old_flood"))
    api = build_test_app(hazard_types=[retired])

    async with api.client() as client:
        default = await client.get(HAZARD_TYPES)
        everything = await client.get(HAZARD_TYPES, params={"include_retired": True})

    assert default.json()["items"] == []
    assert [item["status"] for item in everything.json()["items"]] == ["retired"]


async def test_list_hazard_types_walk_with_cursor_visits_every_type_once() -> None:
    codes = ["code_a", "code_b", "code_c"]
    api = build_test_app(
        hazard_types=[HazardTypeTestFactory.build(code=code) for code in codes]
    )

    async with api.client() as client:
        first = await client.get(HAZARD_TYPES, params={"limit": 2})
        second = await client.get(
            HAZARD_TYPES, params={"limit": 2, "cursor": first.json()["next_cursor"]}
        )

    seen = [item["code"] for page in (first, second) for item in page.json()["items"]]
    assert seen == codes
    assert first.headers["link"].endswith('rel="next"')
    assert "cursor=" in first.headers["link"]
    assert second.json()["next_cursor"] is None


async def test_list_hazard_types_with_limit_above_max_returns_422_problem() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.get(HAZARD_TYPES, params={"limit": 201})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "input" not in response.json()["errors"][0]


async def test_list_hazard_types_with_unknown_parameter_returns_422() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.get(HAZARD_TYPES, params={"offset": 10})

    assert response.status_code == 422


async def test_list_hazard_types_with_corrupt_cursor_returns_422_problem() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.get(HAZARD_TYPES, params={"cursor": "not-a-cursor"})

    assert response.status_code == 422
    assert "not-a-cursor" not in response.text


async def test_get_hazard_type_anonymous_returns_detail_with_etag() -> None:
    glof = HazardTypeTestFactory.build(code="glof")
    api = build_test_app(hazard_types=[glof])

    async with api.client() as client:
        response = await client.get(f"{HAZARD_TYPES}/glof")

    assert response.status_code == 200
    assert response.json()["code"] == "glof"
    assert response.headers["etag"] == make_etag(glof.version, glof.id)


async def test_get_hazard_type_when_missing_returns_404_problem() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.get(f"{HAZARD_TYPES}/unknown_code")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["type"].endswith("/not-found")


async def test_get_hazard_type_with_invalid_code_returns_422() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.get(f"{HAZARD_TYPES}/Not-A-Code")

    assert response.status_code == 422


async def test_get_hazard_type_with_rejected_token_returns_401_problem() -> None:
    api = build_test_app(hazard_types=[HazardTypeTestFactory.build(code="glof")])

    async with api.client() as client:
        response = await client.get(
            f"{HAZARD_TYPES}/glof", headers={"Authorization": "Bearer not-a-jwt"}
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


async def test_list_hazard_types_authenticated_returns_200() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.get(HAZARD_TYPES, headers=auth_headers())

    assert response.status_code == 200
