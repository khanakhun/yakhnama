"""Arrangements for the fixture-source tests: paths, datasets, runs, one run helper.

Everything here is synthetic; the dataset is the ``fixture.temperature_sample``
catalog entry and the files are the committed fixtures.
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from tests.factories.ingestion import DatasetTestFactory
from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.ingestion import InMemoryIngestionUnitOfWork
from tests.unit.modules.ingestion.application.support import (
    EARLIER,
    NOW,
    make_run,
    make_version,
)
from yakhnama.modules.ingestion.application.dto import IngestionOutcome
from yakhnama.modules.ingestion.domain.entities import (
    Dataset,
    DatasetVersion,
    IngestionRun,
)
from yakhnama.modules.ingestion.infrastructure.adapters.local_csv import (
    FixturePathResolver,
    LocalCsvSourceAdapter,
)
from yakhnama.modules.ingestion.infrastructure.adapters.reference import (
    LOCAL_CSV_TEMPERATURE_ADAPTER,
    TEMPERATURE_SAMPLE_DATASET,
)
from yakhnama.modules.ingestion.infrastructure.adapters.temperature import (
    TemperatureCsvPipeline,
)

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[6]
FIXTURES_DIR: Final = REPOSITORY_ROOT / "data" / "fixtures" / "ingestion"
"""The committed ingestion fixtures, ``data/fixtures/ingestion``."""

TEST_FIXTURES_DIR: Final = REPOSITORY_ROOT / "tests" / "fixtures" / "ingestion"
"""Test-only samples, ``tests/fixtures/ingestion``."""

SAMPLE_FILE: Final = "temperature_sample.csv"
MALFORMED_FILE: Final = "temperature_malformed.csv"


def sha256_of(path: Path) -> str:
    """Return the hex SHA-256 of the file at ``path``."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_fixture_dataset() -> Dataset:
    """Return the temperature fixture dataset, created before ``NOW``."""
    return DatasetTestFactory.build(
        code=TEMPERATURE_SAMPLE_DATASET, created_at=EARLIER, updated_at=EARLIER
    )


@dataclass
class FixtureWorld:
    """The fixture dataset, one version pinned to ``file_name`` and its run."""

    directory: Path = FIXTURES_DIR
    file_name: str = SAMPLE_FILE
    clock: FrozenClock = field(default_factory=lambda: FrozenClock(NOW))
    ids: SequentialIdGenerator = field(
        default_factory=lambda: SequentialIdGenerator(seed=5151)
    )

    def __post_init__(self) -> None:
        """Build the entities, the adapter and the unit of work."""
        self.path = self.directory / self.file_name
        self.content = self.path.read_bytes()
        self.dataset = make_fixture_dataset()
        self.version: DatasetVersion = make_version(self.dataset, self.content)
        self.adapter = LocalCsvSourceAdapter(
            LOCAL_CSV_TEMPERATURE_ADAPTER,
            FixturePathResolver(
                self.directory, {TEMPERATURE_SAMPLE_DATASET: self.file_name}
            ),
            clock=self.clock,
        )
        self.uow = InMemoryIngestionUnitOfWork(
            datasets=[self.dataset], versions=[self.version]
        )

    def pipeline(self) -> TemperatureCsvPipeline:
        """Return a fresh pipeline over the world's adapter."""
        return TemperatureCsvPipeline(self.adapter, clock=self.clock, ids=self.ids)

    async def run_once(self) -> IngestionOutcome:
        """Run a new pipeline for a new run of the version and commit.

        The handler stores the running run before the pipeline starts; this does
        the same, so ``record_lineage`` can save the finished run.
        """
        pending: IngestionRun = make_run(
            self.version, adapter_name=LOCAL_CSV_TEMPERATURE_ADAPTER
        )
        running = pending.start(clock=self.clock, ids=self.ids).state
        self.uow.ingestion_runs.store.committed[running.id] = running
        async with self.uow:
            outcome = await self.pipeline().run(
                self.dataset, self.version, running, self.uow
            )
            await self.uow.commit()
        return outcome
