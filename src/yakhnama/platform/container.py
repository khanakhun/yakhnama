"""The composition root for infrastructure: one ``Container`` per application.

``build_container`` binds the kernel ports to their adapters (``Clock`` to
``SystemClock``, ``IdGenerator`` to ``Uuid7Generator``) and builds the engine, session
factory, outbox and unit-of-work factory from ``Settings``. ``yakhnama.main`` stores
the container on ``app.state.container``; routes reach it through the ``get_container``
dependency. Only this module and ``yakhnama.main`` bind ports to adapters
(``AGENTS.md`` §2.1).

Each module's ports are bound here too: its ``UnitOfWorkFactory`` port to
``SqlAlchemyUnitOfWorkFactory(<its SQLAlchemy unit of work>, ...)``, its query service
port to the SQLAlchemy query service, and (from later phases) its outbox subscribers
on ``subscriber_registry``. Importing module infrastructure is what a composition root
is for; nothing inside a module imports this file. ``build_seed_handler`` wires the
reference-data seed used by ``python -m yakhnama.seed``.

``build_task_handlers`` binds background task names to handlers (ADR 0008); see
``yakhnama.platform.tasks.handlers`` for the contract a handler follows.

Patterns: Composition Root, Dependency Injection.
"""

import dataclasses
from collections.abc import Mapping
from datetime import timedelta
from types import MappingProxyType

import httpx
import structlog
from fastapi import Request
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from taskiq import AsyncBroker

from yakhnama.modules.geography.application.handlers import (
    LoadReferencePlacesHandler,
)
from yakhnama.modules.geography.application.ports import (
    GeographyUnitOfWorkFactory,
    PlaceQueryService,
)
from yakhnama.modules.geography.infrastructure.queries import (
    SqlAlchemyPlaceQueryService,
)
from yakhnama.modules.geography.infrastructure.uow import (
    SqlAlchemyGeographyUnitOfWork,
)
from yakhnama.modules.hazards.application.handlers import (
    LoadReferenceHazardTypesHandler,
)
from yakhnama.modules.hazards.application.ports import (
    HazardsUnitOfWorkFactory,
    HazardTypeQueryService,
)
from yakhnama.modules.hazards.infrastructure.queries import (
    SqlAlchemyHazardTypeQueryService,
)
from yakhnama.modules.hazards.infrastructure.uow import SqlAlchemyHazardsUnitOfWork
from yakhnama.modules.identity.application.ports import (
    IdentityQueryService,
    IdentityUnitOfWorkFactory,
)
from yakhnama.modules.identity.infrastructure.queries import (
    SqlAlchemyIdentityQueryService,
)
from yakhnama.modules.identity.infrastructure.uow import SqlAlchemyIdentityUnitOfWork
from yakhnama.modules.identity.public import CanManageReferenceData
from yakhnama.modules.impacts.application.handlers import (
    LoadReferenceImpactMetricsHandler,
)
from yakhnama.modules.impacts.application.ports import (
    ImpactMetricQueryService,
    ImpactsUnitOfWorkFactory,
)
from yakhnama.modules.impacts.infrastructure.queries import (
    SqlAlchemyImpactMetricQueryService,
)
from yakhnama.modules.impacts.infrastructure.uow import SqlAlchemyImpactsUnitOfWork
from yakhnama.platform.auth.jwks import HttpJwksClient
from yakhnama.platform.auth.tokens import TokenValidator
from yakhnama.platform.db import create_engine, create_session_factory
from yakhnama.platform.idempotency.sqlalchemy_store import SqlAlchemyIdempotencyStore
from yakhnama.platform.idempotency.store import IdempotencyStore
from yakhnama.platform.outbox.relay import OutboxRelay, SubscriberRegistry
from yakhnama.platform.outbox.sqlalchemy_store import SqlAlchemyOutboxStore
from yakhnama.platform.outbox.store import OutboxStore
from yakhnama.platform.outbox.writer import OutboxWriter
from yakhnama.platform.ratelimit.limiter import InMemoryRateLimiter, RateLimiter
from yakhnama.platform.ratelimit.redis_limiter import RedisRateLimiter
from yakhnama.platform.settings import Settings
from yakhnama.platform.tasks.broker import build_broker
from yakhnama.platform.tasks.handlers import (
    IDEMPOTENCY_PURGE_TASK,
    OUTBOX_PURGE_TASK,
    OUTBOX_RELAY_TASK,
    TaskHandler,
    TaskHandlerRegistry,
)
from yakhnama.platform.tasks.taskiq_adapter import TaskiqTaskQueue, register_tasks
from yakhnama.platform.uow import SqlAlchemyUnitOfWork, SqlAlchemyUnitOfWorkFactory
from yakhnama.seed.application import SeedReferenceDataHandler
from yakhnama.seed.infrastructure import YamlReferenceFileReader
from yakhnama.shared_kernel.clock import Clock, SystemClock
from yakhnama.shared_kernel.ids import IdGenerator, Uuid7Generator
from yakhnama.shared_kernel.tasks import ScheduledTask, TaskQueue


