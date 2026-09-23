"""Builders shared by the geography application tests.

Codes and names are synthetic (``xx`` codes, "Example" names): the tests must not
assert a real hierarchy, except where they load the fixture file on purpose.
"""

from datetime import UTC, datetime

from tests.fakes.clock import FrozenClock
from tests.fakes.geography import InMemoryGeographyUnitOfWork
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.modules.geography.domain.entities import Place
from yakhnama.modules.geography.domain.factories import PlaceFactory
from yakhnama.modules.geography.domain.reference import PlaceReferenceFile

NOW = datetime(2026, 3, 1, tzinfo=UTC)


def name(
    text: str,
    *,
    language: str = "en",
    is_preferred: bool = True,
    **extra: object,
) -> dict[str, object]:
    """Return one raw reference name; non-English names get a synthetic source."""
    raw: dict[str, object] = {
        "text": text,
        "language": language,
        "is_preferred": is_preferred,
    }
    if not language.startswith("en"):
        raw["source"] = "synthetic source"
    raw.update(extra)
    return raw


def entry(
    code: str,
    level: str,
    *,
    parent_code: str | None = None,
    names: list[dict[str, object]] | None = None,
    **extra: object,
) -> dict[str, object]:
    """Return one raw reference entry with a single preferred English name."""
    raw: dict[str, object] = {
        "code": code,
        "level": level,
        "parent_code": parent_code,
        "names": names or [name(f"Example {code}")],
        "status": "fixture",
    }
    raw.update(extra)
    return raw


def reference_file(
    *entries: dict[str, object], data_version: str = "test-1"
) -> PlaceReferenceFile:
    """Return a validated synthetic reference file."""
    return PlaceReferenceFile.model_validate(
        {
            "schema_version": 1,
            "data_version": data_version,
            "source": "synthetic",
            "licence": "CC0-1.0",
            "entries": list(entries),
        }
    )


def country_and_region() -> PlaceReferenceFile:
    """Return a two-level file: country ``xx`` and region ``xx.a``."""
    return reference_file(
        entry("xx", "country"),
        entry("xx.a", "province_or_region", parent_code="xx"),
    )


def stored_places(file: PlaceReferenceFile) -> tuple[Place, ...]:
    """Create every place of ``file`` exactly as the loader would, parents first."""
    factory = PlaceFactory()
    ids = SequentialIdGenerator(seed=13)
    clock = FrozenClock(NOW)
    created: dict[str, Place] = {}
    for draft in file.to_factory_inputs():
        parent = None if draft.parent_code is None else created[draft.parent_code]
        created[draft.code] = factory.create(
            draft, parent=parent, ids=ids, clock=clock
        ).state
    return tuple(created.values())


def retired(place: Place, reason: str = "abolished") -> Place:
    """Return ``place`` retired."""
    return place.retire(
        reason, clock=FrozenClock(NOW), ids=SequentialIdGenerator(seed=17)
    ).state


def unit_of_work(
    places: tuple[Place, ...] = (),
) -> tuple[
    InMemoryGeographyUnitOfWork,
    InMemoryUnitOfWorkFactory[InMemoryGeographyUnitOfWork],
]:
    """Return a fake unit of work holding ``places`` and its factory."""
    uow = InMemoryGeographyUnitOfWork(places)
    return uow, InMemoryUnitOfWorkFactory(uow)
