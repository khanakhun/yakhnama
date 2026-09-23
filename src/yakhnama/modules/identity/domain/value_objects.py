"""Value objects of the ``identity`` bounded context.

Roles and their implication order, organisation roles, types and statuses, the
identifiers mirrored from an OpenID Connect provider (``Subject``, ``Issuer``), the
optional ``DisplayName`` and the ``Actor`` every authorisation policy is evaluated
against.

Identity stores **no personal data beyond an optional display name**: no email, phone
number, legal name or address is ever modelled here (Phase 2 plan, Q4). Several
choices below are **proposed defaults, not domain facts** (the role implication
order, the organisation types, the loopback exception for issuers); each is marked
where it is defined and listed in ``docs/data-dictionary/identity.md``.

Patterns: Value Object.
"""

import ipaddress
import unicodedata
from collections.abc import Iterable
from enum import StrEnum
from typing import Annotated, Final, Self
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from yakhnama.shared_kernel.ids import EntityId

# --------------------------------------------------------------------------- #
# Roles                                                                       #
# --------------------------------------------------------------------------- #


class Role(StrEnum):
    """A platform-wide role held by a user.

    The roles form a **proposed** partial order by implication (Phase 2 plan, T3):
    ``admin`` implies ``moderator`` and ``org_admin`` implies ``org_member``; nothing
    else is implied. In particular ``admin`` does not imply ``trusted_reporter`` or
    ``org_admin``, and no role implies ``citizen`` because every active user holds
    ``citizen`` explicitly. The order is reflexive (every role implies itself),
    antisymmetric and transitive.

    ``org_member`` and ``org_admin`` are realm-level markers only. Rights inside one
    organisation always come from a ``Membership`` and its ``OrganizationRole``, never
    from these roles (**proposed**, see the data dictionary).

    Implements: Value Object.
    """

    CITIZEN = "citizen"
    TRUSTED_REPORTER = "trusted_reporter"
    ORG_MEMBER = "org_member"
    ORG_ADMIN = "org_admin"
    MODERATOR = "moderator"
    ADMIN = "admin"

    @property
    def implied_roles(self) -> frozenset["Role"]:
        """Return every role this role implies, itself included.

        Returns:
            The reflexive, transitive closure of the implication order from this role.
        """
        return _ROLE_CLOSURE[self]

    def implies(self, other: "Role") -> bool:
        """Tell whether holding this role also grants ``other``.

        Args:
            other: The role being asked for.

        Returns:
            ``True`` if ``other`` is this role or is implied by it.
        """
        return other in _ROLE_CLOSURE[self]


# Direct implications only; the closure below adds reflexivity and transitivity so the
# table stays the single, reviewable statement of the (proposed) order.
_DIRECT_IMPLICATIONS: Final[dict[Role, frozenset[Role]]] = {
    Role.ADMIN: frozenset({Role.MODERATOR}),
    Role.ORG_ADMIN: frozenset({Role.ORG_MEMBER}),
}


def _closure(role: Role) -> frozenset[Role]:
    # Recursion terminates because the order is acyclic (antisymmetry is tested).
    return frozenset({role}).union(
        *(_closure(implied) for implied in _DIRECT_IMPLICATIONS.get(role, ()))
    )


_ROLE_CLOSURE: Final[dict[Role, frozenset[Role]]] = {
    role: _closure(role) for role in Role
}


def expand_roles(roles: Iterable[Role]) -> frozenset[Role]:
    """Return ``roles`` together with every role they imply.

    Args:
        roles: Roles held explicitly.

    Returns:
        The effective roles under the implication order.
    """
    effective: set[Role] = set()
    for role in roles:
        effective |= _ROLE_CLOSURE[role]
    return frozenset(effective)


class OrganizationRole(StrEnum):
    """A user's role inside one organisation.

    ``admin`` implies ``member``: an organisation admin is always a member.

    Implements: Value Object.
    """

    MEMBER = "member"
    ADMIN = "admin"


class OrganizationType(StrEnum):
    """Kind of organisation (**proposed** list, Phase 2 plan Q3).

    Implements: Value Object.
    """

    GOVERNMENT = "government"
    NGO = "ngo"
    RESEARCH = "research"
    MEDIA = "media"
    COMMUNITY = "community"
    OTHER = "other"


class UserStatus(StrEnum):
    """Lifecycle of a user mirror: ``active`` and the reversible ``suspended``.

    Implements: Value Object.
    """

    ACTIVE = "active"
    SUSPENDED = "suspended"


class OrganizationStatus(StrEnum):
    """Lifecycle of an organisation; ``suspended`` is reversible, ``retired`` final.

    Implements: Value Object.
    """

    ACTIVE = "active"
    SUSPENDED = "suspended"
    RETIRED = "retired"


# --------------------------------------------------------------------------- #
# Constrained strings                                                         #
# --------------------------------------------------------------------------- #

