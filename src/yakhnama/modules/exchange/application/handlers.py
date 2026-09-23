"""Write-side use cases of the exchange module: export and import jobs.

Requests (``RequestExport``, ``CancelExport``, ``RequestImport``) check their policy
before opening a unit of work, create or change the job, commit, and only then
enqueue the task: ``enqueue`` is not transactional, so a job is never announced
before it is stored. A failed enqueue propagates and leaves the job ``queued``,
harmless and visible (as in the reports module; an outbox-driven dispatcher or a
sweep is the robust trigger, see the open questions).

Runs (``RunExport``, ``RunImport``) are system tasks with at-least-once delivery.
A final job is left alone and a ``running`` job is skipped, never restarted: a
second worker must not write a second file or a second set of events while the
first one may still be working. Each run rebuilds the requesting actor through
``ActorLookup`` and checks the policy again, so a user who lost the right meanwhile
gets nothing.

A run records every failure on the job with a fixed sentence chosen by the error's
family (``JobErrorSummary``); an exception's text is never copied, because it may
quote a file's content or an adapter's internals. Deliberate failures
(``YakhnamaError``) end the run normally; anything else is recorded and re-raised,
so the worker logs it.

Imports: the file is read once, every row validated (shape by
``ImportedEventDraft.from_flat_row``, references by ``BackfillReferenceChecker``)
and the whole file checked against its declared size and digest. A dry run, a report
with any blocking error (**proposed**: an import with an error creates nothing) or an
empty file completes with the report and nothing written. Otherwise one ``dataset``
lineage source is registered and the rows are written in batches, one
``BatchScope`` (one transaction) per batch; a failing batch fails the job and keeps
the ids of the batches committed before it.

Patterns: Command Handler, Unit of Work, Policy, Strategy, Factory, Domain Events.
"""

import hashlib
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.exchange.application.authorisation import (
    export_job_policy,
    export_policy,
    import_policy,
)
from yakhnama.modules.exchange.application.commands import (
    CancelExport,
    RequestExport,
    RequestImport,
    RunExport,
    RunImport,
)
from yakhnama.modules.exchange.application.dto import ExportSummary
from yakhnama.modules.exchange.application.formats import (
    ExportRow,
    FormatAdapterRegistry,
    ReportExportRow,
)
from yakhnama.modules.exchange.application.ports import (
    RUN_EXPORT_TASK,
    RUN_IMPORT_TASK,
    SIDECAR_MEDIA_TYPE,
    ActorLookup,
    ArtifactStore,
    BackfillReferenceChecker,
    ExchangeUnitOfWork,
    ExchangeUnitOfWorkFactory,
    ExportRowSource,
    HistoricalEventWriter,
    LineageSource,
    LineageSourceRegistrar,
    export_artifact_key,
    export_sidecar_key,
    inline_import_key,
)
from yakhnama.modules.exchange.application.streams import (
    INTEGRITY_REASON,
    SIZE_LIMIT_REASON,
    MeteredSink,
    MeteredSource,
)
from yakhnama.modules.exchange.domain.backfill import (
    BACKFILL_SCHEMA_VERSION,
    ImportedEventDraft,
    require_backfill_header,
)
from yakhnama.modules.exchange.domain.entities import ExportJob, ImportJob
from yakhnama.modules.exchange.domain.errors import (
    ExportJobNotFoundError,
    ImportContractError,
    ImportJobNotFoundError,
)
from yakhnama.modules.exchange.domain.factories import (
    ExportJobFactory,
    ImportJobFactory,
)
from yakhnama.modules.exchange.domain.value_objects import (
    ARTIFACT_MAX_BYTES,
    DEFAULT_IMPORT_BATCH_SIZE,
    EXPORT_MAX_ROWS,
    EXPORT_SCHEMA_VERSION,
    IMPORT_MAX_ROWS,
    NO_WRITES,
    PROPOSED_DATASET_LICENCE,
    ArtifactRef,
    ExportDataset,
    ExportRequest,
    ImportRequest,
    ImportWrites,
    JobStatus,
    LicenceStatement,
    MetadataSidecar,
    RowIssue,
    ValidationReport,
    format_moment,
    plan_import_batches,
)
from yakhnama.modules.identity.public import (
    Actor,
    AuthorisationPolicy,
    IsAuthenticated,
    require_allowed,
)
from yakhnama.modules.provenance.public import SourceDetails
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import (
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
    YakhnamaError,
)
from yakhnama.shared_kernel.ids import EntityId, IdGenerator
from yakhnama.shared_kernel.privacy import PublicCoordinatePolicy
from yakhnama.shared_kernel.tasks import TaskQueue
from yakhnama.shared_kernel.value_objects import DatePrecision, DateWithPrecision

