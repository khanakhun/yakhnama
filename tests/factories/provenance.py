"""Factories for the ``provenance`` domain: sources and their details.

``SourceTestFactory`` is suffixed ``TestFactory`` because the domain already has a
``SourceFactory`` (``yakhnama.modules.provenance.domain.factories``). These factories
build models directly, bypassing registration, so they arrange state; they do not
test creation rules.

Titles, citations and publishers are placeholders (``"Test source <n>"``), never real
people, organisations or publications; URLs use the reserved ``example.test`` domain
(RFC 2606).

Patterns: Factory.
"""

from collections.abc import Mapping

from polyfactory import PostGenerated, Use

from tests.factories.base import (
    FACTORY_IDS,
    YakhnamaModelFactory,
    pick,
    random_instant,
    sequence,
)
from yakhnama.modules.provenance.domain.entities import Source
from yakhnama.modules.provenance.domain.value_objects import (
    SourceDetails,
    SourceType,
)

TEST_SOURCE_URL = "https://sources.example.test/documents/1"
"""A URL every factory-built source may use; ``example.test`` is reserved."""


def _same_as_created_at(_name: str, values: Mapping[str, object]) -> object:
    # A record at version 1 has not changed since it was registered.
    return values["created_at"]


class SourceDetailsTestFactory(YakhnamaModelFactory[SourceDetails]):
    """Builds details with a title and citation only; optional fields stay ``None``.

    Implements: Factory.
    """

    __model__ = SourceDetails

    title = sequence("Test source {}")
    citation = sequence("Test citation {}")


class SourceTestFactory(YakhnamaModelFactory[Source]):
    """Builds unreferenced sources of a random type at version 1.

    Optional fields (URL, licence, retrieval time, publisher, language, owner) keep
    their ``None`` defaults unless passed.

    Implements: Factory.
    """

    __model__ = Source

    id = Use(FACTORY_IDS.new_id)
    source_type = Use(pick(tuple(SourceType)))
    title = sequence("Test source {}")
    citation = sequence("Test citation {}")
    is_referenced = False
    version = 1
    created_at = Use(random_instant)
    updated_at = PostGenerated(_same_as_created_at)
