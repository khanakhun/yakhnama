"""Unit tests for ``yakhnama.modules.provenance.domain.events``."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError as PydanticValidationError

from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.provenance.domain.events import (
    SOURCE_DETAIL_FIELD_COUNT,
    SourceDetailsUpdated,
    SourceReferenced,
    SourceRegistered,
)
from yakhnama.modules.provenance.domain.value_objects import SourceDetails, SourceType
from yakhnama.shared_kernel.events import DomainEvent

OCCURRED_AT = datetime(2026, 9, 1, tzinfo=UTC)
IDS = SequentialIdGenerator()


def _common() -> dict[str, object]:
    return {
        "event_id": IDS.new_id(),
        "occurred_at": OCCURRED_AT,
        "aggregate_id": IDS.new_id(),
        "version": 1,
    }


@pytest.mark.parametrize(
    ("event_class", "event_type"),
    [
        (SourceRegistered, "provenance.source_registered"),
        (SourceDetailsUpdated, "provenance.source_details_updated"),
        (SourceReferenced, "provenance.source_referenced"),
    ],
)
def test_source_event_types_are_namespaced_by_context(
    event_class: type[DomainEvent], event_type: str
) -> None:
    declared = event_class.event_type

    assert declared == event_type


def test_source_registered_carries_ids_and_type_with_source_aggregate() -> None:
    event = SourceRegistered.model_validate(
        {
            **_common(),
            "source_type": SourceType.NEWS,
            "owner_actor_id": None,
            "organization_id": None,
        }
    )

    assert event.aggregate_type == "source"
    assert event.source_type is SourceType.NEWS


def test_source_detail_field_count_matches_source_details() -> None:
    count = SOURCE_DETAIL_FIELD_COUNT

    assert count == len(SourceDetails.model_fields)


def test_source_details_updated_without_changed_fields_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        SourceDetailsUpdated.model_validate({**_common(), "changed_fields": []})


def test_source_events_have_no_free_text_fields() -> None:
    free_text_names = {"title", "citation", "url", "publisher", "licence"}

    fields = {
        name
        for event_class in (SourceRegistered, SourceDetailsUpdated, SourceReferenced)
        for name in event_class.model_fields
    }

    assert fields.isdisjoint(free_text_names)
