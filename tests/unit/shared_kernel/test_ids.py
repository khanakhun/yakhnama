"""Unit tests for ``yakhnama.shared_kernel.ids``."""

import itertools
import uuid
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ConfigDict
from pydantic import ValidationError as PydanticValidationError

from yakhnama.shared_kernel.errors import InvariantViolationError, ValidationError
from yakhnama.shared_kernel.ids import (
    RANDOM_BYTES_PER_ID,
    EntityId,
    IdGenerator,
    Uuid7Generator,
    extract_timestamp,
    is_uuid7,
)

# The last millisecond ``datetime`` can represent, far below the 48-bit ceiling.
LATEST_INSTANT = datetime(9999, 12, 31, 23, 59, 59, 999000, tzinfo=UTC)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

instants = st.datetimes(
    min_value=EPOCH.replace(tzinfo=None),
    max_value=LATEST_INSTANT.replace(tzinfo=None),
    timezones=st.just(UTC),
)


class _FrozenClock:
    """Clock that returns whatever instant the test sets.

    Implements: Fake.
    """

    def __init__(self, instant: datetime) -> None:
        self.instant = instant

    def now(self) -> datetime:
        return self.instant


class _ConstantRandom:
    """Random source returning the same byte every time.

    Implements: Fake.
    """

    def __init__(self, value: int) -> None:
        self._value = value

    def __call__(self, count: int) -> bytes:
        return bytes([self._value]) * count


