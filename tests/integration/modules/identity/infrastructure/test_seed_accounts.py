"""``SeedAccountsHandler`` over the SQLAlchemy identity unit of work and PostGIS."""

import pytest

from tests.fakes.clock import SteppingClock
from tests.fakes.identity import actor_with
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.identity.infrastructure.uow import SqlAlchemyIdentityUnitOfWork
from yakhnama.modules.identity.public import (
    EnsureUserFromPrincipal,
    EnsureUserFromPrincipalHandler,
    ExternalIdentity,
    Role,
    SeedAccounts,
    SeedAccountsHandler,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.seed.demo import (
    DEMO_ORG_MEMBER_SUBJECT,
    DEMO_ORGANIZATION,
    DEMO_ORGANIZATION_ID,
    demo_accounts,
)

pytestmark = pytest.mark.integration

ISSUER = "http://127.0.0.1:18080/realms/yakhnama"


async def test_seed_accounts_twice_then_first_sign_in_sees_the_membership(
    identity_uow_factory: SqlAlchemyUnitOfWorkFactory[SqlAlchemyIdentityUnitOfWork],
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    handler = SeedAccountsHandler(identity_uow_factory, clock, ids)
    command = SeedAccounts(
        actor=actor_with({Role.ADMIN}),
        organization=DEMO_ORGANIZATION,
        accounts=demo_accounts(ISSUER),
    )

    first = await handler(command)
    second = await handler(command)
    actor = await EnsureUserFromPrincipalHandler(identity_uow_factory, clock, ids)(
        EnsureUserFromPrincipal(
            identity=ExternalIdentity(issuer=ISSUER, subject=DEMO_ORG_MEMBER_SUBJECT),
            realm_roles=frozenset({Role.CITIZEN, Role.ORG_MEMBER}),
        )
    )

    assert first.users_created == 2
    assert first.memberships_created == 1
    assert second.is_unchanged is True
    assert actor.has_role(Role.ORG_MEMBER)
    assert actor.is_member_of(DEMO_ORGANIZATION_ID)