# A frozen dataclass rather than a Pydantic model: the container holds live resources
# (an engine with a connection pool, factories), not data, and nothing about it is
# validated or serialised.
@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Container:
    """Every long-lived dependency of one application instance.

    Implements: Composition Root.

    Attributes:
        settings: The settings everything below was built from.
        clock: The ``Clock`` port bound for this application.
        id_generator: The ``IdGenerator`` port bound for this application.
        engine: The async database engine; disposed when the application stops.
        session_factory: Opens one ``AsyncSession`` per unit of work.
        outbox_writer: Stages domain events as outbox rows on commit.
        uow_factory: Opens a plain ``SqlAlchemyUnitOfWork``; modules bind their own
            factories next to it.
        subscriber_registry: Outbox subscribers per ``event_type``.
        outbox_store: The ``OutboxStore`` port (PostgreSQL) the relay records on.
        outbox_relay: Delivers pending outbox messages to ``subscriber_registry``.
        geography_uow_factory: The geography ``UnitOfWorkFactory`` port.
        hazards_uow_factory: The hazards ``UnitOfWorkFactory`` port.
        impacts_uow_factory: The impacts ``UnitOfWorkFactory`` port.
        place_query_service: The geography ``PlaceQueryService`` port.
        hazard_type_query_service: The hazards ``HazardTypeQueryService`` port.
        impact_metric_query_service: The impacts ``ImpactMetricQueryService`` port.
        identity_uow_factory: The identity ``UnitOfWorkFactory`` port.
        identity_query_service: The identity ``IdentityQueryService`` port.
        token_validator: Checks bearer tokens; ``None`` when ``oidc_issuer`` is not
            set, in which case every protected route answers 401.
        rate_limiter: The ``RateLimiter`` port (in memory or Redis).
        idempotency_store: The ``IdempotencyStore`` port (PostgreSQL).
        task_broker: The Taskiq broker ``task_queue`` sends to; shut down on close.
        task_handlers: Handlers for tasks that run in this process (with the
            ``memory`` backend, every task); bound by ``build_container``.
        task_queue: The ``TaskQueue`` port (Taskiq).
        http_client: The HTTP client of the JWKS client, closed on shutdown;
            ``None`` without OIDC.
        redis: The Redis client of the rate limiter, closed on shutdown; ``None``
            with the in-memory backend.
    """

    settings: Settings
    clock: Clock
    id_generator: IdGenerator
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    outbox_writer: OutboxWriter
    uow_factory: SqlAlchemyUnitOfWorkFactory[SqlAlchemyUnitOfWork]
    subscriber_registry: SubscriberRegistry
    outbox_store: OutboxStore
    outbox_relay: OutboxRelay
    geography_uow_factory: GeographyUnitOfWorkFactory
    hazards_uow_factory: HazardsUnitOfWorkFactory
    impacts_uow_factory: ImpactsUnitOfWorkFactory
    place_query_service: PlaceQueryService
    hazard_type_query_service: HazardTypeQueryService
    impact_metric_query_service: ImpactMetricQueryService
    identity_uow_factory: IdentityUnitOfWorkFactory
    identity_query_service: IdentityQueryService
    token_validator: TokenValidator | None
    rate_limiter: RateLimiter
    idempotency_store: IdempotencyStore
    task_broker: AsyncBroker
    task_handlers: TaskHandlerRegistry
    task_queue: TaskQueue
    http_client: httpx.AsyncClient | None = None
    redis: Redis | None = None

    async def aclose(self) -> None:
        """Release every resource the container opened: clients and the engine."""
        await self.task_broker.shutdown()
        if self.http_client is not None:
            await self.http_client.aclose()
        if self.redis is not None:
            await self.redis.aclose()
        await self.engine.dispose()


