"""Fixtures for the impacts domain tests: a stepping clock, ids and the factory.

The clock is the shared ``tests.fakes.clock.SteppingClock``, one minute per reading.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tests.fakes.clock import SteppingClock
from yakhnama.modules.impacts.domain.factories import ImpactMetricFactory
from yakhnama.shared_kernel.ids import Uuid7Generator

START = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
ONE_MINUTE = timedelta(minutes=1)


@pytest.fixture
def clock() -> SteppingClock:
    return SteppingClock(START, ONE_MINUTE)


@pytest.fixture
def id_generator() -> Uuid7Generator:
    return Uuid7Generator(clock=SteppingClock(START, ONE_MINUTE))


@pytest.fixture
def factory(clock: SteppingClock, id_generator: Uuid7Generator) -> ImpactMetricFactory:
    return ImpactMetricFactory(clock=clock, id_generator=id_generator)
