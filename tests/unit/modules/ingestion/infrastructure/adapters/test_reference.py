"""Unit tests for the built-in reference source registration helpers."""

from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from tests.unit.modules.ingestion.application.support import NOW, make_version
from tests.unit.modules.ingestion.infrastructure.adapters.support import (
    FIXTURES_DIR,
    SAMPLE_FILE,
    make_fixture_dataset,
    sha256_of,
)
from yakhnama.modules.ingestion.application.registry import (
    PipelineClassRegistry,
    SourceAdapterRegistry,
)
from yakhnama.modules.ingestion.infrastructure.adapters.reference import (
    LOCAL_CSV_TEMPERATURE_ADAPTER,
    reference_adapters,
    reference_pipelines,
)
from yakhnama.modules.ingestion.infrastructure.adapters.temperature import (
    TemperatureCsvPipeline,
)


def test_reference_adapters_register_the_temperature_adapter_by_name() -> None:
    adapters = reference_adapters(FIXTURES_DIR, clock=FrozenClock(NOW))

    registry = SourceAdapterRegistry(adapters)

    assert registry.names == (LOCAL_CSV_TEMPERATURE_ADAPTER,)


async def test_reference_adapters_temperature_adapter_reads_the_sample() -> None:
    [adapter] = reference_adapters(FIXTURES_DIR, clock=FrozenClock(NOW))
    dataset = make_fixture_dataset()

    payload = await adapter.fetch(dataset, make_version(dataset, b""))

    assert payload.checksum == sha256_of(FIXTURES_DIR / SAMPLE_FILE)


def test_reference_pipelines_build_a_temperature_pipeline_for_the_adapter() -> None:
    clock = FrozenClock(NOW)
    [adapter] = reference_adapters(FIXTURES_DIR, clock=clock)
    registry = PipelineClassRegistry(
        clock=clock, ids=SequentialIdGenerator(seed=1), pipelines=reference_pipelines()
    )

    result = registry.create(adapter)

    assert isinstance(result, TemperatureCsvPipeline)
    assert registry.supports(LOCAL_CSV_TEMPERATURE_ADAPTER)