CITATION_AUTHOR: Final = "Yakhnama contributors"
"""Author named in every export citation (**proposed** until the maintainer decides
the dataset citation with ADR 0010)."""

# Fixed failure sentences, chosen by error family; see the module docs.
EXPORT_DENIED_SUMMARY: Final = "the requesting user may no longer export this dataset"
EXPORT_TOO_LARGE_SUMMARY: Final = "the export exceeded the size limit"
EXPORT_NOT_FOUND_SUMMARY: Final = "a record the export needed no longer exists"
EXPORT_INVALID_SUMMARY: Final = "the export data did not pass validation"
EXPORT_FAILED_SUMMARY: Final = "the export failed"
EXPORT_INTERNAL_SUMMARY: Final = "the export failed because of an internal error"

IMPORT_DENIED_SUMMARY: Final = "the requesting moderator may no longer import"
IMPORT_HEADER_SUMMARY: Final = (
    "the file header does not follow the import column contract"
)
IMPORT_TOO_MANY_ROWS_SUMMARY: Final = (
    "the file has more data rows than an import accepts"
)
IMPORT_TOO_LARGE_SUMMARY: Final = "the file is larger than its declared size"
IMPORT_INTEGRITY_SUMMARY: Final = (
    "the stored file differs from the one the import was asked for"
)
IMPORT_NOT_FOUND_SUMMARY: Final = "the import file was not found in storage"
IMPORT_INVALID_SUMMARY: Final = "the import file could not be read"
IMPORT_FAILED_SUMMARY: Final = "the import failed"
IMPORT_INTERNAL_SUMMARY: Final = "the import failed because of an internal error"


def batch_failure_summary(batch_number: int) -> str:
    """Return the failure sentence of an import stopped by one batch.

    Args:
        batch_number: The 1-based batch that could not be written.

    Returns:
        A fixed sentence naming only the batch number.
    """
    return f"batch {batch_number} could not be written; the batches before it were kept"


class ExchangeHandlerDependencies:
    """What every exchange command handler is built from.

    Implements: Dependency Injection.

    Attributes:
        uow_factory: Opens an exchange unit of work per call.
        clock: Source of timestamps.
        ids: Source of job, upload and event ids.
        tasks: The task queue the requests enqueue runs on.
        formats: The exporter and importer strategies.
        artifacts: Object storage for export and import files.
        actors: Rebuilds a job's requesting actor when it runs.
    """

    def __init__(  # noqa: PLR0913  # reason: one keyword per injected port, all required
        self,
        *,
        uow_factory: ExchangeUnitOfWorkFactory,
        clock: Clock,
        ids: IdGenerator,
        tasks: TaskQueue,
        formats: FormatAdapterRegistry,
        artifacts: ArtifactStore,
        actors: ActorLookup,
    ) -> None:
        """Group the dependencies.

        Args:
            uow_factory: Opens an exchange unit of work per call.
            clock: Source of timestamps.
            ids: Source of job, upload and event ids.
            tasks: The task queue.
            formats: The exporter and importer strategies.
            artifacts: Object storage.
            actors: Rebuilds a job's requesting actor.
        """
        self.uow_factory = uow_factory
        self.clock = clock
        self.ids = ids
        self.tasks = tasks
        self.formats = formats
        self.artifacts = artifacts
        self.actors = actors


# --------------------------------------------------------------------------- #
# Shared steps                                                                #
# --------------------------------------------------------------------------- #


def _authorise(policy: AuthorisationPolicy, actor: Actor, action: str) -> EntityId:
    require_allowed(policy, actor, action=action)
    if actor.user_id is None:
        # Every exchange policy refuses anonymous actors already; this keeps a
        # future permissive policy from creating a job without an owner.
        message = "an anonymous actor cannot own exchange jobs"
        raise PermissionDeniedError(message, details={"reason": "anonymous"})
    return actor.user_id


async def _load_export(uow: ExchangeUnitOfWork, job_id: EntityId) -> ExportJob:
    job = await uow.export_jobs.get(job_id)
    if job is None:
        raise ExportJobNotFoundError.for_id(job_id)
    return job


