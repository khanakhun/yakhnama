"""Authorisation policies: composable yes/no rules over an ``Actor`` (ADR 0005).

Every policy is a kernel ``Specification[Actor]`` with an ``is_allowed`` alias, and
composes with ``&``, ``|`` and ``~`` (or ``and_``, ``or_``, ``not_``) into ``AllOf``,
``AnyOf`` and ``Not``, which are policies themselves. Any policy therefore satisfies
the ``AuthorisationPolicy`` protocol other modules type against through the identity
facade.

**Deny by default.** A policy allows only what its rule explicitly matches. The
anonymous actor holds no roles and no memberships, so every rule except
``CanReadVerifiedData`` refuses it. Composition helpers need at least one operand, so
an empty ``all_of()`` can never allow everything by accident.

Patterns: Policy, Specification.
"""

from typing import Protocol

from yakhnama.modules.identity.domain.value_objects import Actor, Role
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.specification import (
    AndSpecification,
    NotSpecification,
    OrSpecification,
    Specification,
)


class AuthorisationPolicy(Protocol):
    """What every module needs from a policy: an allow/deny answer for an actor.

    Implements: Policy (port side).
    """

    def is_allowed(self, actor: Actor) -> bool:
        """Tell whether ``actor`` may perform the guarded action.

        Args:
            actor: Who is acting.

        Returns:
            ``True`` only if the policy explicitly allows the actor.
        """
        ...


class ActorPolicy(Specification[Actor]):
    """Base of every identity policy; composition keeps the policy type.

    Subclasses implement ``is_satisfied_by``.

    Implements: Policy, Specification.
    """

    def is_allowed(self, actor: Actor) -> bool:
        """Tell whether ``actor`` may perform the guarded action.

        Args:
            actor: Who is acting.

        Returns:
            ``is_satisfied_by(actor)``.
        """
        return self.is_satisfied_by(actor)

    def and_(self, other: Specification[Actor]) -> "AllOf":
        """Return a policy that allows only when both policies allow.

        Args:
            other: The right-hand policy.

        Returns:
            ``self AND other``.
        """
        return AllOf(self, other)

    def or_(self, other: Specification[Actor]) -> "AnyOf":
        """Return a policy that allows when either policy allows.

        Args:
            other: The right-hand policy.

        Returns:
            ``self OR other``.
        """
        return AnyOf(self, other)

    def not_(self) -> "Not":
        """Return a policy that allows exactly when this one refuses.

        Returns:
            ``NOT self``.
        """
        return Not(self)

    def __and__(self, other: Specification[Actor]) -> "AllOf":
        """Operator form of ``and_``.

        Args:
            other: The right-hand policy.

        Returns:
            ``self AND other``.
        """
        return self.and_(other)

    def __or__(self, other: Specification[Actor]) -> "AnyOf":
        """Operator form of ``or_``.

        Args:
            other: The right-hand policy.

        Returns:
            ``self OR other``.
        """
        return self.or_(other)

    def __invert__(self) -> "Not":
        """Operator form of ``not_``.

        Returns:
            ``NOT self``.
        """
        return self.not_()


# ActorPolicy comes first so its composition methods (returning policies) win over the
# kernel's; evaluation and visitor dispatch come from the kernel composite.
class AllOf(ActorPolicy, AndSpecification[Actor]):
    """Allows only when both operands allow.

    Implements: Policy, Specification.
    """


class AnyOf(ActorPolicy, OrSpecification[Actor]):
    """Allows when at least one operand allows.

    Implements: Policy, Specification.
    """


class Not(ActorPolicy, NotSpecification[Actor]):
    """Allows exactly when the operand refuses.

    Implements: Policy, Specification.
    """


def all_of(first: ActorPolicy, *rest: ActorPolicy) -> ActorPolicy:
    """Fold policies into one that allows only when every policy allows.

    Args:
        first: The first policy; at least one is required.
        *rest: Further policies.

    Returns:
        The conjunction, or ``first`` alone.
    """
    combined = first
    for policy in rest:
        combined = combined & policy
    return combined


def any_of(first: ActorPolicy, *rest: ActorPolicy) -> ActorPolicy:
    """Fold policies into one that allows when any policy allows.

    Args:
        first: The first policy; at least one is required.
        *rest: Further policies.

    Returns:
        The disjunction, or ``first`` alone.
    """
    combined = first
    for policy in rest:
        combined = combined | policy
    return combined


# --------------------------------------------------------------------------- #
# Leaf policies                                                               #
# --------------------------------------------------------------------------- #


class IsAuthenticated(ActorPolicy):
    """Allows any known user.

    Suspended users never get an actor (``User.to_actor``), so this does not let
    them through.

    Implements: Policy.
    """

    def is_satisfied_by(self, candidate: Actor) -> bool:
        """Tell whether the actor is authenticated.

        Args:
            candidate: Who is acting.

        Returns:
            ``candidate.is_authenticated``.
        """
        return candidate.is_authenticated


class HasRole(ActorPolicy):
    """Allows actors holding a role, directly or through the implication order.

    Implements: Policy.

    Attributes:
        role: The required role.
    """

    def __init__(self, role: Role) -> None:
        """Create the policy.

        Args:
            role: The required role.
        """
        self._role = role

    @property
    def role(self) -> Role:
        """Return the required role."""
        return self._role

    def is_satisfied_by(self, candidate: Actor) -> bool:
        """Tell whether the actor holds the role.

        Args:
            candidate: Who is acting.

        Returns:
            ``candidate.has_role(role)``.
        """
        return candidate.has_role(self._role)