ORGANIZATION_SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{1,63}$"

OrganizationSlug = Annotated[str, StringConstraints(pattern=ORGANIZATION_SLUG_PATTERN)]
"""URL-safe organisation handle: 2 to 64 lower-case ASCII letters, digits or ``-``,
starting with a letter or digit. Unique among organisations. The ASCII-only pattern
already excludes every control, surrogate and bidirectional formatting character."""

SUBJECT_MAX_LENGTH = 255
# OpenID Connect Core 1.0 §2: "sub" is at most 255 ASCII characters. Printable ASCII
# only, so a NUL or control character can never reach a log line or a database key.
SUBJECT_PATTERN = r"^[\x20-\x7e]{1,255}$"

Subject = Annotated[str, StringConstraints(pattern=SUBJECT_PATTERN)]
"""The OIDC ``sub`` claim: opaque, case-sensitive, never reassigned by the issuer.

It is not stripped or case-folded, because two subjects that differ only in case or
spacing are different identities (OIDC Core §2).
"""

ISSUER_MAX_LENGTH = 512
# Loopback hosts may use plain http so the local development realm works; this is a
# proposed exception, and the platform's production settings validator is where https
# is enforced for the configured issuer.
_LOOPBACK_HOST_NAMES: Final = frozenset({"localhost"})


def _is_loopback(host: str) -> bool:
    if host in _LOOPBACK_HOST_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _check_issuer(value: str) -> str:
    # ValueError, not the kernel's ValidationError: Pydantic only collects ValueError.
    if any(not "\x21" <= character <= "\x7e" for character in value):
        message = "issuer must be printable ASCII without spaces"
        raise ValueError(message)
    parts = urlsplit(value)
    host = parts.hostname or ""
    if not host or "@" in parts.netloc:
        message = "issuer must be a URL with a host and no user information"
        raise ValueError(message)
    try:
        # Accessing port validates it (digits, 0-65535); the value itself is unused.
        _ = parts.port
    except ValueError as error:
        message = "issuer has an invalid port"
        raise ValueError(message) from error
    if "?" in value or "#" in value:
        # OpenID Connect Discovery 1.0 §3: the issuer has no query or fragment.
        message = "issuer must not contain a query or fragment"
        raise ValueError(message)
    if parts.scheme == "https" or (parts.scheme == "http" and _is_loopback(host)):
        return value
    message = "issuer must use https (plain http is accepted only for loopback hosts)"
    raise ValueError(message)


Issuer = Annotated[
    str,
    StringConstraints(min_length=1, max_length=ISSUER_MAX_LENGTH),
    AfterValidator(_check_issuer),
]
"""The OIDC ``iss`` claim: an ``https`` URL with a host, no query and no fragment.

Compared exactly, as OIDC requires. ``http`` is accepted only for loopback hosts
(``localhost``, ``127.0.0.0/8``, ``::1``) so the development realm works
(**proposed**).
"""

DISPLAY_NAME_MAX_LENGTH = 120


# Unicode categories refused in free text: control characters (``Cc``, NUL
# included), which are unsafe in logs and exports, and lone surrogates (``Cs``), which
# cannot be encoded as UTF-8 and would fail only later, at serialisation.
FORBIDDEN_TEXT_CATEGORIES: Final = frozenset({"Cc", "Cs"})

# Bidirectional embeddings, overrides (U+202A-U+202E) and isolates (U+2066-U+2069)
# reorder the text around them, so a name could display differently from how it
# is stored and compared ("Trojan Source", CVE-2021-42574).
BIDI_CONTROL_CHARACTERS: Final = frozenset(
    chr(code_point) for code_point in (*range(0x202A, 0x202F), *range(0x2066, 0x206A))
)


def _is_forbidden_character(character: str) -> bool:
    return (
        unicodedata.category(character) in FORBIDDEN_TEXT_CATEGORIES
        or character in BIDI_CONTROL_CHARACTERS
    )


def _normalise_free_text(value: str) -> str:
    # NFC so a name typed with combining characters equals its precomposed form. NFC
    # never turns an allowed character into a forbidden one and keeps lone
    # surrogates as they are, so checking after it (and after stripping, which
    # keeps surrounding whitespace such as a tab acceptable) misses nothing.
    normalised = unicodedata.normalize("NFC", value).strip()
    if any(_is_forbidden_character(character) for character in normalised):
        message = (
            "text must not contain control, surrogate or bidirectional "
            "formatting characters"
        )
        raise ValueError(message)
    return normalised


DisplayName = Annotated[
    str,
    AfterValidator(_normalise_free_text),
    StringConstraints(min_length=1, max_length=DISPLAY_NAME_MAX_LENGTH),
]
"""The user's chosen display name, 1 to 120 characters after NFC and stripping.

The only personal data the identity module stores. It is optional, editable by the
user, never copied into domain events and never logged.
"""