async def _load_import(uow: ExchangeUnitOfWork, job_id: EntityId) -> ImportJob:
    job = await uow.import_jobs.get(job_id)
    if job is None:
        raise ImportJobNotFoundError.for_id(job_id)
    return job


async def _current_actor(
    actors: ActorLookup, user_id: EntityId, policy: AuthorisationPolicy, reason: str
) -> Actor:
    actor = await actors.actor_for(user_id)
    if actor is None or not policy.is_allowed(actor):
        raise PermissionDeniedError(reason, details={"reason": "actor_changed"})
    return actor


def _summary_for(
    error: YakhnamaError,
    by_reason: Mapping[str, str],
    by_family: Sequence[tuple[type[YakhnamaError], str]],
    fallback: str,
) -> str:
    reason = error.details.get("reason")
    if isinstance(reason, str) and reason in by_reason:
        return by_reason[reason]
    for family, summary in by_family:
        if isinstance(error, family):
            return summary
    return fallback


def export_failure_summary(error: YakhnamaError) -> str:
    """Return the fixed sentence recorded on an export that failed with ``error``.

    Args:
        error: The deliberate failure.

    Returns:
        A sentence chosen by the error's family, never its text.
    """
    return _summary_for(
        error,
        {SIZE_LIMIT_REASON: EXPORT_TOO_LARGE_SUMMARY},
        (
            (PermissionDeniedError, EXPORT_DENIED_SUMMARY),
            (NotFoundError, EXPORT_NOT_FOUND_SUMMARY),
            (ValidationError, EXPORT_INVALID_SUMMARY),
        ),
        EXPORT_FAILED_SUMMARY,
    )


def import_failure_summary(error: YakhnamaError) -> str:
    """Return the fixed sentence recorded on an import that failed with ``error``.

    Args:
        error: The deliberate failure.

    Returns:
        A sentence chosen by the error's family, never its text.
    """
    if isinstance(error, ImportContractError):
        return (
            IMPORT_TOO_MANY_ROWS_SUMMARY
            if "max_rows" in error.details
            else IMPORT_HEADER_SUMMARY
        )
    return _summary_for(
        error,
        {
            SIZE_LIMIT_REASON: IMPORT_TOO_LARGE_SUMMARY,
            INTEGRITY_REASON: IMPORT_INTEGRITY_SUMMARY,
        },
        (
            (PermissionDeniedError, IMPORT_DENIED_SUMMARY),
            (NotFoundError, IMPORT_NOT_FOUND_SUMMARY),
            (ValidationError, IMPORT_INVALID_SUMMARY),
        ),
        IMPORT_FAILED_SUMMARY,
    )


def export_citation(
    job: ExportJob, generated_at: DateWithPrecision, licence: LicenceStatement
) -> str:
    """Return how to cite one export file.

    Args:
        job: The export job.
        generated_at: When the file was generated (``exact``).
        licence: The data licence.

    Returns:
        One line naming the author, the dataset, the job, the filters (codes and
        numbers only), the generation time and the licence with its status.
    """
    return (
        f"{CITATION_AUTHOR} ({generated_at.value.year}). Yakhnama "
        f"{job.dataset.value} export {job.id} ({job.filters.to_citation_fragment()}), "
        f"generated {format_moment(generated_at)}. Licence: {licence.label}."
    )


# --------------------------------------------------------------------------- #
# Exports                                                                     #
# --------------------------------------------------------------------------- #


class RequestExportHandler:
    """Queue an export after checking who may export the dataset.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: ExchangeHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
        """
        self._deps = dependencies

    async def __call__(self, command: RequestExport) -> EntityId:
        """Store the queued job and enqueue ``exchange.run_export``.

        Args:
            command: The validated command.

        Returns:
            The export job's id.

        Raises:
            PermissionDeniedError: If the actor may not export the dataset.
            UnsupportedFormatError: If no exporter is registered for the format.
        """
        deps = self._deps
        requested_by = _authorise(
            export_policy(command.dataset),
            command.actor,
            f"export {command.dataset.value}",
        )
        deps.formats.exporter(command.format)
        async with deps.uow_factory() as uow:
            job = (
                ExportJobFactory()
                .request(
                    requested_by,
                    ExportRequest(
                        dataset=command.dataset,
                        format=command.format,
                        filters=command.filters,
                    ),
                    clock=deps.clock,
                    ids=deps.ids,
                )
                .record_into(uow)
            )
            await uow.export_jobs.add(job)
            await uow.commit()
        await deps.tasks.enqueue(
            RUN_EXPORT_TASK,
            {"export_job_id": job.id},
            idempotency_key=f"{RUN_EXPORT_TASK}:{job.id}",
        )
        return job.id


