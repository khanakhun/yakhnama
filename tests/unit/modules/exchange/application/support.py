"""A fully faked exchange module for the application tests.

Every value is synthetic: no real event, place, report or source.

Patterns: Fake.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Final

from tests.fakes.clock import SteppingClock
from tests.fakes.exchange import (
    FakeActorLookup,
    FakeBackfillReferenceChecker,
    FakeExporter,
    FakeExportRowSource,
    FakeHistoricalEventWriter,
    FakeImporter,
    FakeLineageSourceRegistrar,
    InMemoryArtifactStore,
    InMemoryExchangeQueryService,
    InMemoryExchangeUnitOfWork,
    csv_bytes,
)
from tests.fakes.identity import actor_with
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.tasks import RecordingTaskQueue
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.modules.events.public import EventStatus
from yakhnama.modules.exchange.application.commands import (
    RequestExport,
    RequestImport,
)
from yakhnama.modules.exchange.application.formats import (
    ClaimExportRow,
    EventExportRow,
    FormatAdapterRegistry,
    ReportExportRow,
)
from yakhnama.modules.exchange.application.handlers import (
    CancelExportHandler,
    ExchangeHandlerDependencies,
    RequestExportHandler,
    RequestImportHandler,
    RunExportHandler,
    RunImportHandler,
)
from yakhnama.modules.exchange.application.query_services import (
    ExchangeJobQueryService,
)
from yakhnama.modules.exchange.domain.backfill import BACKFILL_COLUMNS, claim_column
from yakhnama.modules.exchange.domain.entities import ExportJob, ImportJob
from yakhnama.modules.exchange.domain.value_objects import (
    ARTIFACT_MAX_BYTES,
    ArtifactRef,
    ExportDataset,
    ExportFormat,
    ImportFormat,
)
from yakhnama.modules.identity.public import Actor, Role
from yakhnama.modules.impacts.public import ClaimScope, ClaimStatus, CountValue
from yakhnama.modules.reports.public import ReportStatus
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.privacy import PublicCoordinatePolicy
from yakhnama.shared_kernel.value_objects import (
    Confidence,
    Coordinates,
    DatePrecision,
    DateWithPrecision,
)

NOW: Final = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
GENERATOR: Final = "yakhnama/0.0.0-test"
DECIMALS: Final = 2
_IDS: Final = SequentialIdGenerator(seed=9001)
USER_ID: Final = _IDS.new_id()
OTHER_ID: Final = _IDS.new_id()
MODERATOR_ID: Final = _IDS.new_id()
CITIZEN: Final = actor_with(user_id=USER_ID)
OTHER: Final = actor_with(user_id=OTHER_ID)
MODERATOR: Final = actor_with({Role.MODERATOR}, user_id=MODERATOR_ID)
ANONYMOUS: Final = Actor.anonymous()
IMPORT_KEY: Final = "imports/upload-1/source.csv"


def new_id() -> EntityId:
    """Return a fresh, deterministic UUIDv7."""
    return _IDS.new_id()


def day(year: int, month: int, number: int) -> DateWithPrecision:
    """Return midnight UTC of a day at ``day`` precision."""
    return DateWithPrecision(
        value=datetime(year, month, number, tzinfo=UTC), precision=DatePrecision.DAY
    )


def event_row(**fields: object) -> EventExportRow:
    """Return a synthetic published, verified event row."""
    return EventExportRow.model_validate(
        {
            "event_id": new_id(),
            "hazard_code": "glof",
            "title": "Synthetic outburst flood",
            "started_at": day(2022, 7, 15),
            "centroid": Coordinates(longitude=74.5, latitude=36.25),
            "place_codes": ("pk.gb.test",),
            "source_ids": (new_id(),),
            "status": EventStatus.PUBLISHED,
            "verification_state": "verified",
            "updated_at": NOW - timedelta(days=3),
            **fields,
        }
    )


def claim_row(**fields: object) -> ClaimExportRow:
    """Return a synthetic active claim row."""
    return ClaimExportRow.model_validate(
        {
            "claim_id": new_id(),
            "event_id": new_id(),
            "metric_code": "houses_destroyed",
            "value": CountValue(count=12),
            "confidence": Confidence.MEDIUM,
            "source_id": new_id(),
            "source_type": "dataset",
            "claimed_at": day(2022, 7, 16),
            "scope": ClaimScope(),
            "status": ClaimStatus.ACTIVE,
            "created_at": NOW - timedelta(days=2),
            **fields,
        }
    )


def report_row(
    longitude: float = 74.123456, latitude: float = 36.987654
) -> ReportExportRow:
    """Return a synthetic report row with an exact, unrounded position."""
    return ReportExportRow(
        report_id=new_id(),
        status=ReportStatus.SUBMITTED,
        revision=1,
        observed_at=day(2026, 8, 10),
        coordinates=Coordinates(longitude=longitude, latitude=latitude),
        hazard_code="glof",
        media_count=1,
        submitted_at=NOW - timedelta(days=1),
    )


VALID_CELLS: Final[dict[str, str]] = {
    "title": "Synthetic GLOF, upper valley",
    "hazard_type": "glof",
    "started_at": "2022-07-15T06:00:00Z",
    "started_at_precision": "day",
    "longitude": "74.5",
    "latitude": "36.5",
    "source_citation": "Synthetic source, 2022",
    claim_column(1, "metric_code"): "houses_destroyed",
    claim_column(1, "value_kind"): "count",
    claim_column(1, "value"): "12",
    claim_column(1, "confidence"): "medium",
    claim_column(1, "claimed_at"): "2022-07-16T00:00:00+05:00",
    claim_column(1, "claimed_at_precision"): "day",
}


def cells(**changes: str) -> dict[str, str]:
    """Return a valid backfill row with ``changes`` applied."""
    return VALID_CELLS | changes


def import_file(*rows: dict[str, str]) -> bytes:
    """Return a CSV import file with the full contract header."""
    return csv_bytes(rows, BACKFILL_COLUMNS)


def artifact_of(content: bytes, key: str = IMPORT_KEY) -> ArtifactRef:
    """Return the declared reference of an import file."""
    return ArtifactRef(
        object_key=key,
        byte_size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        media_type="text/csv",
    )


class ExchangeWorld:
    """Every fake the exchange handlers and read service need, wired together.

    Implements: Fake.

    Attributes:
        uow: The unit of work every handler opens.
        tasks: Records enqueued tasks.
        artifacts: The object store.
        exporter: The ``csv`` exporter.
        importer: The ``csv`` importer.
        formats: The registry holding both.
        actors: The users a run can rebuild.
        rows: The rows exports read.
        references: The reference checker imports use.
        lineage: Records lineage sources.
        writer: Records historical events written.
        deps: The handler dependencies.
        queries: The guarded read service.
    """

    def __init__(  # noqa: PLR0913  # reason: one optional override per fake
        self,
        *,
        export_jobs: tuple[ExportJob, ...] = (),
        import_jobs: tuple[ImportJob, ...] = (),
        rows: FakeExportRowSource | None = None,
        actors: tuple[Actor, ...] = (CITIZEN, OTHER, MODERATOR),
        references: FakeBackfillReferenceChecker | None = None,
        writer: FakeHistoricalEventWriter | None = None,
        importer: FakeImporter | None = None,
    ) -> None:
        """Build the world.

        Args:
            export_jobs: Export jobs stored before the test acts.
            import_jobs: Import jobs stored before the test acts.
            rows: The export row source.
            actors: The users ``ActorLookup`` knows.
            references: The reference checker.
            writer: The historical event writer.
            importer: The importer.
        """
        self.uow = InMemoryExchangeUnitOfWork(export_jobs, import_jobs)
        self.tasks = RecordingTaskQueue()
        self.artifacts = InMemoryArtifactStore()
        self.exporter = FakeExporter()
        self.importer = importer or FakeImporter()
        self.formats = (
            FormatAdapterRegistry()
            .register_exporter(self.exporter)
            .register_importer(self.importer)
        )
        self.actors = FakeActorLookup(actors)
        self.rows = rows or FakeExportRowSource()
        self.references = references or FakeBackfillReferenceChecker()
        self.lineage = FakeLineageSourceRegistrar()
        self.writer = writer or FakeHistoricalEventWriter()
        self.deps = ExchangeHandlerDependencies(
            uow_factory=InMemoryUnitOfWorkFactory(self.uow),
            clock=SteppingClock(NOW, timedelta(seconds=1)),
            ids=SequentialIdGenerator(seed=77),
            tasks=self.tasks,
            formats=self.formats,
            artifacts=self.artifacts,
            actors=self.actors,
        )
        self.queries = ExchangeJobQueryService(
            InMemoryExchangeQueryService(self.uow), self.artifacts
        )

    def request_export(self) -> RequestExportHandler:
        """Return the ``RequestExport`` handler."""
        return RequestExportHandler(self.deps)

    def cancel_export(self) -> CancelExportHandler:
        """Return the ``CancelExport`` handler."""
        return CancelExportHandler(self.deps)

    def run_export(self, *, max_bytes: int = ARTIFACT_MAX_BYTES) -> RunExportHandler:
        """Return the ``RunExport`` handler."""
        return RunExportHandler(
            self.deps,
            rows=self.rows,
            coordinates=PublicCoordinatePolicy(decimals=DECIMALS),
            generator=GENERATOR,
            max_bytes=max_bytes,
        )

    def request_import(self) -> RequestImportHandler:
        """Return the ``RequestImport`` handler."""
        return RequestImportHandler(self.deps)

    def run_import(self, *, batch_size: int = 2) -> RunImportHandler:
        """Return the ``RunImport`` handler with small batches."""
        return RunImportHandler(
            self.deps,
            references=self.references,
            lineage=self.lineage,
            writer=self.writer,
            batch_size=batch_size,
        )

    def export_job(self, job_id: EntityId) -> ExportJob:
        """Return the committed state of an export job."""
        return self.uow.export_jobs.committed[job_id]

    def import_job(self, job_id: EntityId) -> ImportJob:
        """Return the committed state of an import job."""
        return self.uow.import_jobs.committed[job_id]

    async def queued_export(
        self,
        dataset: ExportDataset = ExportDataset.EVENTS,
        actor: Actor = CITIZEN,
    ) -> EntityId:
        """Request an export through the handler and return its id."""
        return await self.request_export()(
            RequestExport(actor=actor, dataset=dataset, format=ExportFormat.CSV)
        )

    async def queued_import(self, content: bytes, *, dry_run: bool) -> EntityId:
        """Store ``content`` and request an import of it by the moderator."""
        self.artifacts.put(IMPORT_KEY, content)
        return await self.request_import()(
            RequestImport(
                actor=MODERATOR,
                format=ImportFormat.CSV,
                artifact=artifact_of(content),
                dry_run=dry_run,
            )
        )
