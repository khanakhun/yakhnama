"""Unit tests for the authorised provenance query service and its queries."""

from datetime import UTC, datetime, timedelta

import pytest

from tests.factories.provenance import SourceTestFactory
from tests.fakes.identity import actor_with
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.provenance import (
    FakeSourceCitationChecker,
    InMemoryProvenanceUnitOfWork,
    InMemorySourceQueryService,
)
from yakhnama.modules.identity.public import Actor, OrganizationRole, Role
from yakhnama.modules.provenance.application.queries import GetSource, ListSources
from yakhnama.modules.provenance.application.query_services import (
    AuthorisedSourceQueryService,
)
from yakhnama.modules.provenance.application.specifications import (
    SourceTypeSpecification,
)
from yakhnama.modules.provenance.domain.entities import Source
from yakhnama.modules.provenance.domain.errors import SourceNotFoundError
from yakhnama.modules.provenance.domain.value_objects import SourceType
from yakhnama.shared_kernel.pagination import PageRequest
from yakhnama.shared_kernel.specification import TrueSpecification

START = datetime(2026, 2, 1, tzinfo=UTC)
IDS = SequentialIdGenerator(seed=302)
MISSING_ID = IDS.new_id()
OWNER_ID = IDS.new_id()
ORGANIZATION_ID = IDS.new_id()
MODERATOR = actor_with({Role.MODERATOR})
OWNER = actor_with(user_id=OWNER_ID)


def sources() -> list[Source]:
    """Return three government sources and one citizen source, a day apart."""
    types = [
        SourceType.GOVERNMENT,
        SourceType.CITIZEN,
        SourceType.GOVERNMENT,
        SourceType.GOVERNMENT,
    ]
    return [
        SourceTestFactory.build(
            source_type=source_type,
            owner_actor_id=OWNER_ID,
            created_at=START + timedelta(days=index),
            updated_at=START + timedelta(days=index),
        )
        for index, source_type in enumerate(types)
    ]


def service(
    *stored: Source, checker: FakeSourceCitationChecker | None = None
) -> AuthorisedSourceQueryService:
    """Return the authorised service over a fake holding ``stored``."""
    uow = InMemoryProvenanceUnitOfWork(sources=stored)
    return AuthorisedSourceQueryService(InMemorySourceQueryService(uow), checker)


def private_source(source_type: SourceType = SourceType.CITIZEN) -> Source:
    """Return a citizen (or organisation) source owned by ``OWNER_ID``."""
    return SourceTestFactory.build(
        source_type=source_type,
        owner_actor_id=OWNER_ID,
        organization_id=(
            ORGANIZATION_ID if source_type is SourceType.ORGANISATION else None
        ),
    )


# --------------------------------------------------------------------------- #
# GetSource                                                                   #
# --------------------------------------------------------------------------- #


async def test_get_source_ranked_type_anonymous_returns_detail() -> None:
    source = sources()[0]

    result = await service(source).get_source(
        GetSource(actor=Actor.anonymous(), source_id=source.id)
    )

    assert result.id == source.id
    assert result.citation == source.citation


@pytest.mark.parametrize("source_type", [SourceType.CITIZEN, SourceType.ORGANISATION])
@pytest.mark.parametrize("actor", [Actor.anonymous(), OWNER, actor_with()])
async def test_get_source_uncited_private_source_is_told_it_does_not_exist(
    source_type: SourceType, actor: Actor
) -> None:
    source = private_source(source_type)
    checker = FakeSourceCitationChecker()

    with pytest.raises(SourceNotFoundError):
        await service(source, checker=checker).get_source(
            GetSource(actor=actor, source_id=source.id)
        )

    assert checker.asked == [source.id]


async def test_get_source_private_source_without_checker_fails_closed() -> None:
    source = private_source()

    with pytest.raises(SourceNotFoundError):
        await service(source).get_source(
            GetSource(actor=Actor.anonymous(), source_id=source.id)
        )


async def test_get_source_private_source_cited_by_public_event_is_readable() -> None:
    source = private_source()
    checker = FakeSourceCitationChecker({source.id})

    result = await service(source, checker=checker).get_source(
        GetSource(actor=Actor.anonymous(), source_id=source.id)
    )

    assert result.id == source.id


async def test_get_source_private_source_moderator_reads_without_citation() -> None:
    source = private_source()
    checker = FakeSourceCitationChecker()

    result = await service(source, checker=checker).get_source(
        GetSource(actor=MODERATOR, source_id=source.id)
    )

    assert result.id == source.id
    assert checker.asked == []


async def test_get_source_organisation_source_member_reads_it() -> None:
    source = private_source(SourceType.ORGANISATION)
    member = actor_with(memberships={(ORGANIZATION_ID, OrganizationRole.MEMBER)})

    result = await service(source).get_source(
        GetSource(actor=member, source_id=source.id)
    )

    assert result.organization_id == ORGANIZATION_ID


async def test_get_source_missing_raises_not_found() -> None:
    with pytest.raises(SourceNotFoundError):
        await service().get_source(GetSource(actor=MODERATOR, source_id=MISSING_ID))


# --------------------------------------------------------------------------- #
# ListSources                                                                 #
# --------------------------------------------------------------------------- #


async def test_list_sources_anonymous_hides_citizen_sources() -> None:
    stored = sources()

    page = await service(*stored).list_sources(ListSources(actor=Actor.anonymous()))

    assert [item.id for item in page.items] == [
        stored[3].id,
        stored[2].id,
        stored[0].id,
    ]


async def test_list_sources_owner_does_not_list_own_citizen_source() -> None:
    stored = sources()

    page = await service(*stored).list_sources(
        ListSources(actor=OWNER, source_type=SourceType.CITIZEN)
    )

    assert page.items == ()


async def test_list_sources_moderator_pages_every_source() -> None:
    stored = sources()
    query_service = service(*stored)

    first = await query_service.list_sources(
        ListSources(actor=MODERATOR, page=PageRequest(limit=3))
    )
    second = await query_service.list_sources(
        ListSources(
            actor=MODERATOR, page=PageRequest(limit=3, cursor=first.next_cursor)
        )
    )

    assert [item.id for item in first.items] == [s.id for s in reversed(stored[1:])]
    assert [item.id for item in second.items] == [stored[0].id]
    assert second.next_cursor is None


async def test_list_sources_with_type_returns_matching_newest_first() -> None:
    stored = sources()

    page = await service(*stored).list_sources(
        ListSources(actor=Actor.anonymous(), source_type=SourceType.GOVERNMENT)
    )

    assert [item.id for item in page.items] == [
        stored[3].id,
        stored[2].id,
        stored[0].id,
    ]
    assert page.next_cursor is None


def test_list_sources_without_type_builds_true_specification() -> None:
    query = ListSources(actor=Actor.anonymous())

    specification = query.to_specification()

    assert isinstance(specification, TrueSpecification)


def test_list_sources_with_type_builds_type_specification() -> None:
    query = ListSources(actor=Actor.anonymous(), source_type=SourceType.NEWS)

    specification = query.to_specification()

    assert isinstance(specification, SourceTypeSpecification)
    assert specification.source_type is SourceType.NEWS


def test_source_type_specification_other_type_is_not_satisfied() -> None:
    specification = SourceTypeSpecification(SourceType.NEWS)
    source = SourceTestFactory.build(source_type=SourceType.CITIZEN)

    result = specification.is_satisfied_by(source)

    assert result is False
