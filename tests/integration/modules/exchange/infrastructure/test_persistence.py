"""The SQLAlchemy exchange repositories, unit of work and query service on PostGIS.

Every status a job reaches must be saved and reload exactly (filters, artifact,
sidecar, report, writes); optimistic concurrency must accept several changes saved
once and refuse a stale save; the export listing must page newest first exactly
like the in-memory Fake. Visibility is the application's concern, so the query
service returns every job it is asked for.
"""

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories.base import FACTORY_IDS
from tests.factories.exchange import (
    ExportJobTestFactory,
    ImportJobTestFactory,
    artifact_for,
    sidecar_for,
)
from tests.fakes.clock import SteppingClock
from tests.fakes.exchange import (
    InMemoryExchangeQueryService,
    InMemoryExchangeUnitOfWork,
)
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.exchange.application.dto import (
    ExportJobDetail,
    ExportJobSummary,
    ImportJobDetail,
)
from yakhnama.modules.exchange.domain.entities import ExportJob, ImportJob
from yakhnama.modules.exchange.domain.errors import (
    ExportJobNotFoundError,
    ImportJobNotFoundError,
)
from yakhnama.modules.exchange.domain.value_objects import (
    ExportDataset,
    ExportFilters,
    ExportFormat,
    ImportFormat,
    ImportWrites,
    RowIssue,
    ValidationReport,
    ValidationSeverity,
)
from yakhnama.modules.exchange.infrastructure.queries import (
    SqlAlchemyExchangeQueryService,
)
from yakhnama.modules.exchange.infrastructure.uow import SqlAlchemyExchangeUnitOfWork
from yakhnama.platform.outbox.writer import OutboxWriter
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import (
    ConflictError,
    InvariantViolationError,
    ValidationError,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import (
    CursorPayload,
    Page,
    PageRequest,
    encode_cursor,
)
from yakhnama.shared_kernel.value_objects import (
    BoundingBox,
    DatePrecision,
    DateWithPrecision,
)

pytestmark = pytest.mark.integration

type ExchangeFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyExchangeUnitOfWork]

REQUESTED: Final = datetime(2026, 9, 1, 10, 0, 0, 250000, tzinfo=UTC)
FILTERS: Final = ExportFilters(
    bbox=BoundingBox(
        min_longitude=74.1, min_latitude=35.2, max_longitude=75.3, max_latitude=36.4
    ),
    hazard_type="glof",
    place_code="test.place-a",
    occurred_from=DateWithPrecision(
        value=datetime(2010, 1, 1, tzinfo=UTC), precision=DatePrecision.YEAR
    ),
    occurred_to=DateWithPrecision(
        value=datetime(2024, 7, 15, tzinfo=UTC), precision=DatePrecision.DAY
    ),
    status="published",
)


@pytest.fixture
def exchange_queries(
    session_factory: async_sessionmaker[AsyncSession],
) -> SqlAlchemyExchangeQueryService:
    """Return the SQL exchange query service on the test database."""
    return SqlAlchemyExchangeQueryService(session_factory)


async def _add(
    factory: ExchangeFactory,
    exports: tuple[ExportJob, ...] = (),
    imports: tuple[ImportJob, ...] = (),
) -> None:
    async with factory() as uow:
        for export_job in exports:
            await uow.export_jobs.add(export_job)
        for import_job in imports:
            await uow.import_jobs.add(import_job)
        await uow.commit()


def _report() -> ValidationReport:
    return ValidationReport(
        issues=(
            RowIssue(row_number=2, field="hazard_code", message="unknown hazard"),
            RowIssue(
                row_number=3,
                field="claim_1_value",
                message="rounded to whole people",
                severity=ValidationSeverity.WARNING,
            ),
        ),
        rows_seen=3,
        rows_valid=2,
        rows_rejected=1,
    )


# --------------------------------------------------------------------------- #
# Export jobs                                                                 #
# --------------------------------------------------------------------------- #


