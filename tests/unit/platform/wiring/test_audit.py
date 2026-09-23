"""Unit tests for ``yakhnama.platform.wiring.audit``: event types and subscription."""

import importlib
import pkgutil
from types import ModuleType
from typing import ClassVar, Final

import pytest

import yakhnama.modules
from tests.unit.platform.events import make_event
from yakhnama.platform.outbox.envelope import OutboxEnvelope
from yakhnama.platform.outbox.relay import SubscriberRegistry
from yakhnama.platform.wiring.audit import (
    EVENT_MODULES,
    DomainEventTypeRegistry,
    concrete_event_classes,
    subscribe_to_every_event,
)
from yakhnama.shared_kernel.errors import ConflictError
from yakhnama.shared_kernel.events import DomainEvent

REPORTS_EVENT_TYPES: Final = {
    "reports.report_submitted",
    "reports.report_revised",
    "reports.report_superseded",
    "reports.report_withdrawn",
    "reports.report_triaged",
}


def _module_event_files() -> list[ModuleType]:
    modules: list[ModuleType] = []
    for info in pkgutil.iter_modules(yakhnama.modules.__path__):
        name = f"yakhnama.modules.{info.name}.domain.events"
        try:
            modules.append(importlib.import_module(name))
        except ModuleNotFoundError:
            continue
    return modules


def test_concrete_event_classes_skip_shared_bases_and_imports() -> None:
    reports = next(module for module in EVENT_MODULES if "reports" in module.__name__)

    classes = concrete_event_classes(reports)

    assert {event_class.event_type for event_class in classes} == REPORTS_EVENT_TYPES
    assert all(event_class.__module__ == reports.__name__ for event_class in classes)
    # The shared base declares no event_type; DomainEvent itself is imported.
    assert {event_class.__name__ for event_class in classes}.isdisjoint(
        {"ReportEvent", "DomainEvent"}
    )


def test_event_modules_cover_every_module_that_defines_events() -> None:
    registry = DomainEventTypeRegistry.from_modules(EVENT_MODULES)

    expected = {
        event_class.event_type
        for module in _module_event_files()
        for event_class in concrete_event_classes(module)
    }

    assert expected
    assert set(registry.event_types()) == expected


def test_domain_event_type_registry_resolves_known_and_unknown_types() -> None:
    registry = DomainEventTypeRegistry.from_modules(EVENT_MODULES)
    submitted = next(
        event_class
        for module in EVENT_MODULES
        for event_class in concrete_event_classes(module)
        if event_class.event_type == "reports.report_submitted"
    )

    assert registry.resolve("reports.report_submitted") is submitted
    assert registry.resolve("reports.never_happened") is None


class _FirstProbe(DomainEvent):
    """A probe event.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "wiring_test.probe_happened"


class _SecondProbe(DomainEvent):
    """Another class claiming the same event type.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "wiring_test.probe_happened"


def test_domain_event_type_registry_duplicate_event_type_raises_conflict() -> None:
    with pytest.raises(ConflictError):
        DomainEventTypeRegistry([_FirstProbe, _SecondProbe])


def test_domain_event_type_registry_same_class_twice_is_indexed_once() -> None:
    registry = DomainEventTypeRegistry([_FirstProbe, _FirstProbe])

    assert registry.event_types() == ("wiring_test.probe_happened",)


async def _noop_subscriber(envelope: OutboxEnvelope) -> None:
    del envelope


def test_subscribe_to_every_event_registers_the_subscriber_for_each_type() -> None:
    registry = SubscriberRegistry()
    event_types = DomainEventTypeRegistry.from_modules(EVENT_MODULES)

    subscribe_to_every_event(registry, event_types, _noop_subscriber)

    assert all(
        registry.subscribers_for(event_type) == (_noop_subscriber,)
        for event_type in event_types.event_types()
    )
    assert registry.subscribers_for(make_event().event_type) == ()
