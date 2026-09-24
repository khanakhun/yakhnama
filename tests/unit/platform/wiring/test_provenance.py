"""Unit tests for ``yakhnama.platform.wiring.provenance``.

The events side is an in-memory ``EventCitationQueryService``; the provenance side
is the real ``AuthorisedSourceQueryService`` over the provenance fakes, so the test
shows the adapter changing what an anonymous reader may see.
"""

from typing import Final

import pytest

from tests.factories.provenance import SourceTestFactory
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.provenance import (
    InMemoryProvenanceUnitOfWork,
    InMemorySourceQueryService,
)
from yakhnama.modules.identity.public import Actor
from yakhnama.modules.provenance.public import (
    AuthorisedSourceQueryService,
    GetSource,
    Source,
    SourceNotFoundError,
    SourceType,
)
from yakhnama.platform.wiring.provenance import SourceCitationCheckerAdapter
from yakhnama.shared_kernel.ids import EntityId

IDS: Final = SequentialIdGenerator(seed=811)


class InMemoryEventCitations:
    """``EventCitationQueryService`` over a fixed set of publicly cited sources.

    Implements: Fake.
    """

    def __init__(self, cited: frozenset[EntityId]) -> None:
        """Answer ``True`` for exactly ``cited``."""
        self.cited = cited
        self.asked: list[EntityId] = []

    async def is_source_cited_by_public_event(self, source_id: EntityId) -> bool:
        """Tell whether ``source_id`` is in the cited set, recording the question."""
        self.asked.append(source_id)
        return source_id in self.cited


async def test_source_citation_checker_adapter_cited_source_is_cited() -> None:
    source_id = IDS.new_id()
    citations = InMemoryEventCitations(frozenset({source_id}))
    adapter = SourceCitationCheckerAdapter(citations)

    is_cited = await adapter.is_cited_by_published_event(source_id)

    assert is_cited is True
    assert citations.asked == [source_id]


async def test_source_citation_checker_adapter_uncited_source_is_not_cited() -> None:
    adapter = SourceCitationCheckerAdapter(
        InMemoryEventCitations(frozenset({IDS.new_id()}))
    )

    is_cited = await adapter.is_cited_by_published_event(IDS.new_id())

    assert is_cited is False


def _read_side(
    source: Source, citations: InMemoryEventCitations
) -> AuthorisedSourceQueryService:
    uow = InMemoryProvenanceUnitOfWork(sources=[source])
    return AuthorisedSourceQueryService(
        InMemorySourceQueryService(uow),
        citation_checker=SourceCitationCheckerAdapter(citations),
    )


async def test_source_citation_checker_adapter_cited_citizen_source_is_public() -> None:
    source = SourceTestFactory.build(
        source_type=SourceType.CITIZEN, owner_actor_id=IDS.new_id()
    )
    service = _read_side(source, InMemoryEventCitations(frozenset({source.id})))

    detail = await service.get_source(
        GetSource(actor=Actor.anonymous(), source_id=source.id)
    )

    assert detail.id == source.id


async def test_source_citation_checker_adapter_uncited_citizen_source_is_hidden() -> (
    None
):
    source = SourceTestFactory.build(
        source_type=SourceType.CITIZEN, owner_actor_id=IDS.new_id()
    )
    service = _read_side(source, InMemoryEventCitations(frozenset()))

    with pytest.raises(SourceNotFoundError):
        await service.get_source(
            GetSource(actor=Actor.anonymous(), source_id=source.id)
        )