async def test_export_job_each_saved_status_round_trips_exactly(
    exchange_uow_factory: ExchangeFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    queued = ExportJobTestFactory.build(
        requested_at=REQUESTED,
        filters=FILTERS,
        format=ExportFormat.GEOPARQUET,
        dataset=ExportDataset.CLAIMS,
    )
    await _add(exchange_uow_factory, exports=(queued,))
    running = queued.start(clock=clock, ids=ids).state
    artifact = artifact_for(running)
    completed = running.complete(
        artifact, sidecar_for(running, artifact, row_count=42), clock=clock, ids=ids
    ).state
    loaded: list[ExportJob | None] = []

    for state in (running, completed):
        async with exchange_uow_factory() as uow:
            await uow.export_jobs.save(state)
            await uow.commit()
        async with exchange_uow_factory() as uow:
            loaded.append(await uow.export_jobs.get(queued.id))

    assert loaded == [running, completed]


async def test_export_job_failed_and_cancelled_round_trip(
    exchange_uow_factory: ExchangeFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    to_fail = ExportJobTestFactory.build()
    to_cancel = ExportJobTestFactory.build()
    await _add(exchange_uow_factory, exports=(to_fail, to_cancel))
    failed = (
        to_fail.start(clock=clock, ids=ids)
        .state.fail("the row source timed out", clock=clock, ids=ids)
        .state
    )
    cancelled = to_cancel.cancel(clock=clock, ids=ids).state

    async with exchange_uow_factory() as uow:
        # Loaded first, as the handlers do: the failed job then carries two
        # changes (started, failed) in one save.
        await uow.export_jobs.get(to_fail.id)
        await uow.export_jobs.save(failed)
        await uow.export_jobs.save(cancelled)
        await uow.commit()
    async with exchange_uow_factory() as uow:
        loaded = (
            await uow.export_jobs.get(to_fail.id),
            await uow.export_jobs.get(to_cancel.id),
        )

    assert loaded == (failed, cancelled)


async def test_export_job_save_after_load_accepts_several_steps_at_once(
    exchange_uow_factory: ExchangeFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    queued = ExportJobTestFactory.build()
    await _add(exchange_uow_factory, exports=(queued,))

    async with exchange_uow_factory() as uow:
        loaded = await uow.export_jobs.get(queued.id)
        assert loaded is not None
        failed = (
            loaded.start(clock=clock, ids=ids)
            .state.fail("stopped", clock=clock, ids=ids)
            .state
        )
        await uow.export_jobs.save(failed)
        await uow.commit()
    async with exchange_uow_factory() as uow:
        stored = await uow.export_jobs.get(queued.id)

    assert stored == failed
    assert failed.version == queued.version + 2


async def test_export_job_stale_save_raises_conflict(
    exchange_uow_factory: ExchangeFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    queued = ExportJobTestFactory.build()
    await _add(exchange_uow_factory, exports=(queued,))
    running = queued.start(clock=clock, ids=ids).state

    async with exchange_uow_factory() as first, exchange_uow_factory() as second:
        in_first = await first.export_jobs.get(queued.id)
        in_second = await second.export_jobs.get(queued.id)
        assert in_first is not None
        assert in_second is not None
        await first.export_jobs.save(in_first.start(clock=clock, ids=ids).state)
        await first.commit()
        with pytest.raises(ConflictError) as raised:
            await second.export_jobs.save(in_second.cancel(clock=clock, ids=ids).state)

    assert raised.value.details["stored_version"] == running.version


async def test_export_job_two_steps_saved_without_loading_is_refused(
    exchange_uow_factory: ExchangeFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    queued = ExportJobTestFactory.build()
    await _add(exchange_uow_factory, exports=(queued,))
    failed = (
        queued.start(clock=clock, ids=ids)
        .state.fail("stopped", clock=clock, ids=ids)
        .state
    )

    async with exchange_uow_factory() as uow:
        # Not loaded here, so one step is assumed; failing safe refuses the save.
        with pytest.raises(ConflictError):
            await uow.export_jobs.save(failed)


async def test_export_job_save_without_growth_or_unknown_is_refused(
    exchange_uow_factory: ExchangeFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    queued = ExportJobTestFactory.build()
    await _add(exchange_uow_factory, exports=(queued,))
    unknown = ExportJobTestFactory.build().start(clock=clock, ids=ids).state

    async with exchange_uow_factory() as uow:
        loaded = await uow.export_jobs.get(queued.id)
        assert loaded is not None
        with pytest.raises(ConflictError):
            await uow.export_jobs.save(loaded)
        with pytest.raises(ExportJobNotFoundError):
            await uow.export_jobs.save(unknown)


async def test_export_job_add_duplicate_id_raises_conflict_and_keeps_uow(
    exchange_uow_factory: ExchangeFactory,
) -> None:
    first = ExportJobTestFactory.build()
    await _add(exchange_uow_factory, exports=(first,))
    other = ExportJobTestFactory.build()

    async with exchange_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.export_jobs.add(first)
        await uow.export_jobs.add(other)
        await uow.commit()
    async with exchange_uow_factory() as uow:
        stored = await uow.export_jobs.get(other.id)
        missing = await uow.export_jobs.get(FACTORY_IDS.new_id())

    assert stored == other
    assert missing is None


# --------------------------------------------------------------------------- #
# Import jobs                                                                 #
# --------------------------------------------------------------------------- #


async def test_import_job_real_import_round_trips_report_and_writes(
    exchange_uow_factory: ExchangeFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    queued = ImportJobTestFactory.build(requested_at=REQUESTED)
    await _add(exchange_uow_factory, imports=(queued,))
    running = queued.start(clock=clock, ids=ids).state
    report = ValidationReport(rows_seen=2, rows_valid=2, rows_rejected=0)
    writes = ImportWrites(
        created_ids=(FACTORY_IDS.new_id(), FACTORY_IDS.new_id()),
        lineage_source_id=FACTORY_IDS.new_id(),
        batches_applied=1,
    )
    completed = running.complete(report, writes, clock=clock, ids=ids).state
    loaded: list[ImportJob | None] = []

    for state in (running, completed):
        async with exchange_uow_factory() as uow:
            await uow.import_jobs.save(state)
            await uow.commit()
        async with exchange_uow_factory() as uow:
            loaded.append(await uow.import_jobs.get(queued.id))

    assert loaded == [running, completed]


async def test_import_job_dry_run_and_failure_round_trip(
    exchange_uow_factory: ExchangeFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    dry_run = ImportJobTestFactory.build(dry_run=True, format=ImportFormat.GEOJSON)
    to_fail = ImportJobTestFactory.build()
    await _add(exchange_uow_factory, imports=(dry_run, to_fail))
    checked = (
        dry_run.start(clock=clock, ids=ids)
        .state.complete(_report(), clock=clock, ids=ids)
        .state
    )
    failed = (
        to_fail.start(clock=clock, ids=ids)
        .state.fail(
            "batch 2 was refused",
            _report(),
            ImportWrites(
                created_ids=(FACTORY_IDS.new_id(),),
                lineage_source_id=FACTORY_IDS.new_id(),
                batches_applied=1,
            ),
            clock=clock,
            ids=ids,
        )
        .state
    )

    async with exchange_uow_factory() as uow:
        # Loaded first: each job carries two changes in one save.
        await uow.import_jobs.get(dry_run.id)
        await uow.import_jobs.get(to_fail.id)
        await uow.import_jobs.save(checked)
        await uow.import_jobs.save(failed)
        await uow.commit()
    async with exchange_uow_factory() as uow:
        loaded = (
            await uow.import_jobs.get(dry_run.id),
            await uow.import_jobs.get(to_fail.id),
        )

    assert loaded == (checked, failed)


async def test_import_job_stale_duplicate_and_unknown_are_refused(
    exchange_uow_factory: ExchangeFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    queued = ImportJobTestFactory.build()
    await _add(exchange_uow_factory, imports=(queued,))
    running = queued.start(clock=clock, ids=ids).state
    unknown = ImportJobTestFactory.build().start(clock=clock, ids=ids).state

    async with exchange_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.import_jobs.add(queued)
        await uow.import_jobs.save(running)
        with pytest.raises(ConflictError):
            await uow.import_jobs.save(running)
        with pytest.raises(ImportJobNotFoundError):
            await uow.import_jobs.save(unknown)


async def test_import_job_uncommitted_add_is_rolled_back(
    exchange_uow_factory: ExchangeFactory,
) -> None:
    job = ImportJobTestFactory.build()

    async with exchange_uow_factory() as uow:
        await uow.import_jobs.add(job)
    async with exchange_uow_factory() as uow:
        stored = await uow.import_jobs.get(job.id)

    assert stored is None


def test_exchange_uow_repositories_outside_async_with_raise(
    outbox_writer: OutboxWriter,
) -> None:
    def _no_session() -> AsyncSession:
        message = "a session must not be opened"
        raise AssertionError(message)

    uow = SqlAlchemyExchangeUnitOfWork(_no_session, outbox_writer)

    with pytest.raises(InvariantViolationError):
        _ = uow.export_jobs
    with pytest.raises(InvariantViolationError):
        _ = uow.import_jobs


# --------------------------------------------------------------------------- #
# Query service                                                               #
# --------------------------------------------------------------------------- #


async def test_query_service_get_returns_every_job_whoever_owns_it(
    exchange_uow_factory: ExchangeFactory,
    exchange_queries: SqlAlchemyExchangeQueryService,
) -> None:
    export_job = ExportJobTestFactory.build(filters=FILTERS)
    import_job = ImportJobTestFactory.build()
    await _add(exchange_uow_factory, exports=(export_job,), imports=(import_job,))

    export_detail = await exchange_queries.get_export_job(export_job.id)
    import_detail = await exchange_queries.get_import_job(import_job.id)
    missing_export = await exchange_queries.get_export_job(FACTORY_IDS.new_id())
    missing_import = await exchange_queries.get_import_job(FACTORY_IDS.new_id())

    assert export_detail == ExportJobDetail.from_entity(export_job)
    assert import_detail == ImportJobDetail.from_entity(import_job)
    assert missing_export is None
    assert missing_import is None


async def _pages(
    queries: SqlAlchemyExchangeQueryService | InMemoryExchangeQueryService,
    requested_by: EntityId | None,
    limit: int,
) -> list[Page[ExportJobSummary]]:
    pages: list[Page[ExportJobSummary]] = []
    cursor: str | None = None
    while True:
        page = await queries.list_export_jobs(
            requested_by, PageRequest(limit=limit, cursor=cursor)
        )
        pages.append(page)
        cursor = page.next_cursor
        if cursor is None:
            return pages


async def test_list_export_jobs_pages_newest_first_like_the_fake(
    exchange_uow_factory: ExchangeFactory,
    exchange_queries: SqlAlchemyExchangeQueryService,
) -> None:
    owner, other = FACTORY_IDS.new_id(), FACTORY_IDS.new_id()
    # Ties on requested_at (whole and fractional seconds) are broken by id.
    jobs = tuple(
        ExportJobTestFactory.build(
            requested_by=owner if index % 3 else other,
            requested_at=REQUESTED + timedelta(milliseconds=500 * (index // 2)),
        )
        for index in range(11)
    )
    await _add(exchange_uow_factory, exports=jobs)
    fake = InMemoryExchangeQueryService(InMemoryExchangeUnitOfWork(export_jobs=jobs))
    expected_all = [
        ExportJobSummary.from_entity(job)
        for job in sorted(
            jobs, key=lambda job: (job.requested_at, job.id), reverse=True
        )
    ]

    for limit in (1, 2, 5, 20):
        for requested_by in (None, owner, other):
            sql_pages = await _pages(exchange_queries, requested_by, limit)
            fake_pages = await _pages(fake, requested_by, limit)

            assert sql_pages == fake_pages
    everything = await _pages(exchange_queries, None, 4)
    assert [item for page in everything for item in page.items] == expected_all


async def test_list_export_jobs_with_invalid_cursor_raises_validation_error(
    exchange_queries: SqlAlchemyExchangeQueryService,
) -> None:
    for sort_key in ("2026-09-01T10:00:00", "not-a-time"):
        cursor = encode_cursor(
            CursorPayload(sort_key=sort_key, last_id=FACTORY_IDS.new_id())
        )

        with pytest.raises(ValidationError):
            await exchange_queries.list_export_jobs(None, PageRequest(cursor=cursor))
