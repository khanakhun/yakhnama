"""Unit tests for ``tests.fakes.ids``."""

import itertools
from datetime import UTC, datetime, timedelta
from typing import get_protocol_members
from uuid import UUID, uuid4

import pytest

from tests.fakes.ids import (
    DEFAULT_ID_START,
    DEFAULT_ID_STEP,
    FixedIdGenerator,
    SequentialIdGenerator,
)
from yakhnama.shared_kernel.ids import IdGenerator, extract_timestamp, is_uuid7

START = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("fake_class", [SequentialIdGenerator, FixedIdGenerator])
def test_id_fakes_declare_every_protocol_member(fake_class: type[object]) -> None:
    members = get_protocol_members(IdGenerator)

    missing = {member for member in members if not hasattr(fake_class, member)}

    assert missing == set()


def test_sequential_id_generator_ids_are_valid_distinct_and_increasing() -> None:
    generator: IdGenerator = SequentialIdGenerator()

    identifiers = [generator.new_id() for _ in range(200)]

    assert all(is_uuid7(identifier) for identifier in identifiers)
    assert len(set(identifiers)) == len(identifiers)
    assert all(earlier < later for earlier, later in itertools.pairwise(identifiers))


def test_sequential_id_generator_same_arguments_yield_the_same_sequence() -> None:
    first = SequentialIdGenerator(START, seed=7)
    second = SequentialIdGenerator(START, seed=7)

    first_ids = [first.new_id() for _ in range(20)]
    second_ids = [second.new_id() for _ in range(20)]

    assert first_ids == second_ids
    assert first.issued == first_ids


def test_sequential_id_generator_different_seeds_yield_different_ids() -> None:
    first = SequentialIdGenerator(START, seed=1)
    second = SequentialIdGenerator(START, seed=2)

    first_id, second_id = first.new_id(), second.new_id()

    assert first_id != second_id
    assert extract_timestamp(first_id) == extract_timestamp(second_id) == START


def test_sequential_id_generator_embeds_start_plus_one_step_per_id() -> None:
    generator = SequentialIdGenerator(START, timedelta(seconds=1))

    timestamps = [extract_timestamp(generator.new_id()) for _ in range(3)]

    assert timestamps == [START + timedelta(seconds=seconds) for seconds in range(3)]


def test_sequential_id_generator_defaults_start_at_the_documented_instant() -> None:
    generator = SequentialIdGenerator()

    timestamps = [extract_timestamp(generator.new_id()) for _ in range(2)]

    assert timestamps == [DEFAULT_ID_START, DEFAULT_ID_START + DEFAULT_ID_STEP]


def test_sequential_id_generator_naive_start_raises_value_error() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        SequentialIdGenerator(START.replace(tzinfo=None))


def test_fixed_id_generator_returns_preset_ids_in_order() -> None:
    source = SequentialIdGenerator(START)
    preset = [source.new_id(), source.new_id()]
    generator: IdGenerator = FixedIdGenerator(preset)

    identifiers = [generator.new_id(), generator.new_id()]

    assert identifiers == preset


def test_fixed_id_generator_exhausted_raises_runtime_error() -> None:
    generator = FixedIdGenerator([SequentialIdGenerator(START).new_id()])
    generator.new_id()

    with pytest.raises(RuntimeError, match="preset ids have been used"):
        generator.new_id()

    assert generator.remaining == 0


def test_fixed_id_generator_remaining_counts_down() -> None:
    source = SequentialIdGenerator(START)
    generator = FixedIdGenerator([source.new_id(), source.new_id()])

    before = generator.remaining
    generator.new_id()

    assert (before, generator.remaining) == (2, 1)


@pytest.mark.parametrize("identifier", [uuid4(), UUID(int=0)])
def test_fixed_id_generator_non_uuid7_raises_value_error(identifier: UUID) -> None:
    with pytest.raises(ValueError, match="UUIDv7"):
        FixedIdGenerator([identifier])