def build_token_validator(
    settings: Settings, clock: Clock
) -> tuple[TokenValidator | None, httpx.AsyncClient | None]:
    """Bind the bearer-token validator to the configured identity provider.

    No request is made here; the JWKS is fetched on the first token.

    Args:
        settings: Supplies the ``oidc_*`` and ``jwks_cache_ttl_seconds`` settings.
        clock: The JWKS cache clock.

    Returns:
        The validator and the HTTP client it owns, or ``(None, None)`` when
        ``oidc_issuer`` is not set.
    """
    if settings.oidc_issuer is None:
        return None, None
    # No redirects: a JWKS or discovery URL that redirects elsewhere is a
    # misconfiguration or an attack, never something to follow silently.
    http_client = httpx.AsyncClient(
        timeout=settings.oidc_http_timeout_seconds, follow_redirects=False
    )
    jwks_client = HttpJwksClient(
        http_client=http_client,
        clock=clock,
        issuer=settings.oidc_issuer,
        jwks_url=settings.oidc_jwks_url,
        cache_ttl=timedelta(seconds=settings.jwks_cache_ttl_seconds),
    )
    validator = TokenValidator(
        jwks_client=jwks_client,
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        algorithms=settings.oidc_allowed_algorithms,
        leeway_seconds=settings.oidc_leeway_seconds,
        clock=clock,
        roles_claim=settings.oidc_roles_claim,
    )
    return validator, http_client


def build_rate_limiter(
    settings: Settings, clock: Clock
) -> tuple[RateLimiter, Redis | None]:
    """Bind the ``RateLimiter`` port to the configured backend.

    Args:
        settings: Supplies ``rate_limit_backend``, ``redis_url`` and
            ``redis_socket_timeout_seconds``.
        clock: The in-memory limiter's clock.

    Returns:
        The limiter and the Redis client it owns (``None`` for ``memory``).
    """
    if settings.rate_limit_backend == "redis" and settings.redis_url is not None:
        # from_url connects lazily, on the first command. Short timeouts and no
        # retries: the limiter fails open (ADR 0017), so a slow or unreachable
        # Redis must cost each request a bounded fraction of a second, not stall it.
        timeout = settings.redis_socket_timeout_seconds
        redis = Redis.from_url(
            str(settings.redis_url),
            socket_timeout=timeout,
            socket_connect_timeout=timeout,
            retry=Retry(NoBackoff(), 0),
        )
        return RedisRateLimiter(redis), redis
    return InMemoryRateLimiter(clock), None


def build_container(settings: Settings) -> Container:
    """Bind the ports to their production adapters.

    No I/O happens here: the engine connects lazily on first use.

    Args:
        settings: The application settings.

    Returns:
        A container ready to be stored on ``app.state.container``.
    """
    clock = SystemClock()
    id_generator = Uuid7Generator(clock)
    token_validator, http_client = build_token_validator(settings, clock)
    rate_limiter, redis = build_rate_limiter(settings, clock)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    outbox_writer = OutboxWriter(clock)
    subscriber_registry = SubscriberRegistry()
    outbox_store = SqlAlchemyOutboxStore(session_factory)
    task_broker = build_broker(settings)
    task_handlers = TaskHandlerRegistry()
    task_queue = TaskiqTaskQueue(
        task_broker, register_tasks(task_broker, task_handlers)
    )
    container = Container(
        settings=settings,
        clock=clock,
        id_generator=id_generator,
        engine=engine,
        session_factory=session_factory,
        outbox_writer=outbox_writer,
        uow_factory=SqlAlchemyUnitOfWorkFactory(
            SqlAlchemyUnitOfWork,
            session_factory=session_factory,
            outbox_writer=outbox_writer,
        ),
        subscriber_registry=subscriber_registry,
        outbox_store=outbox_store,
        outbox_relay=OutboxRelay(
            subscriber_registry,
            clock,
            outbox_store,
            max_attempts=settings.outbox_max_attempts,
            lease_seconds=settings.outbox_lease_seconds,
            subscriber_timeout_seconds=settings.outbox_subscriber_timeout_seconds,
        ),
        geography_uow_factory=SqlAlchemyUnitOfWorkFactory(
            SqlAlchemyGeographyUnitOfWork,
            session_factory=session_factory,
            outbox_writer=outbox_writer,
        ),
        hazards_uow_factory=SqlAlchemyUnitOfWorkFactory(
            SqlAlchemyHazardsUnitOfWork,
            session_factory=session_factory,
            outbox_writer=outbox_writer,
        ),
        impacts_uow_factory=SqlAlchemyUnitOfWorkFactory(
            SqlAlchemyImpactsUnitOfWork,
            session_factory=session_factory,
            outbox_writer=outbox_writer,
        ),
        place_query_service=SqlAlchemyPlaceQueryService(session_factory),
        hazard_type_query_service=SqlAlchemyHazardTypeQueryService(session_factory),
        impact_metric_query_service=SqlAlchemyImpactMetricQueryService(session_factory),
        identity_uow_factory=SqlAlchemyUnitOfWorkFactory(
            SqlAlchemyIdentityUnitOfWork,
            session_factory=session_factory,
            outbox_writer=outbox_writer,
        ),
        identity_query_service=SqlAlchemyIdentityQueryService(session_factory),
        token_validator=token_validator,
        rate_limiter=rate_limiter,
        idempotency_store=SqlAlchemyIdempotencyStore(session_factory, id_generator),
        task_broker=task_broker,
        task_handlers=task_handlers,
        task_queue=task_queue,
        http_client=http_client,
        redis=redis,
    )
    # With the memory backend tasks run in this process, so they need handlers
    # here; with redis they run in the worker, and these are simply never called.
    task_handlers.bind(build_task_handlers(container))
    return container


