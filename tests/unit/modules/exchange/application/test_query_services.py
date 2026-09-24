"""Unit tests for reading export and import jobs."""

from uuid import UUID

import pytest

from tests.fakes.identity import actor_with
from tests.unit.modules.exchange.application.support import (
    ANONYMOUS,
    CITIZEN,
    MODERATOR,
    MODERATOR_ID,
    OTHER,
    OTHER_ID,
    USER_ID,
    ExchangeWorld,
    cells,
    import_file,
)
from yakhnama.modules.exchange.application.commands import RunExport, RunImport
from yakhnama.modules.exchange.application.queries import (
    GetExportJob,
    GetImportJob,
    ListExportJobs,
)
from yakhnama.modules.exchange.domain.errors import (
    ExportJobNotFoundError,
    ImportJobNotFoundError,
)
from yakhnama.modules.exchange.domain.value_objects import ExportDataset, JobStatus
from yakhnama.modules.identity.public import Actor, Role
from yakhnama.shared_kernel.errors import PermissionDeniedError
from yakhnama.shared_kernel.pagination import PageRequest


async def test_get_export_job_queued_for_owner_has_no_download_link() -> None:
    world = ExchangeWorld()
    job_id = await world.queued_export()

    view = await world.queries.get_export_job(
        GetExportJob(actor=CITIZEN, job_id=job_id)
    )

    assert view.job.id == job_id
    assert view.job.status is JobStatus.QUEUED
    assert view.download_url is None
    assert world.artifacts.presigned == []


@pytest.mark.parametrize("actor", [CITIZEN, MODERATOR])
async def test_get_export_job_completed_returns_presigned_link(actor: Actor) -> None:
    world = ExchangeWorld()
    job_id = await world.queued_export()
    await world.run_export()(RunExport(job_id=job_id))

    view = await world.queries.get_export_job(GetExportJob(actor=actor, job_id=job_id))

    key = f"exports/{job_id}/events.csv"
    assert view.download_url == f"https://storage.test/{key}"
    assert world.artifacts.presigned == [(key, f"yakhnama-{job_id}-events.csv")]
    assert view.job.sidecar is not None
    assert view.job.sidecar.row_count == 0


async def test_get_export_job_of_other_user_raises_not_found() -> None:
    world = ExchangeWorld()
    job_id = await world.queued_export()

    with pytest.raises(ExportJobNotFoundError):
        await world.queries.get_export_job(GetExportJob(actor=OTHER, job_id=job_id))


async def test_get_export_job_unknown_raises_not_found() -> None:
    world = ExchangeWorld()

    with pytest.raises(ExportJobNotFoundError):
        await world.queries.get_export_job(GetExportJob(actor=CITIZEN, job_id=USER_ID))


async def test_get_export_job_by_anonymous_raises_permission_denied() -> None:
    world = ExchangeWorld()
    job_id = await world.queued_export()

    with pytest.raises(PermissionDeniedError):
        await world.queries.get_export_job(GetExportJob(actor=ANONYMOUS, job_id=job_id))


async def test_list_export_jobs_for_citizen_returns_only_own_jobs() -> None:
    world = ExchangeWorld()
    own = await world.queued_export()
    await world.queued_export(actor=OTHER)

    page = await world.queries.list_export_jobs(ListExportJobs(actor=CITIZEN))

    assert [item.id for item in page.items] == [own]
    assert page.items[0].row_count is None


async def test_list_export_jobs_for_moderator_pages_every_job_newest_first() -> None:
    world = ExchangeWorld()
    first = await world.queued_export()
    second = await world.queued_export(actor=OTHER)
    third = await world.queued_export(ExportDataset.REPORTS, MODERATOR)

    page = await world.queries.list_export_jobs(
        ListExportJobs(actor=MODERATOR, page=PageRequest(limit=2))
    )
    rest = await world.queries.list_export_jobs(
        ListExportJobs(
            actor=MODERATOR, page=PageRequest(limit=2, cursor=page.next_cursor)
        )
    )

    assert [item.id for item in page.items] == [third, second]
    assert [item.id for item in rest.items] == [first]
    assert rest.next_cursor is None


async def test_list_export_jobs_by_anonymous_raises_permission_denied() -> None:
    world = ExchangeWorld()

    with pytest.raises(PermissionDeniedError):
        await world.queries.list_export_jobs(ListExportJobs(actor=ANONYMOUS))


async def test_get_import_job_for_moderator_returns_report_and_writes() -> None:
    world = ExchangeWorld()
    job_id = await world.queued_import(import_file(cells()), dry_run=False)
    await world.run_import()(RunImport(job_id=job_id))

    detail = await world.queries.get_import_job(
        GetImportJob(actor=MODERATOR, job_id=job_id)
    )

    assert detail.status is JobStatus.COMPLETED
    assert detail.report is not None
    assert detail.report.rows_seen == 1
    assert len(detail.created_ids) == 1
    assert detail.lineage_source_id is not None
    assert detail.batches_applied == 1


async def test_get_import_job_by_citizen_raises_permission_denied() -> None:
    world = ExchangeWorld()
    job_id = await world.queued_import(import_file(cells()), dry_run=True)

    with pytest.raises(PermissionDeniedError):
        await world.queries.get_import_job(GetImportJob(actor=CITIZEN, job_id=job_id))


async def test_get_import_job_unknown_raises_not_found() -> None:
    world = ExchangeWorld()

    with pytest.raises(ImportJobNotFoundError):
        await world.queries.get_import_job(
            GetImportJob(actor=MODERATOR, job_id=USER_ID)
        )


async def _completed(
    world: ExchangeWorld, dataset: ExportDataset, actor: Actor
) -> UUID:
    job_id = await world.queued_export(dataset, actor)
    await world.run_export()(RunExport(job_id=job_id))
    return job_id


@pytest.mark.parametrize("dataset", [ExportDataset.REPORTS, ExportDataset.EVENTS])
async def test_get_export_job_for_demoted_owner_hides_download_link(
    dataset: ExportDataset,
) -> None:
    world = ExchangeWorld()
    job_id = await _completed(world, dataset, MODERATOR)
    demoted = Actor(user_id=MODERATOR_ID, roles=frozenset({Role.CITIZEN}))

    view = await world.queries.get_export_job(
        GetExportJob(actor=demoted, job_id=job_id)
    )

    assert view.job.status is JobStatus.COMPLETED
    assert view.job.visibility == "moderation"
    assert view.download_url is None
    assert world.artifacts.presigned == []


@pytest.mark.parametrize("dataset", [ExportDataset.REPORTS, ExportDataset.EVENTS])
async def test_get_export_job_of_moderation_export_links_current_moderator(
    dataset: ExportDataset,
) -> None:
    world = ExchangeWorld()
    job_id = await _completed(world, dataset, MODERATOR)
    other_moderator = actor_with({Role.MODERATOR}, user_id=OTHER_ID)

    view = await world.queries.get_export_job(
        GetExportJob(actor=other_moderator, job_id=job_id)
    )

    assert view.download_url is not None
