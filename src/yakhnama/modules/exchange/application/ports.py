"""Ports the exchange application layer depends on, and the storage key layout.

Only ``yakhnama.main`` and ``yakhnama.platform.container`` bind these protocols
(``AGENTS.md`` §2.1). The ports towards other modules (rows to export, historical
events to write, the lineage source, reference checks, the requesting actor) are
declared here in this module's terms and bound in the composition root to adapters
over those modules' facades, so the exchange application never calls another
module's handler directly and no import cycle can form.

Batch atomicity of an import: ``HistoricalEventWriter.batch`` opens one
``BatchScope`` per batch. The adapter makes everything written through the scope
(the rows' own sources, the events, their claims, the verification cases) one
database transaction, by binding the events, impacts, provenance and verification
units of work it drives to a single session for the scope's lifetime; ``commit``
commits it and leaving the block without ``commit`` rolls all of it back. The
import handler therefore never sees half a batch.

Patterns: Repository (port side), Unit of Work, Query Service, Adapter (port side).
"""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Final, Protocol

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.exchange.application.dto import (
    ExportJobDetail,
    ExportJobSummary,
    ImportJobDetail,
)
from yakhnama.modules.exchange.application.formats import (
    BinarySink,
    BinarySource,
    ClaimExportRow,
    EventExportRow,
    ReportExportRow,
)
from yakhnama.modules.exchange.domain.backfill import ImportedEventDraft
from yakhnama.modules.exchange.domain.entities import ExportJob, ImportJob
from yakhnama.modules.exchange.domain.value_objects import (
    ExportDataset,
    ExportFilters,
    ObjectKey,
    RowIssue,
)
from yakhnama.modules.identity.public import Actor
from yakhnama.modules.media.public import PresignedUpload
from yakhnama.modules.provenance.public import SourceDetails
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import Page, PageRequest
from yakhnama.shared_kernel.uow import UnitOfWork, UnitOfWorkFactory

RUN_EXPORT_TASK: Final = "exchange.run_export"
"""Task name under which ``RunExportHandler`` runs; payload ``{export_job_id}``."""

RUN_IMPORT_TASK: Final = "exchange.run_import"
"""Task name under which ``RunImportHandler`` runs; payload ``{import_job_id}``."""

EXPORT_KEY_PREFIX: Final = "exports/"
IMPORT_KEY_PREFIX: Final = "imports/"
"""Every import file lives under this prefix (**proposed**): an import can only read
files uploaded for imports, never an export or a media object."""

SIDECAR_MEDIA_TYPE: Final = "application/json"


def export_artifact_key(
    job_id: EntityId, dataset: ExportDataset, extension: str
) -> str:
    """Return where an export's file is stored.

    Args:
        job_id: The export job.
        dataset: Its dataset, used as the file's base name.
        extension: The format's extension with its dot, such as ``.geojson``.

    Returns:
        ``exports/<job_id>/<dataset><extension>``.
    """
    return f"{EXPORT_KEY_PREFIX}{job_id}/{dataset.value}{extension}"


def export_sidecar_key(job_id: EntityId, dataset: ExportDataset) -> str:
    """Return where an export's metadata sidecar is stored.

    Args:
        job_id: The export job.
        dataset: Its dataset.

    Returns:
        ``exports/<job_id>/<dataset>.sidecar.json``.
    """
    return f"{EXPORT_KEY_PREFIX}{job_id}/{dataset.value}.sidecar.json"


def inline_import_key(upload_id: EntityId, extension: str) -> str:
    """Return where a file sent inline with an import request is stored.

    Args:
        upload_id: A fresh id for the upload.
        extension: The format's extension with its dot, such as ``.csv``.

    Returns:
        ``imports/<upload_id>/source<extension>``.
    """
    return f"{IMPORT_KEY_PREFIX}{upload_id}/source{extension}"


# --------------------------------------------------------------------------- #
# Repositories and unit of work                                               #
# --------------------------------------------------------------------------- #


class ExportJobRepository(Protocol):
    """Loads and stages ``ExportJob`` aggregates inside one unit of work.

    Implements: Repository (port side).
    """

    async def get(self, job_id: EntityId) -> ExportJob | None:
        """Return one export job.

        Args:
            job_id: The job.

        Returns:
            The aggregate, or ``None``.
        """
        ...

    async def add(self, job: ExportJob) -> None:
        """Stage a new export job.

        Args:
            job: The job at version 1.

        Raises:
            ConflictError: If a job with that id exists.
        """
        ...

    async def save(self, job: ExportJob) -> None:
        """Stage a changed export job.

        Args:
            job: The new state; its version is greater than the loaded one.

        Raises:
            NotFoundError: If no job with that id is stored.
            ConflictError: If the stored row changed since it was loaded.
        """
        ...


