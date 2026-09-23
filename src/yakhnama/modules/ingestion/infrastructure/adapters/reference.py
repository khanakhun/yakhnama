"""The built-in reference sources, for the composition root to register.

Only the synthetic temperature fixture exists in this run (no live network calls).
A real source gets its own adapter and pipeline modules and is registered in the
composition root beside these, without changing them.

Patterns: Factory, Registry.
"""

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Final

from yakhnama.modules.ingestion.application.ports import SourceAdapter
from yakhnama.modules.ingestion.application.registry import PipelineConstructor
from yakhnama.modules.ingestion.infrastructure.adapters.local_csv import (
    FixturePathResolver,
    LocalCsvSourceAdapter,
)
from yakhnama.modules.ingestion.infrastructure.adapters.temperature import (
    TemperatureCsvPipeline,
)
from yakhnama.shared_kernel.clock import Clock

LOCAL_CSV_TEMPERATURE_ADAPTER: Final = "local_csv_temperature"
"""Registry name of the fixture temperature adapter and its pipeline."""

TEMPERATURE_SAMPLE_DATASET: Final = "fixture.temperature_sample"
"""Catalog code of the synthetic temperature fixture."""

TEMPERATURE_FIXTURE_FILES: Final[Mapping[str, str]] = MappingProxyType(
    {TEMPERATURE_SAMPLE_DATASET: "temperature_sample.csv"}
)
"""Fixture file by dataset code, relative to the ingestion fixtures directory
(``data/fixtures/ingestion`` in the repository)."""


def reference_adapters(
    fixtures_dir: Path, *, clock: Clock
) -> tuple[SourceAdapter, ...]:
    """Build the built-in source adapters.

    Args:
        fixtures_dir: The ingestion fixtures directory, ``data/fixtures/ingestion``.
        clock: Source of every payload's ``retrieved_at``.

    Returns:
        One adapter per built-in source, ready for ``SourceAdapterRegistry``.
    """
    return (
        LocalCsvSourceAdapter(
            LOCAL_CSV_TEMPERATURE_ADAPTER,
            FixturePathResolver(fixtures_dir, TEMPERATURE_FIXTURE_FILES),
            clock=clock,
        ),
    )


def reference_pipelines() -> Mapping[str, PipelineConstructor]:
    """Return the pipeline class of every built-in source, by adapter name.

    Returns:
        A read-only mapping for ``PipelineClassRegistry(pipelines=...)``.
    """
    return MappingProxyType({LOCAL_CSV_TEMPERATURE_ADAPTER: TemperatureCsvPipeline})
