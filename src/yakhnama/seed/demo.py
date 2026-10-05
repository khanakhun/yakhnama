"""The development demo accounts and organisation the seed ensures (ADR 0019).

Two accounts exist in the development realm (``docker/keycloak/yakhnama-realm.json``)
besides ``demo-citizen`` and ``demo-moderator``, so assisted reporting can be tried
and tested end to end:

- ``demo-trusted-reporter`` (``citizen``, ``trusted_reporter``);
- ``demo-org-member`` (``citizen``, ``org_member``), a member of the demo
  organisation below.

The realm gives both a fixed user id, which Keycloak puts in the token's ``sub``,
so the seed can mirror them under the realm's issuer before their first sign-in and
attach the membership; first sight then finds the seeded user (realm roles are only
copied at first sight, Q50). Nothing here is real: the organisation's name says so,
and none of it is seeded in production.

Patterns: Composition Root (development data bound for the seed).
"""

from typing import Final
from uuid import UUID

from yakhnama.modules.identity.public import (
    ExternalIdentity,
    OrganizationRole,
    OrganizationType,
    Role,
    SeedAccount,
    SeedOrganization,
)

DEMO_TRUSTED_REPORTER_SUBJECT: Final = "7e0a1c52-3b8d-4f6e-9a21-5c0de0000001"
"""Keycloak user id (token ``sub``) of ``demo-trusted-reporter`` in the dev realm."""

DEMO_ORG_MEMBER_SUBJECT: Final = "7e0a1c52-3b8d-4f6e-9a21-5c0de0000002"
"""Keycloak user id (token ``sub``) of ``demo-org-member`` in the dev realm."""

DEMO_ORGANIZATION_ID: Final = UUID("0199b2a0-0000-7000-8000-0000000000de")
"""Fixed UUIDv7 of the demo organisation, so tests and the portal can name it."""

DEMO_ORGANIZATION: Final = SeedOrganization(
    organization_id=DEMO_ORGANIZATION_ID,
    slug="demo-organisation-dev",
    name="Demo organisation (development only, not real)",
    organization_type=OrganizationType.OTHER,
)


def demo_accounts(issuer: str) -> tuple[SeedAccount, ...]:
    """Return the demo accounts as the realm at ``issuer`` identifies them.

    Args:
        issuer: The development realm's issuer, exactly as in its tokens.

    Returns:
        The trusted reporter and the organisation member.
    """
    return (
        SeedAccount(
            identity=ExternalIdentity(
                issuer=issuer, subject=DEMO_TRUSTED_REPORTER_SUBJECT
            ),
            roles=frozenset({Role.CITIZEN, Role.TRUSTED_REPORTER}),
        ),
        SeedAccount(
            identity=ExternalIdentity(issuer=issuer, subject=DEMO_ORG_MEMBER_SUBJECT),
            roles=frozenset({Role.CITIZEN, Role.ORG_MEMBER}),
            organization_role=OrganizationRole.MEMBER,
        ),
    )