class ImportJobRepository(Protocol):
    """Loads and stages ``ImportJob`` aggregates inside one unit of work.

    Implements: Repository (port side).
    """

    async def get(self, job_id: EntityId) -> ImportJob | None:
        """Return one import job.

        Args:
            job_id: The job.

        Returns:
            The aggregate, or ``None``.
        """
        ...

    async def add(self, job: ImportJob) -> None:
        """Stage a new import job.

        Args:
            job: The job at version 1.

        Raises:
            ConflictError: If a job with that id exists.
        """
        ...

    async def save(self, job: ImportJob) -> None:
        """Stage a changed import job.

        Args:
            job: The new state; its version is greater than the loaded one.

        Raises:
            NotFoundError: If no job with that id is stored.
            ConflictError: If the stored row changed since it was loaded.
        """
        ...


class ExchangeUnitOfWork(UnitOfWork, Protocol):
    """Transaction boundary exposing the exchange repositories.

    Implements: Unit of Work.
    """

    @property
    def export_jobs(self) -> ExportJobRepository:
        """Return the export job repository bound to this transaction."""
        ...

    @property
    def import_jobs(self) -> ImportJobRepository:
        """Return the import job repository bound to this transaction."""
        ...


type ExchangeUnitOfWorkFactory = UnitOfWorkFactory[ExchangeUnitOfWork]
"""Opens a fresh exchange unit of work per use case."""


# --------------------------------------------------------------------------- #
# Object storage                                                              #
# --------------------------------------------------------------------------- #


class ArtifactStore(Protocol):
    """Stores export files and reads import files in object storage.

    Keys are built by this module (see the key functions above), never from a
    user-supplied name.

    Implements: Adapter (port side).
    """

    def open_sink(
        self, key: ObjectKey, media_type: str
    ) -> AbstractAsyncContextManager[BinarySink]:
        """Open a new object for writing, streamed (multipart upload).

        The object becomes visible only when the block exits without an error;
        an error aborts the upload so no partial object remains.

        Args:
            key: The object key.
            media_type: Its ``Content-Type``.

        Returns:
            An async context manager yielding the sink.
        """
        ...

    def open_source(self, key: ObjectKey) -> AbstractAsyncContextManager[BinarySource]:
        """Open an existing object for reading, streamed.

        Args:
            key: The object key.

        Returns:
            An async context manager yielding the source.

        Raises:
            NotFoundError: If no object has that key (on entering the block).
        """
        ...

    async def put_bytes(self, key: ObjectKey, data: bytes, media_type: str) -> None:
        """Store a small object in one request.

        Args:
            key: The object key.
            data: The whole content.
            media_type: Its ``Content-Type``.
        """
        ...

    async def presign_download(self, key: ObjectKey, *, file_name: str) -> str:
        """Return a short-lived link to download an object.

        Args:
            key: The object key.
            file_name: The name the browser saves it under
                (``Content-Disposition``); built by this module.

        Returns:
            The presigned URL; its lifetime is the adapter's configuration.
        """
        ...

    async def presign_upload(
        self, key: ObjectKey, media_type: str, max_bytes: int
    ) -> PresignedUpload:
        """Return a short-lived presigned ``PUT`` for an import file.

        ``key`` is always an import key (``inline_import_key``), never an export:
        the grant lets a moderator's client write the file an import will read,
        and nothing else. Like media's ``presign_put``, the URL is bound to the
        content type and caps the size at ``max_bytes``, so storage itself refuses
        a larger file.

        Args:
            key: The object key, under ``IMPORT_KEY_PREFIX``.
            media_type: The ``Content-Type`` the upload must carry.
            max_bytes: Largest accepted file.

        Returns:
            The URL, the headers the upload must carry and the expiry.
        """
        ...


# --------------------------------------------------------------------------- #
# Towards other modules                                                       #
# --------------------------------------------------------------------------- #


class ExportRowSource(Protocol):
    """Streams the rows of a dataset through the owning modules' facades.

    Each method applies ``filters`` and **the same visibility rules as the API**
    for ``actor``: through ``EventRecordQueryService`` (non-moderators see only
    published and verified events), ``EventImpactsQueryService`` (claims of events
    the actor may see) and ``AuthorisedReportQueryService`` (rounded positions).
    Adapters page through the facades; they never read another module's tables.

    Implements: Adapter (port side).
    """

    def events(
        self, filters: ExportFilters, *, actor: Actor
    ) -> AsyncIterator[EventExportRow]:
        """Yield every event matching ``filters`` that ``actor`` may see.

        Args:
            filters: The export's filters.
            actor: The requesting user, rebuilt at run time.

        Yields:
            The rows, in a stable order (newest first, then by id).
        """
        ...

    def claims(
        self, filters: ExportFilters, *, actor: Actor
    ) -> AsyncIterator[ClaimExportRow]:
        """Yield every claim of the events matching ``filters`` that ``actor`` may see.

        Args:
            filters: The export's filters, applied to the claims' events.
            actor: The requesting user, rebuilt at run time.

        Yields:
            The rows, in a stable order.
        """
        ...

    def reports(
        self, filters: ExportFilters, *, actor: Actor
    ) -> AsyncIterator[ReportExportRow]:
        """Yield every report matching ``filters``, positions rounded.

        Args:
            filters: The export's filters.
            actor: The requesting moderator, rebuilt at run time.

        Yields:
            The rows, in a stable order.
        """
        ...


