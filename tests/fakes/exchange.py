"""Fakes of the ``exchange`` module's ports and format strategies.

Repositories stage writes until the unit of work commits, as a rolled-back
transaction would leave the tables unchanged. ``InMemoryArtifactStore`` keeps
objects in a dictionary and, like a multipart upload, stores an object only when
its sink closes without an error. ``FakeHistoricalEventWriter`` keeps a batch's
drafts only when the batch commits, and can be told to fail a given batch.
``FakeExporter`` and ``FakeImporter`` stand in for a ``csv`` format: the exporter
writes one JSON line per row, the importer reads real CSV with the standard library.

Patterns: Fake.
"""

import csv
import io
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Final

from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.uow import InMemoryUnitOfWork
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
    ExportRow,
    ReportExportRow,
)
from yakhnama.modules.exchange.application.ports import BatchScope, LineageSource
from yakhnama.modules.exchange.domain.backfill import ImportedEventDraft
from yakhnama.modules.exchange.domain.entities import ExportJob, ImportJob
from yakhnama.modules.exchange.domain.value_objects import (
    ExportDataset,
    ExportFilters,
    ExportFormat,
    ImportFormat,
    RowIssue,
    ValidationSeverity,
)
from yakhnama.modules.identity.public import Actor
from yakhnama.modules.media.public import HttpHeader, PresignedUpload
from yakhnama.shared_kernel.errors import (
    ConflictError,
    InvariantViolationError,
    NotFoundError,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import (
    CursorPayload,
    Page,
    PageRequest,
    encode_cursor,
)

PRESIGN_BASE_URL: Final = "https://storage.test/"
UPLOAD_EXPIRES_AT: Final = datetime(2030, 1, 1, tzinfo=UTC)
"""Expiry of every fake upload grant: fixed, so responses are comparable."""
READ_CHUNK_BYTES: Final = 7
"""Deliberately tiny, so importers and the metering see many short reads."""

# --------------------------------------------------------------------------- #
# Repositories and unit of work                                               #
# --------------------------------------------------------------------------- #


class InMemoryExportJobRepository:
    """``ExportJobRepository`` over a dictionary keyed by job id.

    Implements: Fake (of Repository).

    Attributes:
        committed: The stored jobs, as a committed transaction left them.
    """

    def __init__(self, jobs: Iterable[ExportJob] = ()) -> None:
        """Create the repository.

        Args:
            jobs: Jobs that exist before the test acts.
        """
        self.committed: dict[EntityId, ExportJob] = {job.id: job for job in jobs}
        self._staged: dict[EntityId, ExportJob] = {}

    def _current(self) -> dict[EntityId, ExportJob]:
        return {**self.committed, **self._staged}

    async def get(self, job_id: EntityId) -> ExportJob | None:
        """Return one job, staged changes included.

        Args:
            job_id: The job.

        Returns:
            The job, or ``None``.
        """
        return self._current().get(job_id)

    async def add(self, job: ExportJob) -> None:
        """Stage a new job.

        Args:
            job: The job.

        Raises:
            ConflictError: If the id is taken.
        """
        if job.id in self._current():
            message = "the export job already exists"
            raise ConflictError(message)
        self._staged[job.id] = job

    async def save(self, job: ExportJob) -> None:
        """Stage a changed job, checking its version is newer.

        Args:
            job: The new state.

        Raises:
            NotFoundError: If the job is not stored.
            ConflictError: If the version did not grow.
        """
        stored = self._current().get(job.id)
        if stored is None:
            message = "the export job is not stored"
            raise NotFoundError(message)
        if job.version <= stored.version:
            message = "the export job was changed concurrently"
            raise ConflictError(message)
        self._staged[job.id] = job

    def apply_staged(self) -> None:
        """Make the staged writes permanent; called on commit."""
        self.committed.update(self._staged)
        self._staged.clear()

    def discard_staged(self) -> None:
        """Forget the staged writes; called on rollback."""
        self._staged.clear()


class InMemoryImportJobRepository:
    """``ImportJobRepository`` over a dictionary keyed by job id.

    Implements: Fake (of Repository).

    Attributes:
        committed: The stored jobs, as a committed transaction left them.
    """

    def __init__(self, jobs: Iterable[ImportJob] = ()) -> None:
        """Create the repository.

        Args:
            jobs: Jobs that exist before the test acts.
        """
        self.committed: dict[EntityId, ImportJob] = {job.id: job for job in jobs}
        self._staged: dict[EntityId, ImportJob] = {}

    def _current(self) -> dict[EntityId, ImportJob]:
        return {**self.committed, **self._staged}

    async def get(self, job_id: EntityId) -> ImportJob | None:
        """Return one job, staged changes included.

        Args:
            job_id: The job.

        Returns:
            The job, or ``None``.
        """
        return self._current().get(job_id)

    async def add(self, job: ImportJob) -> None:
        """Stage a new job.

        Args:
            job: The job.

        Raises:
            ConflictError: If the id is taken.
        """
        if job.id in self._current():
            message = "the import job already exists"
            raise ConflictError(message)
        self._staged[job.id] = job

    async def save(self, job: ImportJob) -> None:
        """Stage a changed job, checking its version is newer.

        Args:
            job: The new state.

        Raises:
            NotFoundError: If the job is not stored.
            ConflictError: If the version did not grow.
        """
        stored = self._current().get(job.id)
        if stored is None:
            message = "the import job is not stored"
            raise NotFoundError(message)
        if job.version <= stored.version:
            message = "the import job was changed concurrently"
            raise ConflictError(message)
        self._staged[job.id] = job

    def apply_staged(self) -> None:
        """Make the staged writes permanent; called on commit."""
        self.committed.update(self._staged)
        self._staged.clear()

    def discard_staged(self) -> None:
        """Forget the staged writes; called on rollback."""
        self._staged.clear()


class InMemoryExchangeUnitOfWork(InMemoryUnitOfWork):
    """``ExchangeUnitOfWork`` over in-memory repositories.

    Implements: Fake (of Unit of Work).

    Attributes:
        export_jobs: The export job repository.
        import_jobs: The import job repository.
    """

    def __init__(
        self,
        export_jobs: Iterable[ExportJob] = (),
        import_jobs: Iterable[ImportJob] = (),
    ) -> None:
        """Create the unit of work.

        Args:
            export_jobs: Export jobs that exist before the test acts.
            import_jobs: Import jobs that exist before the test acts.
        """
        super().__init__()
        self.export_jobs = InMemoryExportJobRepository(export_jobs)
        self.import_jobs = InMemoryImportJobRepository(import_jobs)

    def _on_commit(self) -> None:
        self.export_jobs.apply_staged()
        self.import_jobs.apply_staged()

    def _on_rollback(self) -> None:
        self.export_jobs.discard_staged()
        self.import_jobs.discard_staged()


class InMemoryExchangeQueryService:
    """``ExchangeQueryService`` over a fake unit of work's committed rows.

    Implements: Fake (of Query Service).
    """

    def __init__(self, uow: InMemoryExchangeUnitOfWork) -> None:
        """Create the query service.

        Args:
            uow: The unit of work whose committed rows are served.
        """
        self._uow = uow

    async def get_export_job(self, job_id: EntityId) -> ExportJobDetail | None:
        """Return one committed export job.

        Args:
            job_id: The job.

        Returns:
            Its detail view, or ``None``.
        """
        job = self._uow.export_jobs.committed.get(job_id)
        return None if job is None else ExportJobDetail.from_entity(job)

    async def list_export_jobs(
        self, requested_by: EntityId | None, page: PageRequest
    ) -> Page[ExportJobSummary]:
        """Filter, order newest first and page like the SQL implementation.

        Args:
            requested_by: Only this user's jobs, or all.
            page: Page size and cursor.

        Returns:
            One page of summaries.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        cursor = page.decode_cursor()
        ordered = sorted(
            self._uow.export_jobs.committed.values(),
            key=lambda job: (job.requested_at.isoformat(), job.id),
            reverse=True,
        )
        matching = [
            job
            for job in ordered
            if (requested_by is None or job.requested_by == requested_by)
            and (
                cursor is None
                or (job.requested_at.isoformat(), job.id)
                < (cursor.sort_key, cursor.last_id)
            )
        ]
        window = matching[: page.limit]
        next_cursor = None
        if len(matching) > page.limit:
            last = window[-1]
            next_cursor = encode_cursor(
                CursorPayload(sort_key=last.requested_at.isoformat(), last_id=last.id)
            )
        return Page[ExportJobSummary](
            items=tuple(ExportJobSummary.from_entity(job) for job in window),
            next_cursor=next_cursor,
        )

    async def get_import_job(self, job_id: EntityId) -> ImportJobDetail | None:
        """Return one committed import job.

        Args:
            job_id: The job.

        Returns:
            Its detail view, or ``None``.
        """
        job = self._uow.import_jobs.committed.get(job_id)
        return None if job is None else ImportJobDetail.from_entity(job)


# --------------------------------------------------------------------------- #
# Object storage                                                              #
# --------------------------------------------------------------------------- #


class StoredObject:
    """One object in the fake store.

    Implements: Fake.

    Attributes:
        data: Its content.
        media_type: Its ``Content-Type``.
    """

    def __init__(self, data: bytes, media_type: str) -> None:
        """Create the object.

        Args:
            data: Its content.
            media_type: Its ``Content-Type``.
        """
        self.data = data
        self.media_type = media_type


class _BufferSink:
    """Collects written bytes in memory.

    Implements: Fake (of ``BinarySink``).
    """

    def __init__(self) -> None:
        self.buffer = bytearray()

    async def write(self, data: bytes) -> None:
        self.buffer.extend(data)


class _BufferSource:
    """Hands out stored bytes in the sizes asked for.

    Implements: Fake (of ``BinarySource``).
    """

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._position = 0

    async def read(self, size: int) -> bytes:
        chunk = self._data[self._position : self._position + size]
        self._position += len(chunk)
        return chunk


class InMemoryArtifactStore:
    """``ArtifactStore`` over a dictionary of objects.

    Implements: Fake (of Adapter).

    Attributes:
        objects: The stored objects by key.
        presigned: Every ``(key, file_name)`` a link was asked for.
        upload_grants: Every ``(key, media_type, max_bytes)`` an upload was
            presigned for.
        failure: Raised by ``put_bytes`` when set, to simulate an outage.
    """

    def __init__(self, objects: Mapping[str, StoredObject] | None = None) -> None:
        """Create the store.

        Args:
            objects: Objects stored before the test acts.
        """
        self.objects: dict[str, StoredObject] = dict(objects or {})
        self.presigned: list[tuple[str, str]] = []
        self.upload_grants: list[tuple[str, str, int]] = []
        self.failure: Exception | None = None

    def put(self, key: str, data: bytes, media_type: str = "text/csv") -> None:
        """Store an object directly, for arranging a test.

        Args:
            key: The key.
            data: The content.
            media_type: Its ``Content-Type``.
        """
        self.objects[key] = StoredObject(data, media_type)

    @asynccontextmanager
    async def open_sink(self, key: str, media_type: str) -> AsyncIterator[BinarySink]:
        """Collect bytes and store them only if the block exits cleanly.

        Args:
            key: The key.
            media_type: Its ``Content-Type``.

        Yields:
            The sink.
        """
        sink = _BufferSink()
        yield sink
        self.objects[key] = StoredObject(bytes(sink.buffer), media_type)

    @asynccontextmanager
    async def open_source(self, key: str) -> AsyncIterator[BinarySource]:
        """Read a stored object.

        Args:
            key: The key.

        Yields:
            The source.

        Raises:
            NotFoundError: If no object has that key.
        """
        stored = self.objects.get(key)
        if stored is None:
            message = "no object has this key"
            raise NotFoundError(message)
        yield _BufferSource(stored.data)

    async def put_bytes(self, key: str, data: bytes, media_type: str) -> None:
        """Store a small object.

        Args:
            key: The key.
            data: The content.
            media_type: Its ``Content-Type``.

        Raises:
            Exception: ``failure``, when set.
        """
        if self.failure is not None:
            raise self.failure
        self.objects[key] = StoredObject(data, media_type)

    async def presign_download(self, key: str, *, file_name: str) -> str:
        """Return a fake link naming the key.

        Args:
            key: The key.
            file_name: The download name.

        Returns:
            ``https://storage.test/<key>``.
        """
        self.presigned.append((key, file_name))
        return f"{PRESIGN_BASE_URL}{key}"

    async def presign_upload(
        self, key: str, media_type: str, max_bytes: int
    ) -> PresignedUpload:
        """Record the grant and return a fake ``PUT`` URL naming the key.

        Args:
            key: The key.
            media_type: The ``Content-Type`` the upload must carry.
            max_bytes: The size cap.

        Returns:
            ``https://storage.test/<key>?signature=put`` with the content type and
            cap as headers, expiring at ``UPLOAD_EXPIRES_AT``.
        """
        self.upload_grants.append((key, media_type, max_bytes))
        return PresignedUpload(
            url=f"{PRESIGN_BASE_URL}{key}?signature=put",
            headers=(
                HttpHeader(name="Content-Type", value=media_type),
                HttpHeader(name="X-Max-Bytes", value=str(max_bytes)),
            ),
            expires_at=UPLOAD_EXPIRES_AT,
        )


# --------------------------------------------------------------------------- #
# Towards other modules                                                       #
# --------------------------------------------------------------------------- #


class FakeExportRowSource:
    """``ExportRowSource`` yielding fixed rows and recording each call.

    Implements: Fake (of Adapter).

    Attributes:
        calls: ``(dataset, filters, actor)`` of every call, in order.
        failure: Raised after ``fail_after`` rows when set.
        fail_after: How many rows to yield before raising ``failure``.
    """

    def __init__(
        self,
        *,
        events: Sequence[EventExportRow] = (),
        claims: Sequence[ClaimExportRow] = (),
        reports: Sequence[ReportExportRow] = (),
        mixed: Sequence[ExportRow] | None = None,
    ) -> None:
        """Create the source.

        Args:
            events: Rows of the events dataset.
            claims: Rows of the claims dataset.
            reports: Rows of the reports dataset.
            mixed: When set, returned for every dataset, to prove the handler
                refuses rows of another dataset.
        """
        self._events = tuple(events)
        self._claims = tuple(claims)
        self._reports = tuple(reports)
        self._mixed = None if mixed is None else tuple(mixed)
        self.calls: list[tuple[ExportDataset, ExportFilters, Actor]] = []
        self.failure: Exception | None = None
        self.fail_after = 0

    async def _yield[RowT: ExportRow](
        self, rows: Sequence[RowT]
    ) -> AsyncIterator[RowT]:
        for index, row in enumerate(rows):
            if self.failure is not None and index == self.fail_after:
                raise self.failure
            yield row
        if self.failure is not None and self.fail_after >= len(rows):
            raise self.failure

    def events(
        self, filters: ExportFilters, *, actor: Actor
    ) -> AsyncIterator[EventExportRow]:
        """Yield the event rows.

        Args:
            filters: Recorded.
            actor: Recorded.

        Returns:
            The rows.
        """
        self.calls.append((ExportDataset.EVENTS, filters, actor))
        return self._yield(self._mixed or self._events)  # type: ignore[arg-type]  # reason: mixed rows deliberately break the port's type in one test

    def claims(
        self, filters: ExportFilters, *, actor: Actor
    ) -> AsyncIterator[ClaimExportRow]:
        """Yield the claim rows.

        Args:
            filters: Recorded.
            actor: Recorded.

        Returns:
            The rows.
        """
        self.calls.append((ExportDataset.CLAIMS, filters, actor))
        return self._yield(self._claims)

    def reports(
        self, filters: ExportFilters, *, actor: Actor
    ) -> AsyncIterator[ReportExportRow]:
        """Yield the report rows.

        Args:
            filters: Recorded.
            actor: Recorded.

        Returns:
            The rows.
        """
        self.calls.append((ExportDataset.REPORTS, filters, actor))
        return self._yield(self._reports)


class FakeActorLookup:
    """``ActorLookup`` answering from a fixed mapping.

    Implements: Fake (of Adapter).
    """

    def __init__(self, actors: Iterable[Actor] = ()) -> None:
        """Create the lookup.

        Args:
            actors: The actors that exist; each needs a user id.
        """
        self.actors: dict[EntityId, Actor] = {
            actor.user_id: actor for actor in actors if actor.user_id is not None
        }

    async def actor_for(self, user_id: EntityId) -> Actor | None:
        """Return the known actor.

        Args:
            user_id: The user.

        Returns:
            The actor, or ``None``.
        """
        return self.actors.get(user_id)


class FakeBackfillReferenceChecker:
    """``BackfillReferenceChecker`` refusing a fixed set of hazard codes.

    Implements: Fake (of Adapter).

    Attributes:
        checked: Row numbers checked, in order.
    """

    def __init__(
        self,
        *,
        unknown_hazards: Iterable[str] = (),
        warn_hazards: Iterable[str] = (),
    ) -> None:
        """Create the checker.

        Args:
            unknown_hazards: Codes reported as an error on ``hazard_type``.
            warn_hazards: Codes reported as a warning on ``hazard_type``.
        """
        self._unknown = frozenset(unknown_hazards)
        self._warn = frozenset(warn_hazards)
        self.checked: list[int] = []

    async def check(
        self, row_number: int, draft: ImportedEventDraft
    ) -> tuple[RowIssue, ...]:
        """Report the configured hazard codes.

        Args:
            row_number: The row.
            draft: The draft.

        Returns:
            One issue for a configured code, none otherwise.
        """
        self.checked.append(row_number)
        if draft.hazard_type in self._unknown:
            return (
                RowIssue(
                    row_number=row_number,
                    field="hazard_type",
                    message="the hazard type is unknown or retired",
                ),
            )
        if draft.hazard_type in self._warn:
            return (
                RowIssue(
                    row_number=row_number,
                    field="hazard_type",
                    message="the hazard type is broad",
                    severity=ValidationSeverity.WARNING,
                ),
            )
        return ()


class FakeLineageSourceRegistrar:
    """``LineageSourceRegistrar`` recording each registration.

    Implements: Fake (of Adapter).

    Attributes:
        registered: ``(source, actor, new id)`` of every call.
        failure: Raised by ``register`` when set, to simulate a refusal.
    """

    def __init__(self, ids: SequentialIdGenerator | None = None) -> None:
        """Create the registrar.

        Args:
            ids: Source of the new source ids.
        """
        self._ids = ids or SequentialIdGenerator(seed=7301)
        self.registered: list[tuple[LineageSource, Actor, EntityId]] = []
        self.failure: Exception | None = None

    async def register(self, source: LineageSource, *, actor: Actor) -> EntityId:
        """Record the source and return a fresh id.

        Args:
            source: The lineage source.
            actor: The moderator.

        Returns:
            Its new id.

        Raises:
            Exception: ``failure``, when set.
        """
        if self.failure is not None:
            raise self.failure
        source_id = self._ids.new_id()
        self.registered.append((source, actor, source_id))
        return source_id


class _FakeBatch:
    """One batch of the fake writer; keeps its drafts only on ``commit``.

    Implements: Fake (of Unit of Work).
    """

    def __init__(self, writer: "FakeHistoricalEventWriter", number: int) -> None:
        self._writer = writer
        self._number = number
        self._staged: list[tuple[EntityId, ImportedEventDraft]] = []
        self.is_committed = False

    async def create(
        self,
        draft: ImportedEventDraft,
        *,
        lineage_source_id: EntityId,
        actor: Actor,
    ) -> EntityId:
        writer = self._writer
        if writer.fail_on_batch == self._number and self._staged:
            raise writer.failure
        writer.lineage_ids.add(lineage_source_id)
        writer.actors.add(actor)
        event_id = writer.ids.new_id()
        self._staged.append((event_id, draft))
        return event_id

    async def commit(self) -> None:
        if self.is_committed:
            message = "the batch has already committed"
            raise InvariantViolationError(message)
        self._writer.committed.extend(self._staged)
        self.is_committed = True


class FakeHistoricalEventWriter:
    """``HistoricalEventWriter`` keeping committed drafts, failing on request.

    Implements: Fake (of Adapter).

    Attributes:
        committed: ``(event id, draft)`` of every committed write, in order.
        batches_opened: How many batches were opened.
        rolled_back: How many batches ended without ``commit``.
        fail_on_batch: The 1-based batch whose second write raises ``failure``.
        failure: What that write raises.
        lineage_ids: Every lineage source id written with.
        actors: Every actor written for.
        ids: Source of the new event ids.
    """

    def __init__(
        self,
        *,
        fail_on_batch: int | None = None,
        failure: Exception | None = None,
    ) -> None:
        """Create the writer.

        Args:
            fail_on_batch: The batch to fail, if any.
            failure: What to raise; a ``ConflictError`` by default.
        """
        self.committed: list[tuple[EntityId, ImportedEventDraft]] = []
        self.batches_opened = 0
        self.rolled_back = 0
        self.fail_on_batch = fail_on_batch
        self.failure: Exception = failure or ConflictError("the write was refused")
        self.lineage_ids: set[EntityId] = set()
        self.actors: set[Actor] = set()
        self.ids = SequentialIdGenerator(seed=8117)

    @asynccontextmanager
    async def batch(self) -> AsyncIterator[BatchScope]:
        """Open one batch.

        Yields:
            The batch scope.
        """
        self.batches_opened += 1
        scope = _FakeBatch(self, self.batches_opened)
        try:
            yield scope
        finally:
            if not scope.is_committed:
                self.rolled_back += 1

    @property
    def drafts(self) -> list[ImportedEventDraft]:
        """Return the committed drafts, in order."""
        return [draft for _, draft in self.committed]


# --------------------------------------------------------------------------- #
# Format strategies                                                           #
# --------------------------------------------------------------------------- #


class FakeExporter:
    """``Exporter`` for ``csv`` writing a dataset line then one JSON line per row.

    Implements: Fake (of Strategy).

    Attributes:
        datasets: The dataset of every call.
        failure: Raised after writing the first line when set.
    """

    def __init__(
        self,
        export_format: ExportFormat = ExportFormat.CSV,
        media_type: str = "text/csv",
    ) -> None:
        """Create the exporter.

        Args:
            export_format: The format it claims.
            media_type: The media type it claims.
        """
        self._format = export_format
        self._media_type = media_type
        self.datasets: list[ExportDataset] = []
        self.failure: Exception | None = None

    @property
    def format(self) -> ExportFormat:
        """Return the claimed format."""
        return self._format

    @property
    def media_type(self) -> str:
        """Return the claimed media type."""
        return self._media_type

    async def write(
        self,
        dataset: ExportDataset,
        rows: AsyncIterator[ExportRow],
        sink: BinarySink,
    ) -> None:
        """Write the rows.

        Args:
            dataset: Written on the first line.
            rows: Each written as one JSON line.
            sink: Where the bytes go.

        Raises:
            Exception: ``failure``, when set.
        """
        self.datasets.append(dataset)
        await sink.write(f"{dataset.value}\n".encode())
        if self.failure is not None:
            raise self.failure
        async for row in rows:
            await sink.write(row.model_dump_json().encode() + b"\n")


class FakeImporter:
    """``Importer`` for ``csv`` reading real CSV with the standard library.

    ``header`` reads the whole source and parses it; ``read`` yields the rows it
    parsed. ``row_numbers`` can override the numbers yielded, to prove the handler
    checks them.

    Implements: Fake (of Strategy).
    """

    def __init__(
        self,
        import_format: ImportFormat = ImportFormat.CSV,
        *,
        row_numbers: Sequence[int] | None = None,
    ) -> None:
        """Create the importer.

        Args:
            import_format: The format it claims.
            row_numbers: Numbers to yield instead of 1, 2, 3, ...
        """
        self._format = import_format
        self._row_numbers = row_numbers
        self._rows: list[dict[str, str]] = []

    @property
    def format(self) -> ImportFormat:
        """Return the claimed format."""
        return self._format

    async def header(self, source: BinarySource) -> tuple[str, ...]:
        """Read the whole file and return its header.

        Args:
            source: The file.

        Returns:
            The column names.
        """
        content = bytearray()
        while chunk := await source.read(READ_CHUNK_BYTES):
            content.extend(chunk)
        reader = csv.DictReader(io.StringIO(content.decode("utf-8"), newline=""))
        self._rows = [dict(record) for record in reader]
        return tuple(reader.fieldnames or ())

    async def read(
        self, source: BinarySource
    ) -> AsyncIterator[tuple[int, Mapping[str, str]]]:
        """Yield the parsed rows.

        Args:
            source: Ignored; ``header`` read it.

        Yields:
            ``(row_number, cells)``.
        """
        for index, cells in enumerate(self._rows):
            number = (
                index + 1 if self._row_numbers is None else self._row_numbers[index]
            )
            yield number, cells


def csv_bytes(rows: Iterable[Mapping[str, str]], columns: Sequence[str]) -> bytes:
    """Write rows as CSV with the given header, for arranging import files.

    Args:
        rows: The rows; missing columns are written empty.
        columns: The header.

    Returns:
        UTF-8 CSV.
    """
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(columns), extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")
