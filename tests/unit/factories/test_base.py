"""Unit tests for ``tests.factories.base``."""

from datetime import UTC, datetime

from tests.factories.base import (
    EARLIEST_FACTORY_INSTANT,
    LATEST_FACTORY_INSTANT,
    pick,
    random_instant,
    sequence,
    uniform,
)


def test_pick_returns_only_the_given_options() -> None:
    provider = pick(["a", "b"])

    values = {provider() for _ in range(100)}

    assert values == {"a", "b"}


def test_uniform_stays_within_bounds_and_rounds() -> None:
    values = [uniform(1.0, 2.0, digits=2) for _ in range(200)]

    assert all(1.0 <= value <= 2.0 for value in values)
    assert all(round(value, 2) == value for value in values)


def test_uniform_equal_bounds_returns_the_bound() -> None:
    value = uniform(3.5, 3.5)

    assert value == 3.5


def test_random_instant_is_utc_whole_second_and_within_default_bounds() -> None:
    instants = [random_instant() for _ in range(200)]

    assert all(instant.tzinfo is UTC for instant in instants)
    assert all(instant.microsecond == 0 for instant in instants)
    assert all(
        EARLIEST_FACTORY_INSTANT <= instant <= LATEST_FACTORY_INSTANT
        for instant in instants
    )


def test_random_instant_equal_bounds_returns_the_bound() -> None:
    moment = datetime(2026, 9, 23, tzinfo=UTC)

    instant = random_instant(moment, moment)

    assert instant == moment


def test_sequence_numbers_from_one_and_never_repeats() -> None:
    provider = sequence("item_{:03d}")

    values = [provider() for _ in range(3)]

    assert values == ["item_001", "item_002", "item_003"]
