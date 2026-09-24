"""Shared arrangements for the ingestion application tests.

Station codes, values and datasets are synthetic; nothing here is a real source.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from tests.factories.ingestion import (
    DatasetTestFactory,
    DatasetVersionTestFactory,
    IngestionRunTestFactory,
)
from tests.fakes.clock import FrozenClock
from tests.fakes.identity import actor_with
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.ingestion import (
    MINIMAL_HEADER,
    FakeSourceAdapter,
    InMemoryIngestionUnitOfWork,
    MinimalTestPipeline,
    RecordingPipelineFactory,
)
from tests.fakes.tasks import RecordingTaskQueue
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.modules.identity.public import Role
from yakhnama.modules.ingestion.application.registry import SourceAdapterRegistry
from yakhnama.modules.ingestion.domain.entities import (
    Dataset,
    DatasetVersion,
    IngestionRun,
)
from yakhnama.modules.ingestion.domain.value_objects import RunStatus
from yakhnama.shared_kernel.value_objects import DatePrecision, DateWithPrecision

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
EARLIER = NOW - timedelta(days=1)
ADAPTER = "test_adapter"

ADMIN = actor_with({Role.ADMIN})
MODERATOR = actor_with({Role.MODERATOR})
CITIZEN = actor_with()


def row(  # noqa: PLR0913  # reason: one keyword per CSV column, all defaulted
    *,
    station: str = "TEST-01",
    variable: str = "air_temperature",
    value: str = "273.15",
    unit: str = "kelvin",
    observed_at: str = "2026-01-01T00:00:00+00:00",
    quality: str = "good",
) -> str:
    """Return one line of the minimal test layout."""
    return ",".join((station, variable, value, unit, observed_at, quality))


def hour(index: int) -> str:
    """Return the ISO instant ``index`` hours after 2026-01-01T00:00Z."""
    return (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=index)).isoformat()


def csv(*lines: str) -> bytes:
    """Return a minimal-layout CSV with ``lines`` after the header."""
    return "\n".join((MINIMAL_HEADER, *lines)).encode()


def sha256(content: bytes) -> str:
    """Return the hex SHA-256 of ``content``."""
    return hashlib.sha256(content).hexdigest()


def make_dataset() -> Dataset:
    """Return an active dataset created before ``NOW``."""
    return DatasetTestFactory.build(created_at=EARLIER, updated_at=EARLIER)


def make_version(dataset: Dataset, content: bytes) -> DatasetVersion:
    """Return a version of ``dataset`` pinned to ``content``."""
    return DatasetVersionTestFactory.build(
        dataset_id=dataset.id,
        input_checksum=sha256(content),
        created_at=EARLIER,
        retrieved_at=DateWithPrecision(value=EARLIER, precision=DatePrecision.EXACT),
    )


def make_run(version: DatasetVersion, adapter_name: str = ADAPTER) -> IngestionRun:
    """Return a pending run of ``version`` through ``ADAPTER``."""
    return IngestionRunTestFactory.build(
        dataset_version_id=version.id,
        adapter_name=adapter_name,
        created_at=EARLIER,
        updated_at=EARLIER,
    )


@dataclass
class World:
    """One dataset, one version of ``content`` and one pending run, wired up."""

    content: bytes
    clock: FrozenClock = field(default_factory=lambda: FrozenClock(NOW))
    ids: SequentialIdGenerator = field(
        default_factory=lambda: SequentialIdGenerator(seed=4242)
    )
    queue: RecordingTaskQueue = field(default_factory=RecordingTaskQueue)

    def __post_init__(self) -> None:
        """Build the entities, fakes and registries."""
        self.dataset = make_dataset()
        self.version = make_version(self.dataset, self.content)
        self.run = make_run(self.version)
        self.uow = InMemoryIngestionUnitOfWork(
            datasets=[self.dataset], versions=[self.version], runs=[self.run]
        )
        self.uow_factory = InMemoryUnitOfWorkFactory(self.uow)
        self.adapter = FakeSourceAdapter(
            {self.version.id: self.content}, clock=self.clock
        )
        self.adapters = SourceAdapterRegistry([self.adapter])
        self.pipelines = RecordingPipelineFactory(clock=self.clock, ids=self.ids)

    def running(self) -> IngestionRun:
        """Return ``run`` moved to ``running`` (not stored)."""
        return self.run.start(clock=self.clock, ids=self.ids).state

    def pipeline(self) -> MinimalTestPipeline:
        """Return a fresh pipeline over the world's adapter."""
        return MinimalTestPipeline(self.adapter, clock=self.clock, ids=self.ids)

    def stored_run(self) -> IngestionRun:
        """Return the committed state of ``run``."""
        return self.uow.ingestion_runs.committed[self.run.id]


def status_of(world: World) -> RunStatus:
    """Return the committed status of the world's run."""
    return world.stored_run().status
