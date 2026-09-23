"""The composition root for infrastructure: one ``Container`` per application.

``build_container`` binds the kernel ports to their adapters (``Clock`` to
``SystemClock``, ``IdGenerator`` to ``Uuid7Generator``) and builds the engine, session
factory, outbox and unit-of-work factory from ``Settings``. ``yakhnama.main`` stores
the container on ``app.state.container``; routes reach it through the ``get_container``
dependency. Only this module and ``yakhnama.main`` bind ports to adapters
(``AGENTS.md`` §2.1).

Modules add their bindings here in later tasks (T11 onwards): a module's
``SqlAlchemyUnitOfWorkFactory(<Module>UnitOfWork, ...)``, its query services and its
outbox subscribers registered on ``subscriber_registry``.

Patterns: Composition Root, Dependency Injection.
"""

import dataclasses

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from yakhnama.platform.db import create_engine, create_session_factory
from yakhnama.platform.outbox.relay import OutboxRelay, SubscriberRegistry
from yakhnama.platform.outbox.writer import OutboxWriter
from yakhnama.platform.settings import Settings
from yakhnama.platform.uow import SqlAlchemyUnitOfWork, SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.clock import Clock, SystemClock
from yakhnama.shared_kernel.ids import IdGenerator, Uuid7Generator


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
