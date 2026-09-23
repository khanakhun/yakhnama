"""Unit tests for ``tests.fakes.clock``."""

from datetime import UTC, datetime, timedelta, timezone
from typing import get_protocol_members

import pytest

from tests.fakes.clock import FrozenClock, SteppingClock
from yakhnama.shared_kernel.clock import Clock

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
PAKISTAN = timezone(timedelta(hours=5))


@pytest.mark.parametrize("fake_class", [FrozenClock, SteppingClock])
def test_clock_fakes_declare_every_protocol_member(fake_class: type[object]) -> None:
    members = get_protocol_members(Clock)

    missing = {member for member in members if not hasattr(fake_class, member)}

    assert missing == set()


def test_frozen_clock_now_returns_the_same_utc_instant_every_call() -> None:
    clock: Clock = FrozenClock(NOW)

    readings = [clock.now() for _ in range(3)]

    assert readings == [NOW, NOW, NOW]
    assert all(reading.tzinfo is UTC for reading in readings)


def test_frozen_clock_offset_instant_is_normalised_to_utc() -> None:
    local = datetime(2026, 9, 23, 17, 0, tzinfo=PAKISTAN)

    reading = FrozenClock(local).now()

    assert reading == NOW
    assert reading.tzinfo is UTC


def test_frozen_clock_naive_instant_raises_value_error() -> None:
    naive = NOW.replace(tzinfo=None)

    with pytest.raises(ValueError, match="timezone-aware"):
        FrozenClock(naive)


def test_frozen_clock_move_to_and_advance_change_the_instant() -> None:
    clock = FrozenClock(NOW)

    clock.move_to(NOW + timedelta(days=1))
    moved = clock.now()
    clock.advance(timedelta(hours=-2))
    advanced = clock.now()

    assert moved == NOW + timedelta(days=1)
    assert advanced == NOW + timedelta(days=1, hours=-2)
    assert clock.calls == 2


def test_frozen_clock_move_to_naive_instant_raises_value_error() -> None:
    clock = FrozenClock(NOW)

    with pytest.raises(ValueError, match="timezone-aware"):
        clock.move_to(NOW.replace(tzinfo=None))


def test_stepping_clock_now_advances_one_step_per_call() -> None:
    clock: Clock = SteppingClock(NOW, timedelta(minutes=1))

    readings = [clock.now() for _ in range(3)]

    assert readings == [NOW + timedelta(minutes=minutes) for minutes in range(3)]
    assert all(reading.tzinfo is UTC for reading in readings)


def test_stepping_clock_peek_returns_next_reading_without_advancing() -> None:
    clock = SteppingClock(NOW, timedelta(seconds=1))

    peeked = clock.peek()
    reading = clock.now()

    assert peeked == reading == NOW
    assert clock.peek() == NOW + timedelta(seconds=1)
    assert clock.calls == 1


def test_stepping_clock_offset_start_is_normalised_to_utc() -> None:
    clock = SteppingClock(datetime(2026, 9, 23, 17, 0, tzinfo=PAKISTAN), timedelta(1))

    reading = clock.now()

    assert reading == NOW
    assert reading.tzinfo is UTC


def test_stepping_clock_naive_start_raises_value_error() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        SteppingClock(NOW.replace(tzinfo=None), timedelta(seconds=1))


@pytest.mark.parametrize("step", [timedelta(0), timedelta(seconds=-1)])
def test_stepping_clock_non_positive_step_raises_value_error(step: timedelta) -> None:
    with pytest.raises(ValueError, match="step must be positive"):
        SteppingClock(NOW, step)
