"""Creation of export and import jobs.

Patterns: Factory.
"""

from yakhnama.modules.exchange.domain.entities import ExportJob, ImportJob
from yakhnama.modules.exchange.domain.events import ExportRequested, ImportRequested
from yakhnama.modules.exchange.domain.value_objects import ExportRequest, ImportRequest
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import EntityId, IdGenerator


class ExportJobFactory:
    """Create queued export jobs.

    Implements: Factory.
    """

    def request(
        self,
        requested_by: EntityId,
        request: ExportRequest,
        *,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange[ExportJob]:
        """Create a queued export job.

        Whether ``requested_by`` may export the dataset is decided by the
        application's policy before this is called.

        Args:
            requested_by: The requesting user.
            request: Dataset, format and filters.
            clock: Source of ``requested_at`` and ``occurred_at``.
            ids: Source of the job id and the event id.

        Returns:
            The queued job and ``ExportRequested``.
        """
        now = clock.now()
        job = ExportJob(
            id=ids.new_id(),
            requested_by=requested_by,
            dataset=request.dataset,
            format=request.format,
            filters=request.filters,
            requested_at=now,
        )
        event = ExportRequested(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=job.id,
            version=job.version,
            requested_by=requested_by,
            dataset=request.dataset,
            format=request.format,
        )
        return AggregateChange[ExportJob](state=job, events=(event,))


class ImportJobFactory:
    """Create queued import jobs.

    Implements: Factory.
    """

    def request(
        self,
        requested_by: EntityId,
        request: ImportRequest,
        *,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange[ImportJob]:
        """Create a queued import job for a file already in storage.

        Args:
            requested_by: The requesting moderator.
            request: Format, stored file and whether it is a dry run.
            clock: Source of ``requested_at`` and ``occurred_at``.
            ids: Source of the job id and the event id.

        Returns:
            The queued job and ``ImportRequested``.
        """
        now = clock.now()
        job = ImportJob(
            id=ids.new_id(),
            requested_by=requested_by,
            format=request.format,
            dry_run=request.dry_run,
            source_artifact=request.source_artifact,
            requested_at=now,
        )
        event = ImportRequested(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=job.id,
            version=job.version,
            requested_by=requested_by,
            format=request.format,
            dry_run=request.dry_run,
        )
        return AggregateChange[ImportJob](state=job, events=(event,))
