"""Builders shared by the hazards application tests.

Codes and labels are synthetic: the tests must not assert a real taxonomy, except
where they load the real reference file on purpose.
"""

from datetime import UTC, datetime

from tests.fakes.clock import FrozenClock
from tests.fakes.hazards import InMemoryHazardsUnitOfWork
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.modules.hazards.domain.entities import HazardTaxonomy, HazardType
from yakhnama.modules.hazards.domain.factories import HazardTypeFactory
from yakhnama.modules.hazards.domain.reference import HazardTypeReferenceFile
from yakhnama.modules.hazards.domain.value_objects import (
    IrdrAlignment,
    RetirementReason,
)
from yakhnama.shared_kernel.value_objects import LocalizedText

NOW = datetime(2026, 3, 1, tzinfo=UTC)
ALIGNMENT = IrdrAlignment(family="hydrological", main_event="Example")


def label_for(code: str) -> str:
    """Return the default synthetic English label of ``code``."""
    return f"Label {code}"


def entry(
    code: str,
    *,
    parent: str | None = None,
    label: str | None = None,
    retirement: RetirementReason | None = None,
    **extra: object,
) -> dict[str, object]:
    """Return one raw reference entry with sensible synthetic defaults."""
    raw: dict[str, object] = {
        "code": code,
        "parent": parent,
        "labels": {"en": label or label_for(code)},
        "alignment": ALIGNMENT.model_dump(),
        "source": "proposed",
    }
    if retirement is not None:
        raw["status"] = "retired"
        raw["retirement"] = retirement.model_dump()
    raw.update(extra)
    return raw


def reference_file(
    *entries: dict[str, object], data_version: str = "test-1"
) -> HazardTypeReferenceFile:
    """Return a validated synthetic reference file."""
    return HazardTypeReferenceFile.model_validate(
        {
            "schema_version": 1,
            "data_version": data_version,
            "source": "synthetic",
            "licence": "CC0-1.0",
            "entries": list(entries),
        }
    )


def stored_types(
    *codes_and_parents: tuple[str, str | None],
    clock: FrozenClock | None = None,
) -> tuple[HazardType, ...]:
    """Build active hazard types whose labels match ``entry`` defaults."""
    factory = HazardTypeFactory(
        SequentialIdGenerator(seed=7), clock or FrozenClock(NOW)
    )
    taxonomy = HazardTaxonomy()
    for code, parent in codes_and_parents:
        change = factory.create(
            code=code,
            labels=LocalizedText(texts={"en": label_for(code)}),
            alignment=ALIGNMENT,
            taxonomy=taxonomy,
            parent_code=parent,
        )
        taxonomy = taxonomy.with_hazard_type(change.state)
    return taxonomy.hazard_types


def unit_of_work(
    hazard_types: tuple[HazardType, ...] = (),
) -> tuple[
    InMemoryHazardsUnitOfWork, InMemoryUnitOfWorkFactory[InMemoryHazardsUnitOfWork]
]:
    """Return a fake unit of work holding ``hazard_types`` and its factory."""
    uow = InMemoryHazardsUnitOfWork(hazard_types)
    return uow, InMemoryUnitOfWorkFactory(uow)
