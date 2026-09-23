"""The SQL identity query service against real PostGIS, including member paging."""

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories.identity import (
    MembershipTestFactory,
    OrganizationTestFactory,
    UserTestFactory,
)
from yakhnama.modules.identity.application.dto import (
    MeDetail,
    MembershipSummary,
    MemberSummary,
    OrganizationDetail,
)
from yakhnama.modules.identity.application.queries import ListOrganizationMembers
from yakhnama.modules.identity.domain.entities import (
    Membership,
    Organization,
    User,
)
from yakhnama.modules.identity.domain.value_objects import (
    OrganizationRole,
    OrganizationStatus,
    Role,
)
from yakhnama.modules.identity.infrastructure.queries import (
    SqlAlchemyIdentityQueryService,
    decode_since,
)
from yakhnama.modules.identity.infrastructure.uow import SqlAlchemyIdentityUnitOfWork
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.pagination import (
    CursorPayload,
    Page,
    PageRequest,
    encode_cursor,
)

pytestmark = pytest.mark.integration

type IdentityFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyIdentityUnitOfWork]

CREATED: Final = datetime(2026, 9, 1, 12, 0, 0, 123456, tzinfo=UTC)


@pytest.fixture
def service(
    session_factory: async_sessionmaker[AsyncSession],
) -> SqlAlchemyIdentityQueryService:
    """Return the SQL query service on the test database."""
    return SqlAlchemyIdentityQueryService(session_factory)


def _user(**overrides: object) -> User:
    # model_validate rather than model_copy, so an override is validated like any
    # stored state would be.
    built = UserTestFactory.build(created_at=CREATED)
    return User.model_validate({**dict(built), **overrides})


def _organization(**overrides: object) -> Organization:
    built = OrganizationTestFactory.build(created_at=CREATED)
    return Organization.model_validate({**dict(built), **overrides})


def _membership(
    organization: Organization,
    user: User,
    *,
    created_at: datetime = CREATED,
    role: OrganizationRole = OrganizationRole.MEMBER,
) -> Membership:
    return MembershipTestFactory.build(
        organization_id=organization.id,
        user_id=user.id,
        created_at=created_at,
        role=role,
    )


async def _store(
    factory: IdentityFactory,
    *,
    users: tuple[User, ...] = (),
    organizations: tuple[Organization, ...] = (),
    memberships: tuple[Membership, ...] = (),
) -> None:
    async with factory() as uow:
        for user in users:
            await uow.users.add(user)
        for organization in organizations:
            await uow.organizations.add(organization)
        for membership in memberships:
            await uow.memberships.add(membership)
        await uow.commit()


async def _all_members(
    service: SqlAlchemyIdentityQueryService, organization: Organization, limit: int
) -> tuple[list[Page[MemberSummary]], list[MemberSummary]]:
    pages: list[Page[MemberSummary]] = []
    cursor: str | None = None
    while True:
        page = await service.list_members(
            ListOrganizationMembers(
                organization_id=organization.id,
                page=PageRequest(limit=limit, cursor=cursor),
            )
        )
        pages.append(page)
        if page.next_cursor is None:
            return pages, [item for each in pages for item in each.items]
        cursor = page.next_cursor


async def test_identity_query_service_get_me_unknown_user_returns_none(
    service: SqlAlchemyIdentityQueryService,
) -> None:
    found = await service.get_me(_user().id)

    assert found is None


async def test_identity_query_service_get_me_lists_active_memberships_by_slug(
    identity_uow_factory: IdentityFactory, service: SqlAlchemyIdentityQueryService
) -> None:
    user = _user(roles=frozenset({Role.CITIZEN, Role.TRUSTED_REPORTER}))
    zulu = _organization(slug="zulu-test-org")
    alpha = _organization(slug="alpha-test-org")
    suspended = _organization(
        slug="middle-test-org",
        status=OrganizationStatus.SUSPENDED,
        status_reason="Test suspension",
    )
    retired = _organization(
        slug="retired-test-org",
        status=OrganizationStatus.RETIRED,
        status_reason="Test retirement",
    )
    await _store(
        identity_uow_factory,
        users=(user,),
        organizations=(zulu, alpha, suspended, retired),
        memberships=(
            _membership(zulu, user, role=OrganizationRole.ADMIN),
            _membership(alpha, user),
            _membership(suspended, user),
            _membership(retired, user),
        ),
    )

    me = await service.get_me(user.id)

    assert me == MeDetail(
        id=user.id,
        display_name=user.display_name,
        roles=user.roles,
        memberships=(
            MembershipSummary(
                organization_id=alpha.id,
                slug=alpha.slug,
                role=OrganizationRole.MEMBER,
            ),
            MembershipSummary(
                organization_id=zulu.id, slug=zulu.slug, role=OrganizationRole.ADMIN
            ),
        ),
        version=user.version,
    )