class ActorLookup(Protocol):
    """Rebuilds the actor a job runs for, through the identity facade.

    Jobs store only the requesting user's id; the actor (roles, memberships) is
    rebuilt when the job runs, so a user suspended or stripped of a role meanwhile
    no longer gets the data.

    Implements: Adapter (port side).
    """

    async def actor_for(self, user_id: EntityId) -> Actor | None:
        """Return the current actor of a user.

        Args:
            user_id: The user.

        Returns:
            The actor, or ``None`` if the user does not exist or is suspended.
        """
        ...


class BackfillReferenceChecker(Protocol):
    """Checks a draft's codes against the registries it refers to.

    ``from_flat_row`` checks only the shape of codes; this port asks the hazards,
    impacts and geography facades whether the hazard type is active, each claim's
    metric is active and suits the value's kind, unit and currency, and each place
    code exists. A row it rejects is never written, so a real import does not fail
    half way over a code the dry run could have reported.

    Implements: Adapter (port side).
    """

    async def check(
        self, row_number: int, draft: ImportedEventDraft
    ) -> tuple[RowIssue, ...]:
        """Return every reference problem of one draft.

        Args:
            row_number: The draft's 1-based data row, used in every issue.
            draft: The draft.

        Returns:
            The issues, each naming its column (for example
            ``claim_2_metric_code``); empty if every reference resolves. Messages
            never quote a cell.
        """
        ...


class LineageSource(BaseModel):
    """The ``dataset`` source registered for one real import.

    Implements: DTO.

    Attributes:
        import_job_id: The import it records.
        details: Title and citation of the source.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    import_job_id: EntityId
    details: SourceDetails


class LineageSourceRegistrar(Protocol):
    """Registers the lineage source of an import through the provenance facade.

    Bound to an adapter sending ``RegisterSource`` with ``source_type=dataset``.

    Implements: Adapter (port side).
    """

    async def register(self, source: LineageSource, *, actor: Actor) -> EntityId:
        """Register the lineage source.

        Args:
            source: What to register.
            actor: The requesting moderator.

        Returns:
            The new source's id.

        Raises:
            PermissionDeniedError: If the actor may not register it.
        """
        ...


class BatchScope(Protocol):
    """One import batch: every write through it commits or rolls back together.

    Implements: Unit of Work.
    """

    async def create(
        self,
        draft: ImportedEventDraft,
        *,
        lineage_source_id: EntityId,
        actor: Actor,
    ) -> EntityId:
        """Write one historical event with its source and claims.

        The adapter registers the row's own source (``draft.source``), sends
        ``CreateHistoricalEvent`` citing it and the lineage source, then one
        ``RecordImpactClaim`` per claim citing the row's source.

        Args:
            draft: The validated row.
            lineage_source_id: The import's ``dataset`` source.
            actor: The requesting moderator.

        Returns:
            The new event's id.

        Raises:
            YakhnamaError: If any write is refused; the batch must then be left
                without ``commit``.
        """
        ...

    async def commit(self) -> None:
        """Commit every write of the batch at once."""
        ...


class HistoricalEventWriter(Protocol):
    """Opens import batches over the events, impacts and provenance facades.

    Implements: Adapter (port side).
    """

    def batch(self) -> AbstractAsyncContextManager[BatchScope]:
        """Open one batch; see the module docs for its atomicity.

        Returns:
            An async context manager yielding the scope; leaving it without
            ``commit`` rolls back every write made through it.
        """
        ...


# --------------------------------------------------------------------------- #
# Read side                                                                   #
# --------------------------------------------------------------------------- #


class ExchangeQueryService(Protocol):
    """Read port for export and import jobs.

    Implements: Query Service.
    """

    async def get_export_job(self, job_id: EntityId) -> ExportJobDetail | None:
        """Return one export job.

        Args:
            job_id: The job.

        Returns:
            Its detail view, or ``None``.
        """
        ...

    async def list_export_jobs(
        self, requested_by: EntityId | None, page: PageRequest
    ) -> Page[ExportJobSummary]:
        """Return one page of export jobs, newest request first, then by id.

        Args:
            requested_by: Only this user's jobs, or every job when ``None``.
            page: Page size and cursor.

        Returns:
            The page.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        ...

    async def get_import_job(self, job_id: EntityId) -> ImportJobDetail | None:
        """Return one import job.

        Args:
            job_id: The job.

        Returns:
            Its detail view, or ``None``.
        """
        ...
