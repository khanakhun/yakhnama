"""Factories for the ``identity`` domain: users, organisations, memberships, actors.

The classes are suffixed ``TestFactory`` because the domain already has
``UserFactory``, ``OrganizationFactory`` and ``MembershipFactory``
(``yakhnama.modules.identity.domain.factories``); one name for two different things
would make every test that uses both ambiguous. These factories build models
directly, bypassing the domain factories, so they are for arranging state, not for
testing creation rules.

Display names and organisation names are placeholders (``"Test user <n>"``), never
real people or organisations.

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
from yakhnama.modules.identity.domain.entities import Membership, Organization, User
from yakhnama.modules.identity.domain.value_objects import (
    Actor,
    OrganizationRole,
    OrganizationStatus,
    OrganizationType,
    Role,
    UserStatus,
)

TEST_ISSUER = "https://auth.example.test/realms/yakhnama"
"""Issuer of every factory-built user; ``example.test`` is reserved (RFC 2606)."""


def _same_as_created_at(_name: str, values: Mapping[str, object]) -> object:
    # A record at version 1 has not changed since it was created.
    return values["created_at"]


def _citizen_only() -> frozenset[Role]:
    return frozenset({Role.CITIZEN})


def _no_memberships() -> frozenset[tuple[UUID, OrganizationRole]]:
    return frozenset()


class UserTestFactory(YakhnamaModelFactory[User]):
    """Builds active citizens at version 1, just mirrored.

    ``updated_at`` and ``last_seen_at`` equal ``created_at``. Pass ``roles=`` to add
    roles; keep ``Role.CITIZEN`` in them for an active user.

    Implements: Factory.
    """

    __model__ = User

    id = Use(FACTORY_IDS.new_id)
    subject = sequence("test-subject-{:05d}")
    issuer = TEST_ISSUER
    display_name = sequence("Test user {}")
    roles = Use(_citizen_only)
    status = UserStatus.ACTIVE
    status_reason = None
    version = 1
    created_at = Use(random_instant)
    updated_at = PostGenerated(_same_as_created_at)
    last_seen_at = PostGenerated(_same_as_created_at)


class OrganizationTestFactory(YakhnamaModelFactory[Organization]):
    """Builds active organisations of a random type at version 1.

    Implements: Factory.
    """

    __model__ = Organization

    id = Use(FACTORY_IDS.new_id)
    slug = sequence("test-org-{:05d}")
    name = sequence("Test organisation {}")
    organization_type = pick(list(OrganizationType))
    status = OrganizationStatus.ACTIVE
    status_reason = None
    version = 1
    created_at = Use(random_instant)
    updated_at = PostGenerated(_same_as_created_at)


class MembershipTestFactory(YakhnamaModelFactory[Membership]):
    """Builds ``member`` memberships at version 1 of fresh organisation and user ids.

    Pass ``organization_id=`` and ``user_id=`` to attach them to built records.

    Implements: Factory.
    """

    __model__ = Membership

    id = Use(FACTORY_IDS.new_id)
    organization_id = Use(FACTORY_IDS.new_id)
    user_id = Use(FACTORY_IDS.new_id)
    role = OrganizationRole.MEMBER
    version = 1
    created_at = Use(random_instant)
    updated_at = PostGenerated(_same_as_created_at)


class ActorTestFactory(YakhnamaModelFactory[Actor]):
    """Builds authenticated citizens without memberships.

    Use ``Actor.anonymous()`` for an anonymous caller.

    Implements: Factory.
    """

    __model__ = Actor

    user_id = Use(FACTORY_IDS.new_id)
    roles = Use(_citizen_only)
    memberships = Use(_no_memberships)