class CancelExportHandler:
    """Cancel a queued export on behalf of its owner or a moderator.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: ExchangeHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
        """
        self._deps = dependencies

    async def __call__(self, command: CancelExport) -> None:
        """Cancel the export.

        Args:
            command: The validated command.

        Raises:
            PermissionDeniedError: If the actor is anonymous.
            ExportJobNotFoundError: If the job does not exist or belongs to
                someone else and the actor may not moderate (so job ids cannot be
                probed).
            JobStateError: If a worker has started the job or it is final.
        """
        deps = self._deps
        _authorise(IsAuthenticated(), command.actor, "cancel exports")
        async with deps.uow_factory() as uow:
            job = await _load_export(uow, command.job_id)
            if not export_job_policy(job.requested_by).is_allowed(command.actor):
                raise ExportJobNotFoundError.for_id(command.job_id)
            cancelled = job.cancel(clock=deps.clock, ids=deps.ids).record_into(uow)
            await uow.export_jobs.save(cancelled)
            await uow.commit()


class _RowStream:
    """Feeds an exporter the rows of one job, counted, checked and made public.

    Implements: Decorator (of the row source's iterator).

    Attributes:
        row_count: Rows handed to the exporter so far.
    """

    def __init__(
        self,
        rows: AsyncIterator[ExportRow],
        *,
        dataset: ExportDataset,
        coordinates: PublicCoordinatePolicy,
    ) -> None:
        self._rows = rows
        self._dataset = dataset
        self._coordinates = coordinates
        self.row_count = 0

    async def __aiter__(self) -> AsyncIterator[ExportRow]:
        async for row in self._rows:
            if row.dataset is not self._dataset:
                message = "the row source produced a row of another dataset"
                raise ValidationError(message, details={"dataset": row.dataset.value})
            if self.row_count >= EXPORT_MAX_ROWS:
                message = "the export has more rows than allowed"
                raise ValidationError(
                    message,
                    details={"reason": SIZE_LIMIT_REASON, "max_rows": EXPORT_MAX_ROWS},
                )
            self.row_count += 1
            if isinstance(row, ReportExportRow):
                # Rounding again is a no-op for a correct adapter and a safeguard
                # against leaking a reporter's exact position otherwise.
                yield ReportExportRow.model_validate(
                    {
                        **dict(row),
                        "coordinates": self._coordinates.apply(row.coordinates),
                    }
                )
            else:
                yield row