async def test_identity_query_service_get_organization_unknown_returns_none(
    service: SqlAlchemyIdentityQueryService,
) -> None:
    found = await service.get_organization(_organization().id)

    assert found is None


async def test_identity_query_service_get_organization_counts_only_its_members(
    identity_uow_factory: IdentityFactory, service: SqlAlchemyIdentityQueryService
) -> None:
    organization, empty, other = _organization(), _organization(), _organization()
    first, second = _user(), _user()
    await _store(
        identity_uow_factory,
        users=(first, second),
        organizations=(organization, empty, other),
        memberships=(
            _membership(organization, first),
            _membership(organization, second, role=OrganizationRole.ADMIN),
            _membership(other, first),
        ),
    )

    detail = await service.get_organization(organization.id)
    empty_detail = await service.get_organization(empty.id)

    assert detail == OrganizationDetail.from_entity(organization, 2)
    assert empty_detail == OrganizationDetail.from_entity(empty, 0)


async def test_identity_query_service_list_members_pages_through_ties_in_order(
    identity_uow_factory: IdentityFactory, service: SqlAlchemyIdentityQueryService
) -> None:
    organization, other = _organization(), _organization()
    users = tuple(_user() for _ in range(5))
    later = CREATED + timedelta(microseconds=1)
    # Three members share one created_at, so only the user id orders them.
    memberships = (
        _membership(organization, users[0], created_at=later),
        _membership(organization, users[1]),
        _membership(organization, users[2], created_at=later),
        _membership(organization, users[3], created_at=later),
        _membership(other, users[4]),
    )
    await _store(
        identity_uow_factory,
        users=users,
        organizations=(organization, other),
        memberships=memberships,
    )
    tied = sorted((users[0], users[2], users[3]), key=lambda user: user.id)
    expected_order = [users[1].id, *(user.id for user in tied)]

    pages, members = await _all_members(service, organization, limit=1)
    _, members_in_pairs = await _all_members(service, organization, limit=2)

    assert [member.user_id for member in members] == expected_order
    assert [member.user_id for member in members_in_pairs] == expected_order
    assert len(pages) == 4
    assert members[0] == MemberSummary.from_entities(memberships[1], users[1])


async def test_identity_query_service_list_members_exact_page_has_no_next_cursor(
    identity_uow_factory: IdentityFactory, service: SqlAlchemyIdentityQueryService
) -> None:
    organization, user = _organization(), _user()
    await _store(
        identity_uow_factory,
        users=(user,),
        organizations=(organization,),
        memberships=(_membership(organization, user),),
    )

    page = await service.list_members(
        ListOrganizationMembers(
            organization_id=organization.id, page=PageRequest(limit=1)
        )
    )

    assert len(page.items) == 1
    assert page.next_cursor is None


async def test_identity_query_service_list_members_unknown_organization_is_empty(
    service: SqlAlchemyIdentityQueryService,
) -> None:
    page = await service.list_members(
        ListOrganizationMembers(organization_id=_organization().id)
    )

    assert page == Page[MemberSummary](items=(), next_cursor=None)


@pytest.mark.parametrize(
    "sort_key", ["not-an-instant", "2026-09-01T12:00:00"], ids=["garbage", "naive"]
)
async def test_identity_query_service_list_members_bad_cursor_raises_validation_error(
    service: SqlAlchemyIdentityQueryService, sort_key: str
) -> None:
    cursor = encode_cursor(CursorPayload(sort_key=sort_key, last_id=_user().id))

    with pytest.raises(ValidationError):
        await service.list_members(
            ListOrganizationMembers(
                organization_id=_organization().id,
                page=PageRequest(cursor=cursor),
            )
        )


def test_decode_since_with_offset_returns_aware_instant() -> None:
    decoded = decode_since(CREATED.isoformat())

    assert decoded == CREATED
