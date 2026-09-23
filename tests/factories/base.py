"""The base factory and the shared, deterministic sources every factory draws from.

Patterns: Factory.
"""

import itertools
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from random import Random
from typing import ClassVar

from faker import Faker
from polyfactory.factories.base import BaseFactory
from polyfactory.factories.pydantic_factory import ModelFactory
from polyfactory.field_meta import FieldMeta
from pydantic import BaseModel

from tests.fakes.ids import SequentialIdGenerator

FACTORY_SEED = 20260923
"""Seed of every random choice the factories make."""

FACTORY_RANDOM = Random(FACTORY_SEED)  # noqa: S311  # reason: seeded test-data values, never secrets or ids
"""The one random source all factories share, so a run is reproducible end to end."""

FACTORY_IDS = SequentialIdGenerator(seed=FACTORY_SEED)
"""The one id source all factories share, so ids are distinct across factories."""

EARLIEST_FACTORY_INSTANT = datetime(2000, 1, 1, tzinfo=UTC)
LATEST_FACTORY_INSTANT = datetime(2026, 9, 1, tzinfo=UTC)

_FAKER = Faker()
_FAKER.seed_instance(FACTORY_SEED)


def pick[ValueT](options: Sequence[ValueT]) -> Callable[[], ValueT]:
    """Return a field provider that picks one of ``options`` at random.

    Args:
        options: The allowed values, for example ``get_args(SomeLiteral)``.

    Returns:
        A zero-argument callable polyfactory calls once per build.
    """
    choices = tuple(options)
    return lambda: FACTORY_RANDOM.choice(choices)


def uniform(low: float, high: float, digits: int = 6) -> float:
    """Return a random float in ``[low, high]``, rounded to ``digits`` decimals.

    Rounding keeps values readable in failure messages; it cannot leave the range
    because both bounds are themselves rounded values in every caller.

    Args:
        low: Lower bound, inclusive.
        high: Upper bound, inclusive.
        digits: Decimal places to keep.

    Returns:
        The random value.
    """
    return min(max(round(FACTORY_RANDOM.uniform(low, high), digits), low), high)


def random_instant(
    earliest: datetime = EARLIEST_FACTORY_INSTANT,
    latest: datetime = LATEST_FACTORY_INSTANT,
) -> datetime:
    """Return a random whole-second UTC instant in ``[earliest, latest]``.

    Args:
        earliest: Lower bound, timezone-aware.
        latest: Upper bound, timezone-aware.

    Returns:
        A timezone-aware ``datetime`` in ``datetime.UTC``.
    """
    span = int((latest - earliest).total_seconds())
    return (earliest + timedelta(seconds=FACTORY_RANDOM.randint(0, span))).astimezone(
        UTC
    )


def sequence(template: str) -> Callable[[], str]:
    """Return a provider of unique strings ``template.format(n)`` for n = 1, 2, ...

    Args:
        template: A ``str.format`` template with one positional field, for example
            ``"test_hazard_{:05d}"``.

    Returns:
        A zero-argument callable returning the next string.
    """
    counter: Iterator[int] = itertools.count(1)
    return lambda: template.format(next(counter))


class YakhnamaModelFactory[ModelT: BaseModel](ModelFactory[ModelT]):
    """Base of every test factory: seeded, deterministic, and explicit about fields.

    Fields with a model default keep that default unless the factory declares the
    field, so optional nested objects (geometries, retirements) never appear at random
    and break an invariant. Declared fields always win over defaults.

    Implements: Factory.
    """

    __is_base_factory__ = True
    __random__: ClassVar[Random] = FACTORY_RANDOM
    __faker__: ClassVar[Faker] = _FAKER
    __use_defaults__: ClassVar[bool] = True

    @classmethod
    def should_use_default_value(cls, field_meta: FieldMeta) -> bool:
        """Use a field's model default unless the factory declares the field.

        Args:
            field_meta: polyfactory's description of the field.

        Returns:
            ``True`` if polyfactory should leave the field to the model default.
        """
        is_declared = hasattr(cls, field_meta.name) and not hasattr(
            BaseFactory, field_meta.name
        )
        return not is_declared and super().should_use_default_value(field_meta)
