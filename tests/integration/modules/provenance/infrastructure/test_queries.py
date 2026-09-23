"""The SQL source query service against real PostGIS."""

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories.provenance import SourceTestFactory
from yakhnama.modules.provenance.application.dto import SourceDetail
from yakhnama.modules.provenance.application.specifications import (
    SourceTypeSpecification,
)
from yakhnama.modules.provenance.domain.entities import Source
from yakhnama.modules.provenance.domain.value_objects import SourceType
from yakhnama.modules.provenance.infrastructure.queries import (
    SourceSpecificationCompiler,
    SqlAlchemySourceQueryService,
    decode_since,
)
from yakhnama.modules.provenance.infrastructure.uow import (
    SqlAlchemyProvenanceUnitOfWork,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.pagination import CursorPayload, PageRequest, encode_cursor
from yakhnama.shared_kernel.specification import (
    FalseSpecification,
    Specification,
    TrueSpecification,
)

pytestmark = pytest.mark.integration

type ProvenanceFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyProvenanceUnitOfWork]

START: Final = datetime(2026, 1, 1, tzinfo=UTC)
TYPES: Final = (
    SourceType.NEWS,
    SourceType.CITIZEN,
    SourceType.NEWS,
    SourceType.GOVERNMENT,
    SourceType.NEWS,
)


class _UnknownSpecification(Specification[Source]):
    """A leaf the SQL compiler has never heard of.

    Implements: Specification.
    """

    def is_satisfied_by(self, candidate: Source) -> bool:
        """Accept everything; only its type matters here.

        Args:
            candidate: Ignored.

        Returns:
            Always ``True``.
        """
        return True


@pytest.fixture
async def stored(provenance_uow_factory: ProvenanceFactory) -> list[Source]:
    """Store five sources one hour apart; the last two share a creation time."""
    instants = [START + timedelta(hours=index) for index in range(4)]
    instants.append(instants[-1])
    sources = [
        SourceTestFactory.build(source_type=kind, created_at=at, updated_at=at)
        for kind, at in zip(TYPES, instants, strict=True)
    ]
    async with provenance_uow_factory() as uow:
        for source in sources:
            await uow.sources.add(source)
        await uow.commit()
    return sources


def _newest_first(sources: list[Source]) -> list[Source]:
    return sorted(
        sources, key=lambda source: (source.created_at, source.id), reverse=True
    )


async def test_source_query_service_get_source_returns_detail(
    session_factory: async_sessionmaker[AsyncSession], stored: list[Source]
) -> None:
    service = SqlAlchemySourceQueryService(session_factory)

    detail = await service.get_source(stored[0].id)

    assert detail == SourceDetail.from_entity(stored[0])


async def test_source_query_service_get_unknown_source_returns_none(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = SqlAlchemySourceQueryService(session_factory)

    detail = await service.get_source(SourceTestFactory.build().id)

    assert detail is None


async def test_source_query_service_list_pages_newest_first_with_cursor(
    session_factory: async_sessionmaker[AsyncSession], stored: list[Source]
) -> None:
    service = SqlAlchemySourceQueryService(session_factory)
    expected = [source.id for source in _newest_first(stored)]

    first = await service.list_sources(TrueSpecification(), PageRequest(limit=2))
    second = await service.list_sources(
        TrueSpecification(), PageRequest(limit=2, cursor=first.next_cursor)
    )
    third = await service.list_sources(
        TrueSpecification(), PageRequest(limit=2, cursor=second.next_cursor)
    )

    listed = [item.id for page in (first, second, third) for item in page.items]
    assert listed == expected
    assert third.next_cursor is None


async def test_source_query_service_list_by_type_returns_only_that_type(
    session_factory: async_sessionmaker[AsyncSession], stored: list[Source]
) -> None:
    service = SqlAlchemySourceQueryService(session_factory)
    specification = SourceTypeSpecification(SourceType.NEWS)

    page = await service.list_sources(specification, PageRequest())

    expected = [
        source.id
        for source in _newest_first(stored)
        if specification.is_satisfied_by(source)
    ]
    assert [item.id for item in page.items] == expected


@pytest.mark.parametrize(
    "specification",
    [
        SourceTypeSpecification(SourceType.NEWS)
        | SourceTypeSpecification(SourceType.CITIZEN),
        ~SourceTypeSpecification(SourceType.NEWS),
        SourceTypeSpecification(SourceType.NEWS) & FalseSpecification(),
    ],
    ids=["or", "not", "and-false"],
)
async def test_source_query_service_list_combined_specification_matches_in_memory(
    session_factory: async_sessionmaker[AsyncSession],
    stored: list[Source],
    specification: Specification[Source],
) -> None:
    service = SqlAlchemySourceQueryService(session_factory)

    page = await service.list_sources(specification, PageRequest())

    expected = [
        source.id
        for source in _newest_first(stored)
        if specification.is_satisfied_by(source)
    ]
    assert [item.id for item in page.items] == expected


def test_source_specification_compiler_unknown_leaf_raises_type_error() -> None:
    compiler = SourceSpecificationCompiler()

    with pytest.raises(TypeError, match="_UnknownSpecification"):
        _UnknownSpecification().accept(compiler)


@pytest.mark.parametrize("sort_key", ["not a date", "2026-01-01T00:00:00"])
def test_source_decode_since_invalid_or_naive_raises_validation_error(
    sort_key: str,
) -> None:
    with pytest.raises(ValidationError):
        decode_since(sort_key)


async def test_source_query_service_list_with_bad_sort_key_raises_validation_error(
    session_factory: async_sessionmaker[AsyncSession], stored: list[Source]
) -> None:
    service = SqlAlchemySourceQueryService(session_factory)
    cursor = encode_cursor(CursorPayload(sort_key="yesterday", last_id=stored[0].id))

    with pytest.raises(ValidationError):
        await service.list_sources(TrueSpecification(), PageRequest(cursor=cursor))
