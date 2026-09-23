"""Unit tests for ``yakhnama.modules.exchange.domain.factories``."""

from datetime import UTC, datetime

from tests.factories.base import FACTORY_IDS
from tests.factories.exchange import artifact_ref
from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.exchange.domain.events import ExportRequested, ImportRequested
from yakhnama.modules.exchange.domain.factories import (
    ExportJobFactory,
    ImportJobFactory,
)
from yakhnama.modules.exchange.domain.value_objects import (
    ExportDataset,
    ExportFilters,
    ExportFormat,
    ExportRequest,
    ImportFormat,
    ImportRequest,
    JobStatus,
)

NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)


def test_export_job_factory_request_creates_queued_job_and_event() -> None:
    requester = FACTORY_IDS.new_id()
    request = ExportRequest(
        dataset=ExportDataset.EVENTS,
        format=ExportFormat.GEOPARQUET,
        filters=ExportFilters(hazard_type="glof"),
    )

    change = ExportJobFactory().request(
        requester, request, clock=FrozenClock(NOW), ids=SequentialIdGenerator(seed=3)
    )

    job = change.state
    assert (job.status, job.version, job.requested_at) == (JobStatus.QUEUED, 1, NOW)
    assert (job.dataset, job.format, job.filters) == (
        request.dataset,
        request.format,
        request.filters,
    )
    (event,) = change.events
    assert isinstance(event, ExportRequested)
    assert event.event_type == "exchange.export_requested"
    assert (event.aggregate_id, event.requested_by) == (job.id, requester)
    assert event.event_id != job.id


def test_import_job_factory_request_creates_queued_dry_run_and_event() -> None:
    requester = FACTORY_IDS.new_id()
    request = ImportRequest(
        format=ImportFormat.GEOJSON,
        source_artifact=artifact_ref(object_key="imports/test/rows.geojson"),
        dry_run=True,
    )

    change = ImportJobFactory().request(
        requester, request, clock=FrozenClock(NOW), ids=SequentialIdGenerator(seed=4)
    )

    job = change.state
    assert (job.status, job.dry_run, job.created_ids) == (JobStatus.QUEUED, True, ())
    assert job.source_artifact == request.source_artifact
    (event,) = change.events
    assert isinstance(event, ImportRequested)
    assert event.event_type == "exchange.import_requested"
    assert (event.format, event.dry_run) == (ImportFormat.GEOJSON, True)
