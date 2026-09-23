"""Unit tests for ``yakhnama.modules.hazards.domain.events``."""

import pytest
from pydantic import ValidationError as PydanticValidationError

from tests.unit.modules.hazards.domain.sample_hazard_types import CREATED_AT, ids
from yakhnama.modules.hazards.domain.events import (
    HazardTypeCreated,
    HazardTypeEvent,
    HazardTypeReactivated,
    HazardTypeRelabelled,
    HazardTypeReparented,
    HazardTypeRetired,
)
from yakhnama.shared_kernel.events import DomainEvent


@pytest.mark.parametrize(
    ("event_class", "event_type"),
    [
        (HazardTypeCreated, "hazards.hazard_type_created"),
        (HazardTypeRetired, "hazards.hazard_type_retired"),
        (HazardTypeReactivated, "hazards.hazard_type_reactivated"),
        (HazardTypeRelabelled, "hazards.hazard_type_relabelled"),
        (HazardTypeReparented, "hazards.hazard_type_reparented"),
    ],
)
def test_hazard_type_event_classes_declare_stable_event_types(
    event_class: type[DomainEvent], event_type: str
) -> None:
    declared = event_class.event_type

    assert declared == event_type


def test_hazard_type_event_defaults_aggregate_type_to_hazard_type() -> None:
    event = HazardTypeReactivated(
        event_id=ids.new_id(),
        occurred_at=CREATED_AT,
        aggregate_id=ids.new_id(),
        code="glof",
        reason="restored",
    )

    assert event.aggregate_type == "hazard_type"


def test_hazard_type_event_other_aggregate_type_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        HazardTypeReactivated.model_validate(
            {
                "event_id": ids.new_id(),
                "occurred_at": CREATED_AT,
                "aggregate_id": ids.new_id(),
                "aggregate_type": "report",
                "code": "glof",
                "reason": "restored",
            }
        )


def test_hazard_type_event_base_without_event_type_raises_type_error() -> None:
    with pytest.raises(TypeError):
        HazardTypeEvent(
            event_id=ids.new_id(),
            occurred_at=CREATED_AT,
            aggregate_id=ids.new_id(),
            code="glof",
        )
