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

Patterns: Composition Root, Dependency Injection.
"""

import dataclasses

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

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
from yakhnama.platform.db import create_engine, create_session_factory
from yakhnama.platform.outbox.relay import OutboxRelay, SubscriberRegistry
from yakhnama.platform.outbox.writer import OutboxWriter
from yakhnama.platform.settings import Settings
from yakhnama.platform.uow import SqlAlchemyUnitOfWork, SqlAlchemyUnitOfWorkFactory
from yakhnama.seed.application import ActorAllowListPolicy, SeedReferenceDataHandler
from yakhnama.seed.infrastructure import YamlReferenceFileReader
from yakhnama.shared_kernel.clock import Clock, SystemClock
from yakhnama.shared_kernel.ids import EntityId, IdGenerator, Uuid7Generator


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
        outbox_relay: Delivers pending outbox messages to ``subscriber_registry``.
        geography_uow_factory: The geography ``UnitOfWorkFactory`` port.
        hazards_uow_factory: The hazards ``UnitOfWorkFactory`` port.
        impacts_uow_factory: The impacts ``UnitOfWorkFactory`` port.
        place_query_service: The geography ``PlaceQueryService`` port.
        hazard_type_query_service: The hazards ``HazardTypeQueryService`` port.
        impact_metric_query_service: The impacts ``ImpactMetricQueryService`` port.
    """

    settings: Settings
    clock: Clock
    id_generator: IdGenerator
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    outbox_writer: OutboxWriter
    uow_factory: SqlAlchemyUnitOfWorkFactory[SqlAlchemyUnitOfWork]
    subscriber_registry: SubscriberRegistry
    outbox_relay: OutboxRelay
    geography_uow_factory: GeographyUnitOfWorkFactory
    hazards_uow_factory: HazardsUnitOfWorkFactory
    impacts_uow_factory: ImpactsUnitOfWorkFactory
    place_query_service: PlaceQueryService
    hazard_type_query_service: HazardTypeQueryService
    impact_metric_query_service: ImpactMetricQueryService


def build_container(settings: Settings) -> Container:
    """Bind the ports to their production adapters.

    No I/O happens here: the engine connects lazily on first use.

    Args:
        settings: The application settings.

    Returns:
        A container ready to be stored on ``app.state.container``.
    """
    clock = SystemClock()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    outbox_writer = OutboxWriter(clock)
    subscriber_registry = SubscriberRegistry()
    return Container(
        settings=settings,
        clock=clock,
        id_generator=Uuid7Generator(clock),
        engine=engine,
        session_factory=session_factory,
        outbox_writer=outbox_writer,
        uow_factory=SqlAlchemyUnitOfWorkFactory(
            SqlAlchemyUnitOfWork,
            session_factory=session_factory,
            outbox_writer=outbox_writer,
        ),
        subscriber_registry=subscriber_registry,
        outbox_relay=OutboxRelay(subscriber_registry, clock),
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
    )


def build_seed_handler(
    container: Container, actor_id: EntityId
) -> SeedReferenceDataHandler:
    """Wire the reference-data seed to the container's ports.

    One ``ActorAllowListPolicy`` listing only ``actor_id`` guards the seed and every
    module load it calls, so the seed can change nothing on behalf of anyone else.
    The reader reads ``settings.reference_data_dir`` when the handler runs, not now.

    Args:
        container: The container whose units of work, clock and ids the loads use.
        actor_id: The system actor the seed acts as; the only actor allowed.

    Returns:
        The seed handler, ready to be called with ``SeedReferenceData``.
    """
    policy = ActorAllowListPolicy([actor_id])
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
