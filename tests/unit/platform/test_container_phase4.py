"""Unit tests for the Phase 4 bindings of ``yakhnama.platform.container``.

``build_container`` does no I/O, so the production bindings of the exchange and
ingestion modules are inspected directly; the task handlers run over the API
harness container, whose stores are Fakes.
"""

from collections.abc import AsyncIterator
from typing import Final

import pytest

from tests.fakes.api import build_test_app
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.exchange.infrastructure.adapters.s3_artifact_store import (
    S3ArtifactStore,
)
from yakhnama.modules.exchange.infrastructure.queries import (
    SqlAlchemyExchangeQueryService,
)
from yakhnama.modules.exchange.infrastructure.uow import SqlAlchemyExchangeUnitOfWork
from yakhnama.modules.exchange.public import ExportFormat, ImportFormat
from yakhnama.modules.ingestion.domain.errors import IngestionRunNotFoundError
from yakhnama.modules.ingestion.infrastructure.adapters.reference import (
    LOCAL_CSV_TEMPERATURE_ADAPTER,
)
from yakhnama.modules.ingestion.infrastructure.queries import (
    SqlAlchemyIngestionQueryService,
)
from yakhnama.modules.ingestion.infrastructure.uow import (
    SqlAlchemyIngestionUnitOfWork,
)
from yakhnama.platform.container import (
    EXPORT_GENERATOR_PREFIX,
    Container,
    build_container,
    build_task_handlers,
    export_generator,
    includes_fixture_datasets,
)
from yakhnama.platform.settings import Settings
from yakhnama.platform.tasks.handlers import (
    EXCHANGE_RUN_EXPORT_TASK,
    EXCHANGE_RUN_IMPORT_TASK,
    INGESTION_RUN_TASK,
)
from yakhnama.platform.wiring.exchange import (
    FacadeExportRowSource,
    IdentityActorLookupAdapter,
    RunExportTaskAdapter,
    RunImportTaskAdapter,
)
from yakhnama.platform.wiring.ingestion import ExecuteIngestionRunTaskAdapter
from yakhnama.shared_kernel.tasks import ScheduledTask, TaskId

IDS: Final = SequentialIdGenerator(seed=9501)


@pytest.fixture
async def container(settings: Settings) -> AsyncIterator[Container]:
    container = build_container(settings)

    yield container

    await container.aclose()


def test_build_container_binds_sqlalchemy_units_of_work_of_phase4_modules(
    container: Container,
) -> None:
    opened = (container.exchange_uow_factory(), container.ingestion_uow_factory())

    # Names, not classes: each port type is a Protocol.
    assert [type(uow).__name__ for uow in opened] == [
        SqlAlchemyExchangeUnitOfWork.__name__,
        SqlAlchemyIngestionUnitOfWork.__name__,
    ]


def test_build_container_binds_sql_reads_and_the_s3_artifact_store(
    container: Container,
) -> None:
    assert isinstance(container.exchange_query_service, SqlAlchemyExchangeQueryService)
    assert isinstance(container.ingestion_queries, SqlAlchemyIngestionQueryService)
    assert isinstance(container.artifact_store, S3ArtifactStore)


def test_build_container_exchange_dependencies_use_production_adapters(
    container: Container,
) -> None:
    dependencies = container.exchange_handler_dependencies

    assert dependencies.artifacts is container.artifact_store
    assert dependencies.uow_factory is container.exchange_uow_factory
    assert dependencies.tasks is container.task_queue
    assert isinstance(dependencies.actors, IdentityActorLookupAdapter)
    for export_format in ExportFormat:
        assert dependencies.formats.exporter(export_format).format is export_format
    for import_format in ImportFormat:
        assert dependencies.formats.importer(import_format).format is import_format


def test_build_container_run_export_reads_rows_through_the_facades(
    container: Container,
) -> None:
    run_export = container.run_export_handler

    # The handler keeps its ports private; the binding is what this test checks.
    rows = run_export._rows
    generator = run_export._generator

    assert isinstance(rows, FacadeExportRowSource)
    assert generator == export_generator()
    assert generator.startswith(EXPORT_GENERATOR_PREFIX)


def test_build_container_registers_the_reference_ingestion_source(
    container: Container,
) -> None:
    adapters = container.source_adapters

    assert LOCAL_CSV_TEMPERATURE_ADAPTER in adapters.names
    assert container.pipeline_factory.supports(LOCAL_CSV_TEMPERATURE_ADAPTER)


def test_build_container_binds_phase4_task_adapters(container: Container) -> None:
    handlers = build_task_handlers(container)

    assert isinstance(handlers[EXCHANGE_RUN_EXPORT_TASK], RunExportTaskAdapter)
    assert isinstance(handlers[EXCHANGE_RUN_IMPORT_TASK], RunImportTaskAdapter)
    assert isinstance(handlers[INGESTION_RUN_TASK], ExecuteIngestionRunTaskAdapter)


async def test_ingestion_task_runs_the_containers_execute_handler() -> None:
    api = build_test_app()
    container = api.app.state.container
    assert isinstance(container, Container)
    task = ScheduledTask.model_validate(
        {
            "task_id": TaskId(value="t-1"),
            "task_name": INGESTION_RUN_TASK,
            "payload": {"run_id": str(IDS.new_id())},
        }
    )

    with pytest.raises(IngestionRunNotFoundError):
        await build_task_handlers(container)[INGESTION_RUN_TASK](task)


@pytest.mark.parametrize(
    ("environment", "expected"),
    [("development", True), ("test", True), ("production", False)],
)
def test_includes_fixture_datasets_only_outside_production(
    settings: Settings, environment: str, *, expected: bool
) -> None:
    # model_copy skips validation, so the production guards need no real secrets.
    chosen = settings.model_copy(update={"environment": environment})

    included = includes_fixture_datasets(chosen)

    assert included is expected