def _floor_to_millisecond(instant: datetime) -> datetime:
    return instant.replace(microsecond=instant.microsecond // 1000 * 1000)


class _Identified(BaseModel):
    """Model with an ``EntityId`` field.

    Implements: Value Object.
    """

    model_config = ConfigDict(frozen=True)

    id: EntityId


@given(instant=instants)
def test_uuid7_generator_new_id_any_instant_sets_version_and_variant(
    instant: datetime,
) -> None:
    generator = Uuid7Generator(_FrozenClock(instant))

    identifier = generator.new_id()

    assert identifier.version == 7
    assert identifier.variant == uuid.RFC_4122
    assert is_uuid7(identifier)


@given(instant=instants)
def test_extract_timestamp_generated_id_returns_instant_floored_to_millisecond(
    instant: datetime,
) -> None:
    generator = Uuid7Generator(_FrozenClock(instant))

    extracted = extract_timestamp(generator.new_id())

    assert extracted == _floor_to_millisecond(instant)
    assert extracted.tzinfo is UTC


def test_uuid7_generator_ten_thousand_ids_in_one_millisecond_strictly_increase() -> (
    None
):
    instant = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    generator = Uuid7Generator(_FrozenClock(instant))

    identifiers = [generator.new_id() for _ in range(10_000)]

    assert identifiers == sorted(identifiers)
    assert len(set(identifiers)) == len(identifiers)
    assert {extract_timestamp(identifier) for identifier in identifiers} == {instant}


@given(
    offsets=st.lists(st.integers(min_value=-5_000, max_value=5_000), max_size=50),
)
def test_uuid7_generator_clock_moving_both_ways_ids_strictly_increase(
    offsets: list[int],
) -> None:
    clock = _FrozenClock(datetime(2026, 9, 23, tzinfo=UTC))
    generator = Uuid7Generator(clock)
    identifiers = [generator.new_id()]

    for offset in offsets:
        clock.instant += timedelta(milliseconds=offset)
        identifiers.append(generator.new_id())

    assert all(earlier < later for earlier, later in itertools.pairwise(identifiers))


def test_uuid7_generator_default_sources_ids_are_unique_and_ordered() -> None:
    generator: IdGenerator = Uuid7Generator()

    identifiers = [generator.new_id() for _ in range(1_000)]

    assert identifiers == sorted(identifiers)
    assert len(set(identifiers)) == len(identifiers)


def test_uuid7_generator_new_millisecond_reseeds_counter_from_random_source() -> None:
    clock = _FrozenClock(datetime(2026, 9, 23, tzinfo=UTC))
    generator = Uuid7Generator(clock, random_source=_ConstantRandom(0x00))
    first = generator.new_id()

    clock.instant += timedelta(milliseconds=1)
    second = generator.new_id()

    # A zero random source gives a zero counter and tail; only the timestamp differs.
    assert first.int & ((1 << 80) - 1) == second.int & ((1 << 80) - 1)
    assert extract_timestamp(second) - extract_timestamp(first) == timedelta(
        milliseconds=1
    )


def test_uuid7_generator_known_inputs_produce_rfc_layout() -> None:
    # RFC 9562 appendix A.6 example timestamp: 0x017F22E279B0 = 2022-02-22T19:22:22Z.
    instant = datetime(2022, 2, 22, 19, 22, 22, tzinfo=UTC)
    generator = Uuid7Generator(_FrozenClock(instant), random_source=_ConstantRandom(0))

    identifier = generator.new_id()

    assert str(identifier) == "017f22e2-79b0-7000-8000-000000000000"


def test_uuid7_generator_seed_leaves_top_counter_bit_clear() -> None:
    instant = datetime(2026, 9, 23, tzinfo=UTC)
    generator = Uuid7Generator(
        _FrozenClock(instant), random_source=_ConstantRandom(0xFF)
    )

    identifier = generator.new_id()

    # rand_a holds the top 12 counter bits; the rollover guard clears the highest.
    assert (identifier.int >> 64) & 0xFFF == 0x7FF
    assert identifier.int & 0xFFFFFFFF == 0xFFFFFFFF


def test_uuid7_generator_counter_exhausted_advances_timestamp_one_millisecond() -> None:
    instant = datetime(2026, 9, 23, tzinfo=UTC)
    generator = Uuid7Generator(
        _FrozenClock(instant), random_source=_ConstantRandom(0xFF)
    )
    first = generator.new_id()
    # Reaching the 2**41 increments naturally would take hours, so the test sets
    # the private counter to its ceiling directly.
    generator._counter = (1 << 42) - 1

    second = generator.new_id()

    assert second > first
    assert extract_timestamp(second) == instant + timedelta(milliseconds=1)


def test_uuid7_generator_naive_clock_raises_invariant_violation() -> None:
    naive = datetime(2026, 9, 23, tzinfo=UTC).replace(tzinfo=None)
    generator = Uuid7Generator(_FrozenClock(naive))

    with pytest.raises(InvariantViolationError, match="naive"):
        generator.new_id()


def test_uuid7_generator_instant_before_epoch_raises_invariant_violation() -> None:
    generator = Uuid7Generator(_FrozenClock(EPOCH - timedelta(milliseconds=1)))

    with pytest.raises(InvariantViolationError, match="1970"):
        generator.new_id()


def test_uuid7_generator_non_utc_clock_encodes_same_instant() -> None:
    karachi = timezone(timedelta(hours=5))
    instant = datetime(2026, 9, 23, 17, 0, tzinfo=karachi)
    generator = Uuid7Generator(_FrozenClock(instant))

    extracted = extract_timestamp(generator.new_id())

    assert extracted == instant


@pytest.mark.parametrize("count", [0, RANDOM_BYTES_PER_ID - 1, RANDOM_BYTES_PER_ID + 1])
def test_uuid7_generator_random_source_wrong_length_raises_invariant_violation(
    count: int,
) -> None:
    generator = Uuid7Generator(
        _FrozenClock(datetime(2026, 9, 23, tzinfo=UTC)),
        random_source=lambda _requested: b"\x00" * count,
    )

    with pytest.raises(InvariantViolationError, match="random source"):
        generator.new_id()


@pytest.mark.parametrize(
    "identifier",
    [
        uuid.uuid4(),
        UUID(int=0),
        # Version nibble 7 but the Microsoft variant (0b110).
        UUID("017f22e2-79b0-7000-c000-000000000000"),
    ],
)
def test_is_uuid7_other_versions_or_variants_returns_false(identifier: UUID) -> None:
    result = is_uuid7(identifier)

    assert result is False


def test_extract_timestamp_non_uuid7_raises_validation_error() -> None:
    identifier = uuid.uuid4()

    with pytest.raises(ValidationError) as caught:
        extract_timestamp(identifier)

    assert caught.value.details["identifier"] == str(identifier)


def test_entity_id_uuid7_value_is_accepted() -> None:
    identifier = Uuid7Generator().new_id()

    model = _Identified(id=identifier)

    assert model.id == identifier


def test_entity_id_uuid4_value_raises_pydantic_validation_error() -> None:
    identifier = uuid.uuid4()

    with pytest.raises(PydanticValidationError, match="UUIDv7"):
        _Identified(id=identifier)