class RunExportHandler:
    """Write an export's file and sidecar to storage and complete the job.

    The file goes to ``exports/<job_id>/<dataset><extension>`` and the sidecar
    to ``exports/<job_id>/<dataset>.sidecar.json``; the sidecar is also kept on the
    job. Its checksum and size are measured around the exporter (``MeteredSink``).

    Implements: Command Handler.
    """

    def __init__(  # noqa: PLR0913  # reason: one keyword per injected port, all required
        self,
        dependencies: ExchangeHandlerDependencies,
        *,
        rows: ExportRowSource,
        coordinates: PublicCoordinatePolicy,
        generator: str,
        licence: LicenceStatement = PROPOSED_DATASET_LICENCE,
        max_bytes: int = ARTIFACT_MAX_BYTES,
    ) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
            rows: Streams the rows through the owning modules' facades.
            coordinates: Rounds report positions (``PublicCoordinatePolicy`` from
                settings).
            generator: ``yakhnama/<version>`` written into every sidecar.
            licence: The data licence; the proposed one until ADR 0010 is
                accepted and settings provide it.
            max_bytes: The largest file an export may write.
        """
        self._deps = dependencies
        self._rows = rows
        self._coordinates = coordinates
        self._generator = generator
        self._licence = licence
        self._max_bytes = max_bytes

    async def __call__(self, command: RunExport) -> ExportJob | None:
        """Run the export.

        Args:
            command: The job to run.

        Returns:
            The completed or failed job, or ``None`` if it was final or running
            already (a repeated delivery).

        Raises:
            ExportJobNotFoundError: If the job does not exist.
            Exception: Anything unexpected, after the job was recorded as failed.
        """
        job = await self._start(command.job_id)
        if job is None:
            return None
        try:
            artifact, sidecar = await self._produce(job)
            finished = await self._complete(job.id, artifact, sidecar)
        except YakhnamaError as error:
            return await self._fail(job.id, export_failure_summary(error))
        except Exception:
            await self._fail(job.id, EXPORT_INTERNAL_SUMMARY)
            raise
        return finished

    async def _start(self, job_id: EntityId) -> ExportJob | None:
        deps = self._deps
        async with deps.uow_factory() as uow:
            job = await _load_export(uow, job_id)
            if job.status is not JobStatus.QUEUED:
                return None
            started = job.start(clock=deps.clock, ids=deps.ids).record_into(uow)
            await uow.export_jobs.save(started)
            await uow.commit()
        return started

    def _select(self, job: ExportJob, actor: Actor) -> AsyncIterator[ExportRow]:
        if job.dataset is ExportDataset.EVENTS:
            return self._rows.events(job.filters, actor=actor)
        if job.dataset is ExportDataset.CLAIMS:
            return self._rows.claims(job.filters, actor=actor)
        return self._rows.reports(job.filters, actor=actor)

    async def _produce(self, job: ExportJob) -> tuple[ArtifactRef, MetadataSidecar]:
        deps = self._deps
        actor = await _current_actor(
            deps.actors,
            job.requested_by,
            export_policy(job.dataset),
            "the requesting user may no longer export this dataset",
        )
        exporter = deps.formats.exporter(job.format)
        descriptor = deps.formats.export_descriptor(job.format)
        key = export_artifact_key(job.id, job.dataset, descriptor.extension)
        stream = _RowStream(
            self._select(job, actor), dataset=job.dataset, coordinates=self._coordinates
        )
        async with deps.artifacts.open_sink(key, descriptor.media_type) as sink:
            metered = MeteredSink(sink, max_bytes=self._max_bytes)
            await exporter.write(job.dataset, aiter(stream), metered)
        summary = ExportSummary(
            row_count=stream.row_count,
            sha256=metered.sha256,
            byte_size=metered.byte_size,
        )
        artifact = ArtifactRef(
            object_key=key,
            byte_size=summary.byte_size,
            sha256=summary.sha256,
            media_type=descriptor.media_type,
        )
        generated_at = DateWithPrecision(
            value=deps.clock.now(), precision=DatePrecision.EXACT
        )
        sidecar = MetadataSidecar(
            licence=self._licence,
            generated_at=generated_at.value,
            dataset=job.dataset,
            format=job.format,
            filters=job.filters,
            schema_version=EXPORT_SCHEMA_VERSION,
            citation=export_citation(job, generated_at, self._licence),
            row_count=summary.row_count,
            checksum=summary.sha256,
            generator=self._generator,
        )
        await deps.artifacts.put_bytes(
            export_sidecar_key(job.id, job.dataset),
            sidecar.to_json_bytes(),
            SIDECAR_MEDIA_TYPE,
        )
        return artifact, sidecar

    async def _complete(
        self, job_id: EntityId, artifact: ArtifactRef, sidecar: MetadataSidecar
    ) -> ExportJob:
        deps = self._deps
        async with deps.uow_factory() as uow:
            job = await _load_export(uow, job_id)
            completed = job.complete(
                artifact, sidecar, clock=deps.clock, ids=deps.ids
            ).record_into(uow)
            await uow.export_jobs.save(completed)
            await uow.commit()
        return completed

    async def _fail(self, job_id: EntityId, summary: str) -> ExportJob:
        deps = self._deps
        async with deps.uow_factory() as uow:
            job = await _load_export(uow, job_id)
            if job.status.is_final:
                return job
            failed = job.fail(summary, clock=deps.clock, ids=deps.ids).record_into(uow)
            await uow.export_jobs.save(failed)
            await uow.commit()
        return failed


# --------------------------------------------------------------------------- #
# Imports                                                                     #
# --------------------------------------------------------------------------- #


class RequestImportHandler:
    """Queue an import of a stored or inline file after checking the moderator.

    Implements: Command Handler.
    """

    def __init__(self, dependencies: ExchangeHandlerDependencies) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
        """
        self._deps = dependencies

    async def __call__(self, command: RequestImport) -> EntityId:
        """Store the file if sent inline, store the queued job, enqueue its run.

        Args:
            command: The validated command.

        Returns:
            The import job's id.

        Raises:
            PermissionDeniedError: If the actor may not import.
            UnsupportedFormatError: If no importer is registered for the format.
        """
        deps = self._deps
        requested_by = _authorise(import_policy(), command.actor, "import records")
        descriptor = deps.formats.import_descriptor(command.format)
        artifact = command.artifact
        if artifact is None:
            # The command guarantees exactly one of artifact and inline_csv.
            content = command.inline_csv or b""
            key = inline_import_key(deps.ids.new_id(), descriptor.extension)
            await deps.artifacts.put_bytes(key, content, descriptor.media_type)
            artifact = ArtifactRef(
                object_key=key,
                byte_size=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
                media_type=descriptor.media_type,
            )
        async with deps.uow_factory() as uow:
            job = (
                ImportJobFactory()
                .request(
                    requested_by,
                    ImportRequest(
                        format=command.format,
                        source_artifact=artifact,
                        dry_run=command.dry_run,
                    ),
                    clock=deps.clock,
                    ids=deps.ids,
                )
                .record_into(uow)
            )
            await uow.import_jobs.add(job)
            await uow.commit()
        await deps.tasks.enqueue(
            RUN_IMPORT_TASK,
            {"import_job_id": job.id},
            idempotency_key=f"{RUN_IMPORT_TASK}:{job.id}",
        )
        return job.id