class IsAdmin(HasRole):
    """Allows platform administrators.

    Implements: Policy.
    """

    def __init__(self) -> None:
        """Create the policy for ``Role.ADMIN``."""
        super().__init__(Role.ADMIN)


class IsModerator(HasRole):
    """Allows moderators, and administrators through the implication order.

    Implements: Policy.
    """

    def __init__(self) -> None:
        """Create the policy for ``Role.MODERATOR``."""
        super().__init__(Role.MODERATOR)


class IsMemberOf(ActorPolicy):
    """Allows members and admins of one organisation.

    Implements: Policy.

    Attributes:
        organization_id: The organisation.
    """

    def __init__(self, organization_id: EntityId) -> None:
        """Create the policy.

        Args:
            organization_id: The organisation.
        """
        self._organization_id = organization_id

    @property
    def organization_id(self) -> EntityId:
        """Return the organisation."""
        return self._organization_id

    def is_satisfied_by(self, candidate: Actor) -> bool:
        """Tell whether the actor belongs to the organisation.

        Args:
            candidate: Who is acting.

        Returns:
            ``True`` for a member or an admin of the organisation.
        """
        return candidate.is_member_of(self._organization_id)


class IsOrgAdminOf(ActorPolicy):
    """Allows admins of one organisation.

    Implements: Policy.

    Attributes:
        organization_id: The organisation.
    """

    def __init__(self, organization_id: EntityId) -> None:
        """Create the policy.

        Args:
            organization_id: The organisation.
        """
        self._organization_id = organization_id

    @property
    def organization_id(self) -> EntityId:
        """Return the organisation."""
        return self._organization_id

    def is_satisfied_by(self, candidate: Actor) -> bool:
        """Tell whether the actor administers the organisation.

        Args:
            candidate: Who is acting.

        Returns:
            ``True`` for an admin of the organisation.
        """
        return candidate.is_org_admin_of(self._organization_id)


class IsSelf(ActorPolicy):
    """Allows the actor who is the given user, for "my own record" actions.

    Implements: Policy.

    Attributes:
        user_id: The user whose record is being acted on.
    """

    def __init__(self, user_id: EntityId) -> None:
        """Create the policy.

        Args:
            user_id: The user whose record is being acted on.
        """
        self._user_id = user_id

    @property
    def user_id(self) -> EntityId:
        """Return the user whose record is being acted on."""
        return self._user_id

    def is_satisfied_by(self, candidate: Actor) -> bool:
        """Tell whether the actor is that user.

        Args:
            candidate: Who is acting.

        Returns:
            ``True`` if the actor's user id equals ``user_id``; never for an
            anonymous actor.
        """
        return candidate.user_id is not None and candidate.user_id == self._user_id


# --------------------------------------------------------------------------- #
# Use-case policies                                                           #
# --------------------------------------------------------------------------- #


class _DelegatingPolicy(ActorPolicy):
    """A named policy defined by a composed rule, so the rule is written once.

    Implements: Policy.
    """

    def __init__(self, rule: ActorPolicy) -> None:
        self._rule = rule

    def is_satisfied_by(self, candidate: Actor) -> bool:
        """Evaluate the composed rule.

        Args:
            candidate: Who is acting.

        Returns:
            The rule's answer.
        """
        return self._rule.is_satisfied_by(candidate)


class CanManageReferenceData(_DelegatingPolicy):
    """Allows changes to places, hazard types and impact metrics: admins only.

    Implements: Policy.
    """

    def __init__(self) -> None:
        """Create the policy: ``IsAdmin()``."""
        super().__init__(IsAdmin())


class CanModerate(_DelegatingPolicy):
    """Allows moderation work: moderators, and admins through role implication.

    Implements: Policy.
    """

    def __init__(self) -> None:
        """Create the policy: ``IsModerator() | IsAdmin()``.

        ``IsAdmin`` is redundant under the current implication order and is kept so
        the rule still reads "moderator or admin" if the order ever changes.
        """
        super().__init__(IsModerator() | IsAdmin())


class CanManageOrganization(_DelegatingPolicy):
    """Allows managing one organisation: platform admins or that organisation's admins.

    Implements: Policy.

    Attributes:
        organization_id: The organisation.
    """

    def __init__(self, organization_id: EntityId) -> None:
        """Create the policy: ``IsAdmin() | IsOrgAdminOf(organization_id)``.

        Args:
            organization_id: The organisation.
        """
        super().__init__(IsAdmin() | IsOrgAdminOf(organization_id))
        self._organization_id = organization_id

    @property
    def organization_id(self) -> EntityId:
        """Return the organisation."""
        return self._organization_id


class CanReadVerifiedData(ActorPolicy):
    """Allows everyone, anonymous callers included, to read verified data.

    The one deliberate "allow all": the open dataset is public by mission
    (``AGENTS.md`` §1) and anonymous reads stay allowed under OIDC-only
    authentication (open question Q10). Unverified data needs another policy.

    Implements: Policy.
    """

    def is_satisfied_by(self, candidate: Actor) -> bool:
        """Allow any actor.

        Args:
            candidate: Ignored.

        Returns:
            Always ``True``.
        """
        return True
