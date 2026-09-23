"""Fixtures for the impacts domain tests: a steppable clock, ids and the factory."""

from datetime import UTC, datetime, timedelta

import pytest

from yakhnama.modules.impacts.domain.factories import ImpactMetricFactory
from yakhnama.shared_kernel.ids import Uuid7Generator

START = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


class SteppingClock:
    """Clock that advances one minute on every reading.

    Implements: Fake.
    """

    def __init__(self, start: datetime = START) -> None:
        """Start at ``start``."""
        self._next = start

    def now(self) -> datetime:
        """Return the current instant and advance by one minute."""
        current = self._next
        self._next += timedelta(minutes=1)
        return current


@pytest.fixture
def clock() -> SteppingClock:
    return SteppingClock()


@pytest.fixture
def id_generator() -> Uuid7Generator:
    return Uuid7Generator(clock=SteppingClock())


@pytest.fixture
def factory(clock: SteppingClock, id_generator: Uuid7Generator) -> ImpactMetricFactory:
    return ImpactMetricFactory(clock=clock, id_generator=id_generator)