ORGANIZATION_NAME_MAX_LENGTH = 200

OrganizationName = Annotated[
    str,
    AfterValidator(_normalise_free_text),
    StringConstraints(min_length=1, max_length=ORGANIZATION_NAME_MAX_LENGTH),
]
"""Display name of an organisation, 1 to 200 characters after NFC and stripping."""

REASON_MAX_LENGTH = 500

StatusReason = Annotated[
    str,
    AfterValidator(_normalise_free_text),
    StringConstraints(min_length=1, max_length=REASON_MAX_LENGTH),
]
"""Why a user or organisation was suspended or retired, 1 to 500 characters.

Kept on the aggregate, not in events, because a moderator's free text can mention a
person.
"""

RECORD_VERSION_MAX = 2_147_483_647
RecordVersion = Annotated[int, Field(ge=1, le=RECORD_VERSION_MAX)]
"""Optimistic-concurrency version: 1 when created, +1 per change with an effect.

The upper bound is the largest signed 32-bit integer, so it fits a PostgreSQL
``integer`` column.
"""

# --------------------------------------------------------------------------- #
# References and the actor                                                    #
# --------------------------------------------------------------------------- #


class ExternalIdentity(BaseModel):
    """The identity of a user at an OpenID Connect provider: ``(issuer, subject)``.

    Unique among users and never changed once mirrored. Both parts are compared
    exactly, as OpenID Connect requires.

    Implements: Value Object.

    Attributes:
        issuer: The ``iss`` claim.
        subject: The ``sub`` claim.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    issuer: Issuer
    subject: Subject


class MembershipRef(BaseModel):
    """The natural key of a membership: one user in one organisation.

    Implements: Value Object.

    Attributes:
        organization_id: The organisation.
        user_id: The member.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    organization_id: EntityId
    user_id: EntityId


class Actor(BaseModel):
    """Who is acting, as every authorisation policy sees it.

    An actor is either anonymous (``user_id`` is ``None``, no roles, no memberships)
    or an authenticated user with the roles they hold explicitly and their
    organisation memberships. ``roles`` are stored as held; ``has_role`` applies the
    implication order of ``Role``. At most one role is recorded per organisation,
    mirroring the one-membership-per-organisation invariant.

    Implements: Value Object.

    Attributes:
        user_id: The user's id, or ``None`` for an anonymous caller.
        roles: Platform roles held explicitly.
        memberships: ``(organization_id, role)`` pairs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: EntityId | None = None
    roles: frozenset[Role] = Field(default=frozenset(), max_length=len(Role))
    memberships: frozenset[tuple[EntityId, OrganizationRole]] = frozenset()

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.user_id is None and (self.roles or self.memberships):
            message = "an anonymous actor has no roles and no memberships"
            raise ValueError(message)
        organization_ids = [organization_id for organization_id, _ in self.memberships]
        if len(organization_ids) != len(set(organization_ids)):
            message = "an actor holds at most one role per organisation"
            raise ValueError(message)
        return self

    @classmethod
    def anonymous(cls) -> Self:
        """Return the actor for a caller without a valid token.

        Returns:
            An actor with no user id, roles or memberships.
        """
        return cls()

    @property
    def is_authenticated(self) -> bool:
        """Tell whether the actor is a known user.

        Returns:
            ``True`` if ``user_id`` is set.
        """
        return self.user_id is not None

    @property
    def effective_roles(self) -> frozenset[Role]:
        """Return the held roles plus every role they imply.

        Returns:
            The roles ``has_role`` answers ``True`` for.
        """
        return expand_roles(self.roles)

    def has_role(self, role: Role) -> bool:
        """Tell whether the actor holds ``role`` directly or through implication.

        Args:
            role: The role being asked for.

        Returns:
            ``True`` if some held role implies ``role``.
        """
        return any(held.implies(role) for held in self.roles)

    def organization_role(self, organization_id: EntityId) -> OrganizationRole | None:
        """Return the actor's role in one organisation.

        Args:
            organization_id: The organisation.

        Returns:
            The role, or ``None`` if the actor is not a member.
        """
        return next(
            (
                role
                for member_of, role in self.memberships
                if member_of == organization_id
            ),
            None,
        )

    def is_member_of(self, organization_id: EntityId) -> bool:
        """Tell whether the actor belongs to an organisation in any role.

        Args:
            organization_id: The organisation.

        Returns:
            ``True`` for a member or an admin of that organisation.
        """
        return self.organization_role(organization_id) is not None

    def is_org_admin_of(self, organization_id: EntityId) -> bool:
        """Tell whether the actor administers an organisation.

        Args:
            organization_id: The organisation.

        Returns:
            ``True`` if the actor's membership there has the ``admin`` role.
        """
        return self.organization_role(organization_id) is OrganizationRole.ADMIN