def build_task_handlers(container: Container) -> Mapping[str, TaskHandler]:
    """Bind every task name this build can serve to its handler.

    The platform tasks are bound here. A module's task (``reports.run_triage``,
    ``media.scan``) is added as one more entry built from the container, for
    example ``REPORTS_TRIAGE_TASK: build_run_triage_task_handler(container)``; until
    then the worker answers such a task with ``TaskHandlerNotBoundError``.

    Args:
        container: Supplies the relay, the stores, the clock and the settings.

    Returns:
        A read-only mapping from task name to handler.
    """
    settings = container.settings

    async def relay_outbox(_task: ScheduledTask) -> None:
        outcome = await container.outbox_relay.relay_once(settings.outbox_batch_size)
        if outcome.claimed:
            structlog.get_logger(__name__).info(
                "outbox_relayed", **outcome.model_dump()
            )

    async def purge_outbox(_task: ScheduledTask) -> None:
        cutoff = container.clock.now() - timedelta(days=settings.outbox_retention_days)
        await container.outbox_relay.purge_published(cutoff)

    async def purge_idempotency_keys(_task: ScheduledTask) -> None:
        deleted = await container.idempotency_store.purge_expired(container.clock.now())
        structlog.get_logger(__name__).info("idempotency_purged", deleted=deleted)

    return MappingProxyType(
        {
            OUTBOX_RELAY_TASK: relay_outbox,
            OUTBOX_PURGE_TASK: purge_outbox,
            IDEMPOTENCY_PURGE_TASK: purge_idempotency_keys,
        }
    )


def build_seed_handler(container: Container) -> SeedReferenceDataHandler:
    """Wire the reference-data seed to the container's ports.

    One ``CanManageReferenceData`` policy (admins only) guards the seed and every
    module load it calls, so the seed changes nothing unless its actor is an admin;
    the command line runs it as a synthetic system actor holding ``admin``.
    The reader reads ``settings.reference_data_dir`` when the handler runs, not now.

    Args:
        container: The container whose units of work, clock and ids the loads use.

    Returns:
        The seed handler, ready to be called with ``SeedReferenceData``.
    """
    policy = CanManageReferenceData()
    return SeedReferenceDataHandler(
        reader=YamlReferenceFileReader(container.settings.reference_data_dir),
        policy=policy,
        load_hazard_types=LoadReferenceHazardTypesHandler(
            container.hazards_uow_factory,
            policy,
            container.clock,
            container.id_generator,
        ),
        load_impact_metrics=LoadReferenceImpactMetricsHandler(
            container.impacts_uow_factory,
            policy,
            container.clock,
            container.id_generator,
        ),
        load_places=LoadReferencePlacesHandler(
            container.geography_uow_factory,
            policy,
            container.clock,
            container.id_generator,
        ),
    )


def get_container(request: Request) -> Container:
    """Return the application's container (a FastAPI dependency).

    Args:
        request: The current request.

    Returns:
        The container stored on ``request.app.state.container``.

    Raises:
        RuntimeError: If the application was not built by ``create_app``; this is a
            wiring bug, so it surfaces as a server error rather than a domain error.
    """
    container = getattr(request.app.state, "container", None)
    if not isinstance(container, Container):
        message = "app.state.container is missing; build the app with create_app"
        raise RuntimeError(message)
    return container
