"""Unit tests for ``yakhnama.modules.exchange.domain.events``."""

from datetime import UTC, datetime

import pytest

from tests.factories.base import FACTORY_IDS
from yakhnama.modules.exchange.domain.events import (
    ExportCancelled,
    ExportCompleted,
    ExportFailed,
    ExportRequested,
    ExportStarted,
    ImportCompleted,
    ImportFailed,
    ImportRequested,
    ImportStarted,
)
from yakhnama.shared_kernel.events import DomainEvent, is_event_type

EVENT_CLASSES: tuple[type[DomainEvent], ...] = (
    ExportRequested,
    ExportStarted,
    ExportCompleted,
    ExportFailed,
    ExportCancelled,
    ImportRequested,
    ImportStarted,
    ImportCompleted,
    ImportFailed,
)
# Free-text or storage fields that must never reach the outbox.
FORBIDDEN_PAYLOAD_FIELDS = frozenset(
    {"object_key", "error_summary", "citation", "filters", "message", "sha256"}
)


@pytest.mark.parametrize("event_class", EVENT_CLASSES)
def test_exchange_event_type_is_namespaced_and_well_formed(
    event_class: type[DomainEvent],
) -> None:
    assert event_class.event_type.startswith("exchange.")
    assert is_event_type(event_class.event_type)


@pytest.mark.parametrize("event_class", EVENT_CLASSES)
def test_exchange_event_payload_carries_ids_codes_and_counts_only(
    event_class: type[DomainEvent],
) -> None:
    assert not FORBIDDEN_PAYLOAD_FIELDS & set(event_class.model_fields)


def test_exchange_event_types_are_distinct() -> None:
    types = [event_class.event_type for event_class in EVENT_CLASSES]

    assert len(set(types)) == len(types)


def test_import_completed_with_negative_count_raises_validation_error() -> None:
    with pytest.raises(ValueError, match="greater than or equal"):
        ImportCompleted(
            event_id=FACTORY_IDS.new_id(),
            occurred_at=datetime(2026, 9, 23, tzinfo=UTC),
            aggregate_id=FACTORY_IDS.new_id(),
            version=2,
            dry_run=False,
            rows_seen=1,
            rows_rejected=0,
            created_count=-1,
            batches_applied=0,
        )
