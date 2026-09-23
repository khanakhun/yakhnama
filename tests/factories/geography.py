"""Factories for the ``geography`` domain: place names and places.

``PlaceTestFactory`` is suffixed ``TestFactory`` because the domain already has a
``PlaceFactory`` (``yakhnama.modules.geography.domain.factories``) that tests import
alongside this one; one name for two different things would make every such test
ambiguous. This factory builds a ``Place`` directly, bypassing the domain factory's
parent lookup, so it is for arranging state, not for testing creation rules.

Patterns: Factory.
"""

from collections.abc import Mapping
from uuid import UUID

from polyfactory import PostGenerated, Use

from tests.factories.base import (
    FACTORY_IDS,
    YakhnamaModelFactory,
    pick,
    random_instant,
    sequence,
)
from tests.factories.shared_kernel import CoordinatesFactory
from yakhnama.modules.geography.domain.entities import Place
from yakhnama.modules.geography.domain.value_objects import (
    AdminLevel,
    PlaceName,
    ScriptCode,
)


def _parent_id_for_level(_name: str, values: Mapping[str, object]) -> UUID | None:
    # A country is the only level without a parent (Place invariant); every other
    # level gets a fresh parent id, which is never the place's own id.
    if values["level"] == AdminLevel.COUNTRY:
        return None
    return FACTORY_IDS.new_id()


def _same_as_created_at(_name: str, values: Mapping[str, object]) -> object:
    # A place at version 1 has not changed since it was created.
    return values["created_at"]


class PlaceNameFactory(YakhnamaModelFactory[PlaceName]):
    """Builds unique English place names in Latin script or without a script.

    The texts are placeholders (``"Test place name <n>"``), never real local names.

    Implements: Factory.
    """

    __model__ = PlaceName

    text = sequence("Test place name {}")
    language = "en"
    script = pick([None, ScriptCode.LATN])


def _single_preferred_name() -> tuple[PlaceName, ...]:
    return (PlaceNameFactory.build(is_preferred=True),)


class PlaceTestFactory(YakhnamaModelFactory[Place]):
    """Builds active places at version 1 with a consistent level and parent.

    Each place has one preferred English name, a centroid inside the test region, no
    geometry, and ``updated_at == created_at``. Pass ``level=`` to choose the level;
    the parent id follows it.

    Implements: Factory.
    """

    __model__ = Place

    id = Use(FACTORY_IDS.new_id)
    code = sequence("test.place.{:05d}")
    level = pick(list(AdminLevel))
    parent_id = PostGenerated(_parent_id_for_level)
    names = Use(_single_preferred_name)
    centroid = Use(CoordinatesFactory.build)
    created_at = Use(random_instant)
    updated_at = PostGenerated(_same_as_created_at)
