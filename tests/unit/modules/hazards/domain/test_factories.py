"""Unit tests for ``yakhnama.modules.hazards.domain.factories``."""

import pytest
from pydantic import ValidationError as PydanticValidationError

from tests.unit.modules.hazards.domain.sample_hazard_types import (
    ALIGNMENT,
    CREATED_AT,
    hazard_type,
    labels,
    stepping_clock,
)
from yakhnama.modules.hazards.domain.attributes import HazardAttributeRegistry
from yakhnama.modules.hazards.domain.entities import HazardTaxonomy
from yakhnama.modules.hazards.domain.errors import (
    HazardCodeAlreadyUsedError,
    HazardTypeNotFoundError,
    HazardTypeRetiredError,
    UnknownHazardAttributesError,
)
from yakhnama.modules.hazards.domain.events import HazardTypeCreated
from yakhnama.modules.hazards.domain.factories import HazardTypeFactory
from yakhnama.modules.hazards.domain.value_objects import HazardTypeStatus
from yakhnama.shared_kernel.ids import Uuid7Generator, is_uuid7

TAXONOMY = HazardTaxonomy.of(
    [hazard_type("flood"), hazard_type("old_flood", is_retired=True)]
)


def _factory(registry: HazardAttributeRegistry | None = None) -> HazardTypeFactory:
    clock = stepping_clock()
    if registry is None:
        return HazardTypeFactory(Uuid7Generator(stepping_clock()), clock)
    return HazardTypeFactory(Uuid7Generator(stepping_clock()), clock, registry)


def test_create_new_code_returns_active_version_one_and_created_event() -> None:
    factory = _factory()

    change = factory.create(
        code="flash_flood",
        labels=labels("Flash flood"),
        alignment=ALIGNMENT,
        taxonomy=TAXONOMY,
        parent_code="flood",
        description=labels("Sudden flooding"),
        attributes_schema="flash_flood",
    )

    created = change.state
    assert (created.code, created.parent_code, created.attributes_schema) == (
        "flash_flood",
        "flood",
        "flash_flood",
    )
    assert created.status is HazardTypeStatus.ACTIVE
    assert created.version == 1
    assert created.created_at == created.updated_at == CREATED_AT
    assert is_uuid7(created.id)
    (event,) = change.events
    assert isinstance(event, HazardTypeCreated)
    assert (event.aggregate_id, event.code, event.parent_code) == (
        created.id,
        "flash_flood",
        "flood",
    )
    assert event.attributes_schema == "flash_flood"
    assert event.occurred_at == CREATED_AT
    assert TAXONOMY.with_hazard_type(created).has_code("flash_flood")


def test_create_root_without_schema_is_abstract_parent() -> None:
    change = _factory().create(
        code="mass_movement", labels=labels(), alignment=ALIGNMENT, taxonomy=TAXONOMY
    )

    assert change.state.parent_code is None
    assert change.state.attributes_schema is None
    assert change.state.description is None


@pytest.mark.parametrize("code", ["flood", "old_flood"])
def test_create_used_code_raises_code_already_used(code: str) -> None:
    with pytest.raises(HazardCodeAlreadyUsedError):
        _factory().create(
            code=code, labels=labels(), alignment=ALIGNMENT, taxonomy=TAXONOMY
        )


def test_create_unknown_parent_raises_hazard_type_not_found() -> None:
    with pytest.raises(HazardTypeNotFoundError):
        _factory().create(
            code="glof",
            labels=labels(),
            alignment=ALIGNMENT,
            taxonomy=TAXONOMY,
            parent_code="nowhere",
        )


def test_create_under_retired_parent_raises_hazard_type_retired() -> None:
    with pytest.raises(HazardTypeRetiredError):
        _factory().create(
            code="glof",
            labels=labels(),
            alignment=ALIGNMENT,
            taxonomy=TAXONOMY,
            parent_code="old_flood",
        )


def test_create_unregistered_schema_raises_unknown_hazard_attributes() -> None:
    with pytest.raises(UnknownHazardAttributesError):
        _factory(HazardAttributeRegistry()).create(
            code="glof",
            labels=labels(),
            alignment=ALIGNMENT,
            taxonomy=TAXONOMY,
            attributes_schema="glof",
        )


def test_create_malformed_code_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        _factory().create(
            code="Flash Flood", labels=labels(), alignment=ALIGNMENT, taxonomy=TAXONOMY
        )
