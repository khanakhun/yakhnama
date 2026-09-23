"""Unit tests for ``yakhnama.modules.ingestion.domain.events``."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError as PydanticValidationError

from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.ingestion.domain.events import (
    DatasetCoverageUpdated,
    DatasetEvent,
    DatasetRegistered,
    DatasetStatusChanged,
    DatasetVersionRecorded,
    IngestionRunEvent,
    IngestionRunFinished,
    IngestionRunRequested,
    IngestionRunStarted,
    RasterAssetCatalogued,
)
from yakhnama.modules.ingestion.domain.value_objects import RunCounts, RunStatus
from yakhnama.shared_kernel.events import DomainEvent, is_event_type

OCCURRED_AT = datetime(2026, 9, 1, tzinfo=UTC)
PUBLISHED_EVENTS: tuple[type[DomainEvent], ...] = (
    DatasetRegistered,
    DatasetStatusChanged,
    DatasetCoverageUpdated,
    DatasetVersionRecorded,
    IngestionRunRequested,
    IngestionRunStarted,
    IngestionRunFinished,
    RasterAssetCatalogued,
)


@pytest.mark.parametrize("event_class", PUBLISHED_EVENTS, ids=lambda cls: cls.__name__)
def test_ingestion_event_type_is_namespaced_and_well_formed(
    event_class: type[DomainEvent],
) -> None:
    event_type = event_class.event_type

    assert event_type.startswith("ingestion.")
    assert is_event_type(event_type)


def test_ingestion_event_types_are_unique() -> None:
    event_types = [event_class.event_type for event_class in PUBLISHED_EVENTS]

    assert len(set(event_types)) == len(event_types)


@pytest.mark.parametrize(
    ("base", "extra_fields"),
    [
        (DatasetEvent, {}),
        (IngestionRunEvent, {"dataset_version_id": SequentialIdGenerator().new_id()}),
    ],
    ids=["dataset", "ingestion_run"],
)
def test_ingestion_event_base_without_event_type_cannot_be_published(
    base: type[DomainEvent], extra_fields: dict[str, object]
) -> None:
    ids = SequentialIdGenerator(seed=1)

    with pytest.raises(TypeError, match="does not declare an event_type"):
        base.model_validate(
            {
                "event_id": ids.new_id(),
                "occurred_at": OCCURRED_AT,
                "aggregate_id": ids.new_id(),
                "version": 1,
                **extra_fields,
            }
        )


def test_ingestion_run_finished_carries_status_counts_and_aggregate_type() -> None:
    ids = SequentialIdGenerator()

    event = IngestionRunFinished(
        event_id=ids.new_id(),
        occurred_at=OCCURRED_AT,
        aggregate_id=ids.new_id(),
        version=3,
        dataset_version_id=ids.new_id(),
        status=RunStatus.SUCCEEDED,
        counts=RunCounts(fetched=1, parsed=1, valid=1, persisted=1),
        error_count=0,
        input_checksum="A" * 64,
    )

    assert event.aggregate_type == "ingestion_run"
    assert event.input_checksum == "a" * 64


def test_dataset_coverage_updated_unknown_field_is_rejected() -> None:
    ids = SequentialIdGenerator()

    with pytest.raises(PydanticValidationError):
        DatasetCoverageUpdated.model_validate(
            {
                "event_id": ids.new_id(),
                "occurred_at": OCCURRED_AT,
                "aggregate_id": ids.new_id(),
                "version": 2,
                "changed_fields": {"licence"},
            }
        )
