"""Builders shared by the impacts application tests.

Codes and labels are synthetic: the tests must not assert a real registry, except
where they load the real reference file on purpose.
"""

from datetime import UTC, datetime

from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.impacts import InMemoryImpactsUnitOfWork
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.factories import ImpactMetricFactory
from yakhnama.modules.impacts.domain.reference import (
    ImpactMetricReferenceEntry,
    ImpactMetricReferenceFile,
)
from yakhnama.modules.impacts.domain.value_objects import RetirementReason

NOW = datetime(2026, 3, 1, tzinfo=UTC)


def entry(
    code: str,
    *,
    label: str | None = None,
    retirement: RetirementReason | None = None,
    **extra: object,
) -> dict[str, object]:
    """Return one raw count-metric entry with synthetic defaults."""
    raw: dict[str, object] = {
        "code": code,
        "labels": {"en": label or f"Label {code}"},
        "category": "human",
        "value_kind": "count",
        "unit": "count",
        "aggregation": "sum",
        "source": "proposed",
    }
    if retirement is not None:
        raw["status"] = "retired"
        raw["retirement"] = retirement.model_dump()
    raw.update(extra)
    return raw


def reference_file(
    *entries: dict[str, object], data_version: str = "test-1"
) -> ImpactMetricReferenceFile:
    """Return a validated synthetic reference file."""
    return ImpactMetricReferenceFile.model_validate(
        {
            "schema_version": 1,
            "data_version": data_version,
            "source": "synthetic",
            "licence": "CC0-1.0",
            "entries": list(entries),
        }
    )


def stored_metric(
    code: str, *, retirement: RetirementReason | None = None
) -> ImpactMetric:
    """Build a stored metric equal to what ``entry(code, ...)`` would create."""
    factory = ImpactMetricFactory(
        clock=FrozenClock(NOW), id_generator=SequentialIdGenerator(seed=11)
    )
    reference = ImpactMetricReferenceEntry.model_validate(
        entry(code, retirement=retirement)
    )
    return factory.create_from_reference(reference).state


def unit_of_work(
    metrics: tuple[ImpactMetric, ...] = (),
) -> tuple[
    InMemoryImpactsUnitOfWork, InMemoryUnitOfWorkFactory[InMemoryImpactsUnitOfWork]
]:
    """Return a fake unit of work holding ``metrics`` and its factory."""
    uow = InMemoryImpactsUnitOfWork(metrics)
    return uow, InMemoryUnitOfWorkFactory(uow)
