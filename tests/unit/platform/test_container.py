"""Unit tests for ``yakhnama.platform.container`` and its wiring in ``create_app``."""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

from tests.fakes.identity import actor_with
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.main import create_app
from yakhnama.modules.geography.infrastructure.queries import (
    SqlAlchemyPlaceQueryService,
)
from yakhnama.modules.geography.infrastructure.uow import (
    SqlAlchemyGeographyUnitOfWork,
)
from yakhnama.modules.hazards.infrastructure.queries import (
    SqlAlchemyHazardTypeQueryService,
)
from yakhnama.modules.hazards.infrastructure.uow import SqlAlchemyHazardsUnitOfWork
from yakhnama.modules.identity.public import Role
from yakhnama.modules.impacts.infrastructure.queries import (
    SqlAlchemyImpactMetricQueryService,
)
from yakhnama.modules.impacts.infrastructure.uow import SqlAlchemyImpactsUnitOfWork
from yakhnama.platform.container import (
    Container,
    build_container,
    build_seed_handler,
    get_container,
)
from yakhnama.platform.outbox.relay import OutboxRelay, SubscriberRegistry
from yakhnama.platform.outbox.writer import OutboxWriter
from yakhnama.platform.settings import Settings
from yakhnama.platform.uow import SqlAlchemyUnitOfWork
from yakhnama.seed.application import SeedReferenceData, SeedReferenceDataHandler
from yakhnama.shared_kernel.clock import SystemClock
from yakhnama.shared_kernel.errors import PermissionDeniedError, ValidationError
from yakhnama.shared_kernel.ids import Uuid7Generator, is_uuid7


@pytest.fixture
async def container(settings: Settings) -> AsyncIterator[Container]:
    container = build_container(settings)

    yield container

    await container.engine.dispose()


def test_build_container_binds_system_clock_and_uuid7_generator(
    container: Container,
) -> None:
    identifier = container.id_generator.new_id()

    assert isinstance(container.clock, SystemClock)
    assert isinstance(container.id_generator, Uuid7Generator)
    assert is_uuid7(identifier)


def test_build_container_engine_uses_the_settings_database_url(
    container: Container, settings: Settings
) -> None:
    rendered = container.engine.url.render_as_string(hide_password=False)

    assert rendered == settings.database_url.unicode_string()
    assert container.settings is settings


def test_build_container_session_factory_is_bound_to_the_engine(
    container: Container,
) -> None:
    bind = container.session_factory.kw["bind"]

    assert bind is container.engine


def test_build_container_uow_factory_opens_sqlalchemy_units_of_work(
    container: Container,
) -> None:
    first, second = container.uow_factory(), container.uow_factory()

    assert type(first) is SqlAlchemyUnitOfWork
    assert first is not second


def test_build_container_holds_outbox_writer_registry_and_relay(
    container: Container,
) -> None:
    parts = (
        container.outbox_writer,
        container.subscriber_registry,
        container.outbox_relay,
    )

    assert isinstance(parts[0], OutboxWriter)
    assert isinstance(parts[1], SubscriberRegistry)
    assert isinstance(parts[2], OutboxRelay)


def test_build_container_module_uow_factories_open_module_units_of_work(
    container: Container,
) -> None:
    opened = (
        container.geography_uow_factory(),
        container.hazards_uow_factory(),
        container.impacts_uow_factory(),
    )

    assert isinstance(opened[0], SqlAlchemyGeographyUnitOfWork)
    assert isinstance(opened[1], SqlAlchemyHazardsUnitOfWork)
    assert isinstance(opened[2], SqlAlchemyImpactsUnitOfWork)
    assert opened[0] is not container.geography_uow_factory()


def test_build_container_binds_sqlalchemy_query_services(
    container: Container,
) -> None:
    services = (
        container.place_query_service,
        container.hazard_type_query_service,
        container.impact_metric_query_service,
    )

    assert isinstance(services[0], SqlAlchemyPlaceQueryService)
    assert isinstance(services[1], SqlAlchemyHazardTypeQueryService)
    assert isinstance(services[2], SqlAlchemyImpactMetricQueryService)


async def test_build_seed_handler_denies_a_non_admin_actor(
    container: Container,
) -> None:
    handler = build_seed_handler(container)
    citizen = actor_with(user_id=SequentialIdGenerator(seed=3).new_id())

    with pytest.raises(PermissionDeniedError):
        await handler(SeedReferenceData(actor=citizen))

    assert isinstance(handler, SeedReferenceDataHandler)


async def test_build_seed_handler_reads_the_settings_reference_directory(
    settings: Settings, tmp_path: Path
) -> None:
    # An empty directory: the allowed actor gets past the policy, and the reader
    # fails on the first file before any load opens a database session.
    container = build_container(
        settings.model_copy(update={"reference_data_dir": tmp_path})
    )
    # Any admin actor is allowed, whatever its id.
    admin = actor_with({Role.ADMIN}, user_id=SequentialIdGenerator(seed=4).new_id())
    handler = build_seed_handler(container)

    with pytest.raises(ValidationError) as caught:
        await handler(SeedReferenceData(actor=admin))
    await container.engine.dispose()

    assert caught.value.details == {"file": "hazard_types.yaml", "reason": "not_found"}


def test_create_app_without_container_builds_and_stores_one(app: FastAPI) -> None:
    stored = app.state.container

    assert isinstance(stored, Container)
    assert stored.settings is app.state.settings


async def test_create_app_with_container_uses_it_as_given(
    settings: Settings, container: Container
) -> None:
    app = create_app(settings, container=container)

    assert app.state.container is container


def test_get_container_returns_the_app_container(app: FastAPI) -> None:
    request = Request({"type": "http", "app": app})

    resolved = get_container(request)

    assert resolved is app.state.container


def test_get_container_without_container_raises_runtime_error(app: FastAPI) -> None:
    app.state.container = None
    request = Request({"type": "http", "app": app})

    with pytest.raises(RuntimeError, match="create_app"):
        get_container(request)


def test_create_app_docs_disabled_serves_no_openapi_or_docs(
    settings: Settings,
) -> None:
    app = create_app(settings.model_copy(update={"docs_enabled": False}))

    urls = (app.openapi_url, app.docs_url, app.redoc_url)

    assert urls == (None, None, None)


async def test_create_app_docs_disabled_openapi_url_returns_not_found(
    settings: Settings,
) -> None:
    app = create_app(settings.model_copy(update={"docs_enabled": False}))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/openapi.json")

    assert response.status_code == 404


async def test_lifespan_shutdown_disposes_engine_of_owned_container(
    app: FastAPI,
) -> None:
    engine = app.state.container.engine
    pool_before = engine.sync_engine.pool

    async with app.router.lifespan_context(app):
        pool_during = engine.sync_engine.pool

    assert pool_during is pool_before
    assert engine.sync_engine.pool is not pool_before


async def test_lifespan_shutdown_leaves_injected_container_engine_alone(
    settings: Settings, container: Container
) -> None:
    app = create_app(settings, container=container)
    pool_before = container.engine.sync_engine.pool

    async with app.router.lifespan_context(app):
        pass

    assert container.engine.sync_engine.pool is pool_before


async def test_lifespan_shutdown_stops_telemetry(settings: Settings) -> None:
    app = create_app(settings.model_copy(update={"otel_enabled": True}))

    async with app.router.lifespan_context(app):
        is_instrumented = SQLAlchemyInstrumentor().is_instrumented_by_opentelemetry

    assert is_instrumented is True
    assert SQLAlchemyInstrumentor().is_instrumented_by_opentelemetry is False
