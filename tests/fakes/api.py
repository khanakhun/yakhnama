"""A real ``create_app`` application wired to in-memory fakes, for HTTP tests.

``build_test_app`` replaces every port the routers reach in the production
container (``build_container``, which does no I/O) with a fake: the module units of
work and query services, the token validator (backed by the session's in-memory
signing key), the rate limiter and the idempotency store. Middlewares, exception
handlers and routers are the production ones, so a test exercises the whole HTTP
stack without a database or a network.

``auth_headers`` returns an ``Authorization`` header with a token signed by the same
key, so the validator accepts it::

    api = build_test_app(hazard_types=[HazardTypeTestFactory.build()])
    async with api.client() as client:
        response = await client.get("/api/v1/me", headers=auth_headers())

Patterns: Fake.
"""

import dataclasses
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from typing import Final

import httpx
from fastapi import FastAPI

from tests.fakes.auth import (
    DEFAULT_ISSUED_AT,
    TEST_AUDIENCE,
    TEST_ISSUER,
    FakeJwksClient,
    InMemoryIdempotencyStore,
    StaticRateLimiter,
    access_token_claims,
    issue_token,
    session_key_pair,
)
from tests.fakes.clock import FrozenClock
from tests.fakes.geography import (
    InMemoryGeographyUnitOfWork,
    InMemoryPlaceQueryService,
)
from tests.fakes.hazards import (
    InMemoryHazardsUnitOfWork,
    InMemoryHazardTypeQueryService,
)
from tests.fakes.identity import (
    InMemoryIdentityQueryService,
    InMemoryIdentityUnitOfWork,
)
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.impacts import (
    InMemoryImpactMetricQueryService,
    InMemoryImpactsUnitOfWork,
)
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.main import create_app
from yakhnama.modules.geography.domain.entities import Place
from yakhnama.modules.hazards.domain.entities import HazardType
from yakhnama.modules.identity.domain.entities import Membership, Organization, User
from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.platform.auth.tokens import TokenValidator
from yakhnama.platform.container import build_container
from yakhnama.platform.ratelimit.limiter import RateLimiter
from yakhnama.platform.settings import Settings

TEST_SUBJECT: Final = "api-test-subject"
"""The ``sub`` of ``auth_headers`` tokens unless told otherwise."""
TEST_BASE_URL: Final = "http://localhost"


def harness_settings() -> Settings:
    """Return settings that ignore the environment and any ``.env`` file.

    Returns:
        Test settings; OIDC stays unset because the validator is replaced.
    """
    return Settings(_env_file=None, environment="test", log_format="console")


# A frozen dataclass, like the container it wraps: it holds live fakes to arrange
# and inspect, not data that is validated or serialised.
@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ApiHarness:
    """The wired application and the fakes behind it.

    Implements: Fake (of the composition root's bindings).

    Attributes:
        app: The application built by ``create_app``.
        hazards: The hazards unit of work; its repositories hold the data.
        impacts: The impacts unit of work.
        geography: The geography unit of work.
        identity: The identity unit of work.
        idempotency_store: The idempotency store the middleware uses.
        rate_limiter: The rate limiter the middleware uses.
        clock: The clock of the container and the token validator.
    """

    app: FastAPI
    hazards: InMemoryHazardsUnitOfWork
    impacts: InMemoryImpactsUnitOfWork
    geography: InMemoryGeographyUnitOfWork
    identity: InMemoryIdentityUnitOfWork
    idempotency_store: InMemoryIdempotencyStore
    rate_limiter: RateLimiter
    clock: FrozenClock

    @asynccontextmanager
    async def client(self) -> AsyncIterator[httpx.AsyncClient]:
        """Yield an HTTP client calling the app in process.

        Yields:
            The client, with a trusted ``Host``.
        """
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(
            transport=transport, base_url=TEST_BASE_URL
        ) as client:
            yield client


def build_test_app(  # noqa: PLR0913  # reason: one optional seed per fake repository
    *,
    settings: Settings | None = None,
    hazard_types: Iterable[HazardType] = (),
    impact_metrics: Iterable[ImpactMetric] = (),
    places: Iterable[Place] = (),
    users: Iterable[User] = (),
    organizations: Iterable[Organization] = (),
    memberships: Iterable[Membership] = (),
    rate_limiter: RateLimiter | None = None,
) -> ApiHarness:
    """Build the app with every port the routers use bound to a fake.

    Args:
        settings: The settings; ``harness_settings()`` when ``None``.
        hazard_types: Hazard types that exist before the test acts.
        impact_metrics: Impact metrics that exist before the test acts.
        places: Places that exist before the test acts.
        users: Users that exist before the test acts.
        organizations: Organisations that exist before the test acts.
        memberships: Memberships that exist before the test acts.
        rate_limiter: The limiter; an always-allowing ``StaticRateLimiter`` when
            ``None``.

    Returns:
        The application and its fakes.
    """
    resolved_settings = settings if settings is not None else harness_settings()
    clock = FrozenClock(DEFAULT_ISSUED_AT)
    hazards = InMemoryHazardsUnitOfWork(hazard_types)
    impacts = InMemoryImpactsUnitOfWork(impact_metrics)
    geography = InMemoryGeographyUnitOfWork(places)
    identity = InMemoryIdentityUnitOfWork(
        users=users, organizations=organizations, memberships=memberships
    )
    idempotency_store = InMemoryIdempotencyStore()
    limiter = rate_limiter if rate_limiter is not None else StaticRateLimiter()
    container = dataclasses.replace(
        build_container(resolved_settings),
        clock=clock,
        id_generator=SequentialIdGenerator(),
        token_validator=TokenValidator(
            jwks_client=FakeJwksClient([session_key_pair()]),
            issuer=TEST_ISSUER,
            audience=TEST_AUDIENCE,
            algorithms=["RS256"],
            leeway_seconds=0,
            clock=clock,
        ),
        rate_limiter=limiter,
        idempotency_store=idempotency_store,
        hazards_uow_factory=InMemoryUnitOfWorkFactory(hazards),
        impacts_uow_factory=InMemoryUnitOfWorkFactory(impacts),
        geography_uow_factory=InMemoryUnitOfWorkFactory(geography),
        identity_uow_factory=InMemoryUnitOfWorkFactory(identity),
        hazard_type_query_service=InMemoryHazardTypeQueryService(hazards.hazard_types),
        impact_metric_query_service=InMemoryImpactMetricQueryService(
            impacts.impact_metrics
        ),
        place_query_service=InMemoryPlaceQueryService(geography.places),
        identity_query_service=InMemoryIdentityQueryService(identity),
    )
    return ApiHarness(
        app=create_app(resolved_settings, container),
        hazards=hazards,
        impacts=impacts,
        geography=geography,
        identity=identity,
        idempotency_store=idempotency_store,
        rate_limiter=limiter,
        clock=clock,
    )


def auth_headers(
    *,
    subject: str = TEST_SUBJECT,
    roles: Iterable[str] = (),
    name: str | None = None,
) -> dict[str, str]:
    """Return an ``Authorization`` header the test app's validator accepts.

    Args:
        subject: The token's ``sub``; one subject is one mirrored user.
        roles: Realm role names, for example ``["moderator"]``.
        name: The ``name`` claim, when given.

    Returns:
        ``{"Authorization": "Bearer <token>"}``.
    """
    claims = access_token_claims(subject=subject, realm_roles=roles, name=name)
    return {"Authorization": f"Bearer {issue_token(claims, session_key_pair())}"}