class _ParsedFile(BaseModel):
    """The outcome of reading an import file: its report and its writable rows.

    Implements: Value Object.

    Attributes:
        report: The row-level validation report.
        drafts: ``(row_number, draft)`` of every row without a blocking issue.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    report: ValidationReport
    drafts: tuple[tuple[int, ImportedEventDraft], ...]


class RunImportHandler:
    """Validate an import file and, unless it is a dry run, write it in batches.

    Implements: Command Handler.
    """

    def __init__(  # reason: one keyword per injected port, all required
        self,
        dependencies: ExchangeHandlerDependencies,
        *,
        references: BackfillReferenceChecker,
        lineage: LineageSourceRegistrar,
        writer: HistoricalEventWriter,
        batch_size: int = DEFAULT_IMPORT_BATCH_SIZE,
    ) -> None:
        """Create the handler.

        Args:
            dependencies: The module's ports.
            references: Checks hazard, metric and place codes of each row.
            lineage: Registers the import's ``dataset`` source.
            writer: Opens the batches events and claims are written in.
            batch_size: Rows per batch (``DEFAULT_IMPORT_BATCH_SIZE``,
                **proposed**).
        """
        self._deps = dependencies
        self._references = references
        self._lineage = lineage
        self._writer = writer
        self._batch_size = batch_size

    async def __call__(self, command: RunImport) -> ImportJob | None:
        """Run the import.

        Args:
            command: The job to run.

        Returns:
            The completed or failed job, or ``None`` if it was final or running
            already (a repeated delivery).

        Raises:
            ImportJobNotFoundError: If the job does not exist.
            Exception: Anything unexpected, after the job was recorded as failed.
        """
        job = await self._start(command.job_id)
        if job is None:
            return None
        try:
            finished = await self._run(job)
        except YakhnamaError as error:
            return await self._fail(job.id, import_failure_summary(error))
        except Exception:
            await self._fail(job.id, IMPORT_INTERNAL_SUMMARY)
            raise
        return finished

    async def _start(self, job_id: EntityId) -> ImportJob | None:
        deps = self._deps
        async with deps.uow_factory() as uow:
            job = await _load_import(uow, job_id)
            if job.status is not JobStatus.QUEUED:
                return None
            started = job.start(clock=deps.clock, ids=deps.ids).record_into(uow)
            await uow.import_jobs.save(started)
            await uow.commit()
        return started

    async def _run(self, job: ImportJob) -> ImportJob:
        actor = await _current_actor(
            self._deps.actors,
            job.requested_by,
            import_policy(),
            "the requesting moderator may no longer import",
        )
        parsed = await self._parse(job)
        if job.dry_run or parsed.report.has_blocking_errors or not parsed.drafts:
            return await self._complete(job.id, parsed.report, NO_WRITES)
        lineage_source_id = await self._lineage.register(
            self._lineage_source(job), actor=actor
        )
        return await self._write(job, parsed, lineage_source_id, actor)

    async def _parse(self, job: ImportJob) -> _ParsedFile:
        deps = self._deps
        importer = deps.formats.importer(job.format)
        declared = job.source_artifact
        issues: list[RowIssue] = []
        drafts: list[tuple[int, ImportedEventDraft]] = []
        rows_seen = 0
        async with deps.artifacts.open_source(declared.object_key) as raw:
            source = MeteredSource(raw, max_bytes=declared.byte_size)
            require_backfill_header(await importer.header(source))
            async for row_number, cells in importer.read(source):
                rows_seen += 1
                if rows_seen > IMPORT_MAX_ROWS:
                    raise ImportContractError.too_many_rows(IMPORT_MAX_ROWS)
                if row_number != rows_seen:
                    message = "the importer numbered the data rows out of order"
                    raise ValidationError(message, details={"row_number": rows_seen})
                outcome = ImportedEventDraft.from_flat_row(row_number, cells)
                if not isinstance(outcome, ImportedEventDraft):
                    issues.extend(outcome)
                    continue
                found = await self._references.check(row_number, outcome)
                issues.extend(found)
                if not any(issue.is_blocking for issue in found):
                    drafts.append((row_number, outcome))
            await source.drain()
        source.verify(declared)
        return _ParsedFile(
            report=ValidationReport.from_issues(rows_seen, issues),
            drafts=tuple(drafts),
        )

    def _lineage_source(self, job: ImportJob) -> LineageSource:
        requested = format_moment(
            DateWithPrecision(value=job.requested_at, precision=DatePrecision.DAY)
        )
        return LineageSource(
            import_job_id=job.id,
            details=SourceDetails(
                title=f"Yakhnama historical import {job.id}",
                citation=(
                    f"Historical records imported into Yakhnama from a "
                    f"{job.format.value} file by import job {job.id} (backfill "
                    f"schema {BACKFILL_SCHEMA_VERSION}), requested {requested}."
                ),
            ),
        )

    async def _write(
        self,
        job: ImportJob,
        parsed: _ParsedFile,
        lineage_source_id: EntityId,
        actor: Actor,
    ) -> ImportJob:
        drafts = dict(parsed.drafts)
        created: list[EntityId] = []
        applied = 0
        for batch in plan_import_batches(parsed.report.rows_seen, self._batch_size):
            try:
                async with self._writer.batch() as scope:
                    batch_ids = [
                        await scope.create(
                            drafts[row_number],
                            lineage_source_id=lineage_source_id,
                            actor=actor,
                        )
                        for row_number in range(batch.first_row, batch.last_row + 1)
                    ]
                    await scope.commit()
            except YakhnamaError:
                return await self._fail(
                    job.id,
                    batch_failure_summary(batch.number),
                    report=parsed.report,
                    writes=ImportWrites(
                        created_ids=tuple(created),
                        lineage_source_id=lineage_source_id,
                        batches_applied=applied,
                    ),
                )
            except Exception:
                await self._fail(
                    job.id,
                    IMPORT_INTERNAL_SUMMARY,
                    report=parsed.report,
                    writes=ImportWrites(
                        created_ids=tuple(created),
                        lineage_source_id=lineage_source_id,
                        batches_applied=applied,
                    ),
                )
                raise
            created.extend(batch_ids)
            applied += 1
        return await self._complete(
            job.id,
            parsed.report,
            ImportWrites(
                created_ids=tuple(created),
                lineage_source_id=lineage_source_id,
                batches_applied=applied,
            ),
        )

    async def _complete(
        self, job_id: EntityId, report: ValidationReport, writes: ImportWrites
    ) -> ImportJob:
        deps = self._deps
        async with deps.uow_factory() as uow:
            job = await _load_import(uow, job_id)
            completed = job.complete(
                report, writes, clock=deps.clock, ids=deps.ids
            ).record_into(uow)
            await uow.import_jobs.save(completed)
            await uow.commit()
        return completed

    async def _fail(
        self,
        job_id: EntityId,
        summary: str,
        *,
        report: ValidationReport | None = None,
        writes: ImportWrites = NO_WRITES,
    ) -> ImportJob:
        deps = self._deps
        async with deps.uow_factory() as uow:
            job = await _load_import(uow, job_id)
            if job.status.is_final:
                return job
            failed = job.fail(
                summary, report, writes, clock=deps.clock, ids=deps.ids
            ).record_into(uow)
            await uow.import_jobs.save(failed)
            await uow.commit()
        return failed
