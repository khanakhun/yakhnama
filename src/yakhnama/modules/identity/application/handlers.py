"""Write-side use cases of the identity module.

Every handler except ``EnsureUserFromPrincipalHandler`` follows the same order, so
nothing is read or staged for a refused actor:

1. ask the policy about ``command.actor`` and raise ``PermissionDeniedError`` before
   opening a unit of work (deny by default);
2. inside the unit of work, reload the acting user and raise
   ``AccountSuspendedError`` if they were suspended after their actor was built,
   because an actor is a snapshot taken when the request started;
3. load the target, compare ``expected_version`` (raise ``PreconditionFailedError``,
   HTTP 412, on a mismatch), apply the domain change, stage it with its events and
   commit.

Identity's own rules are the identity domain policies, so these handlers build them
from the command (``CanManageOrganization(command.organization_id)``) instead of
receiving them from the composition root, as other modules' handlers do.

Patterns: Command Handler, Unit of Work, Policy, Domain Events.
"""

from collections.abc import Callable

from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from yakhnama.modules.identity.application.authorisation import (
    require_allowed,
    self_policy,
)
from yakhnama.modules.identity.application.commands import (
    AddMember,
    ChangeMemberRole,
    CreateOrganization,
    EnsureUserFromPrincipal,
    GrantRole,
    ReinstateUser,
    RemoveMember,
    RenameOrganization,
    RenameSelf,
    RevokeRole,
    SuspendUser,
)
from yakhnama.modules.identity.application.dto import (
    MemberSummary,
    OrganizationDetail,
    UserDetail,
)
from yakhnama.modules.identity.application.ports import (
    IdentityUnitOfWork,
    IdentityUnitOfWorkFactory,
)
from yakhnama.modules.identity.domain.entities import (
    Membership,
    Memberships,
    Organization,
    User,
)
from yakhnama.modules.identity.domain.errors import (
    AccountSuspendedError,
    OrganizationNotFoundError,
    OrganizationSlugTakenError,
    UserNotFoundError,
)
from yakhnama.modules.identity.domain.factories import (
    MembershipFactory,
    OrganizationFactory,
    UserFactory,
)
from yakhnama.modules.identity.domain.policies import (
    CanManageOrganization,
    IsAdmin,
    IsAuthenticated,
)
from yakhnama.modules.identity.domain.value_objects import (
    Actor,
    DisplayName,
    OrganizationRole,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import (
    ConflictError,
    PermissionDeniedError,
    PreconditionFailedError,
)
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import EntityId, IdGenerator

_DISPLAY_NAME: TypeAdapter[str] = TypeAdapter(DisplayName)

type UserChange = Callable[[User], AggregateChange[User]]
"""Applies one domain change to a loaded user."""


# --------------------------------------------------------------------------- #
# Shared steps                                                                #
# --------------------------------------------------------------------------- #


async def _load_acting_user(uow: IdentityUnitOfWork, actor: Actor) -> User:
    # Every policy used here refuses an anonymous actor already; treating one like
    # an actor without a record keeps a future permissive policy from reaching a
    # write without a user.
    user = None if actor.user_id is None else await uow.users.get(actor.user_id)
    if user is None:
        # Refused as forbidden, not as "not found", so an actor cannot probe for
        # user ids.
        message = "the actor has no user record"
        raise PermissionDeniedError(message, details={"reason": "unknown_actor"})
    if not user.is_active:
        raise AccountSuspendedError.for_id(user.id)
    return user


async def _load_user(uow: IdentityUnitOfWork, user_id: EntityId) -> User:
    user = await uow.users.get(user_id)
    if user is None:
        raise UserNotFoundError.for_id(user_id)
    return user


async def _load_organization(
    uow: IdentityUnitOfWork, organization_id: EntityId
) -> Organization:
    organization = await uow.organizations.get(organization_id)
    if organization is None:
        raise OrganizationNotFoundError.for_id(organization_id)
    return organization


def _check_version(expected: int | None, current: int) -> None:
    # The API turns If-Match into expected_version; comparing inside the unit of
    # work, after the load, closes the gap between the client's read and this write.
    if expected is not None and expected != current:
        message = "the record has changed since the client read it"
        raise PreconditionFailedError(
            message,
            details={"expected_version": expected, "current_version": current},
        )


async def _active_memberships(
    uow: IdentityUnitOfWork, user: User
) -> tuple[Membership, ...]:
    # Memberships of suspended or retired organisations grant nothing (Q-I7).
    kept: list[Membership] = []
    for membership in await uow.memberships.list_for_user(user.id):
        organization = await uow.organizations.get(membership.organization_id)
        if organization is not None and organization.is_active:
            kept.append(membership)
    return tuple(kept)


def _usable_display_name(claim: str | None) -> str | None:
    # A malformed claim (control characters, blank) is the provider's data, which
    # the caller cannot fix; it is dropped so sign-in still works (proposed).
    if claim is None:
        return None
    try:
        return _DISPLAY_NAME.validate_python(claim)
    except PydanticValidationError:
        return None


class _IdentityHandler:
    """Dependencies every identity command handler shares.

    Implements: Command Handler.
    """

    def __init__(
        self,
        uow_factory: IdentityUnitOfWorkFactory,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens an identity unit of work per call.
            clock: Source of timestamps and event times.
            ids: Source of aggregate and event ids.
        """
        self._uow_factory = uow_factory
        self._clock = clock
        self._ids = ids

    async def _change_user(
        self,
        actor: Actor,
        user_id: EntityId,
        expected_version: int | None,
        change: UserChange,
    ) -> UserDetail:
        async with self._uow_factory() as uow:
            await _load_acting_user(uow, actor)
            user = await _load_user(uow, user_id)
            _check_version(expected_version, user.version)
            result = change(user)
            if result.events:
                user = result.record_into(uow)
                await uow.users.save(user)
            await uow.commit()
        return UserDetail.from_entity(user)


# --------------------------------------------------------------------------- #
# Users                                                                       #
# --------------------------------------------------------------------------- #


class EnsureUserFromPrincipalHandler(_IdentityHandler):
    """Mirror a verified caller on first sight and return their actor.

    The first request of an ``(issuer, subject)`` creates the user with the mapped
    realm roles; later requests only record ``last_seen_at``: realm roles and the
    display name are not re-synchronised (Q-I1), so a role removed at the provider
    must also be revoked in Yakhnama. If a concurrent first request of the same
    user wins the insert, the resulting conflict is retried once as a lookup.

    Implements: Command Handler.
    """

    async def __call__(self, command: EnsureUserFromPrincipal) -> Actor:
        """Ensure the caller's mirror exists and build their actor.

        Args:
            command: The verified token's claims.

        Returns:
            The actor with the user's roles and memberships of active
            organisations.

        Raises:
            AccountSuspendedError: If the user is suspended; ``last_seen_at`` is
                still committed first, so the record shows the attempt.
            ConflictError: If the insert conflicts twice in a row.
        """
        try:
            user, memberships = await self._ensure(command)
        except ConflictError:
            user, memberships = await self._ensure(command)
        return user.to_actor(memberships)

    async def _ensure(
        self, command: EnsureUserFromPrincipal
    ) -> tuple[User, tuple[Membership, ...]]:
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_identity(command.identity)
            memberships: tuple[Membership, ...] = ()
            if user is None:
                change = UserFactory().mirror(
                    command.identity,
                    _usable_display_name(command.display_name),
                    command.realm_roles,
                    ids=self._ids,
                    clock=self._clock,
                )
                user = change.record_into(uow)
                await uow.users.add(user)
            else:
                touched = user.touch(clock=self._clock).state
                if touched.last_seen_at != user.last_seen_at:
                    await uow.users.touch(touched)
                user = touched
                memberships = await _active_memberships(uow, user)
            await uow.commit()
        return user, memberships


class GrantRoleHandler(_IdentityHandler):
    """Grant a platform role; platform administrators only.

    Implements: Command Handler.
    """

    async def __call__(self, command: GrantRole) -> UserDetail:
        """Grant ``command.role`` to the user.

        Args:
            command: The validated command.

        Returns:
            The user after the change (unchanged if the role was already held).

        Raises:
            PermissionDeniedError: If the actor is not a platform administrator.
            AccountSuspendedError: If the acting user has been suspended.
            UserNotFoundError: If the target user does not exist.
            PreconditionFailedError: If ``expected_version`` is stale.
            UserSuspendedError: If the target user is suspended.
        """
        require_allowed(IsAdmin(), command.actor, action="grant roles")
        return await self._change_user(
            command.actor,
            command.user_id,
            command.expected_version,
            lambda user: user.grant_role(
                command.role, clock=self._clock, ids=self._ids
            ),
        )


class RevokeRoleHandler(_IdentityHandler):
    """Revoke a platform role; platform administrators only.

    Implements: Command Handler.
    """

    async def __call__(self, command: RevokeRole) -> UserDetail:
        """Revoke ``command.role`` from the user.

        Args:
            command: The validated command.

        Returns:
            The user after the change.

        Raises:
            PermissionDeniedError: If the actor is not a platform administrator.
            AccountSuspendedError: If the acting user has been suspended.
            UserNotFoundError: If the target user does not exist.
            PreconditionFailedError: If ``expected_version`` is stale.
            UserSuspendedError: If the target user is suspended.
            CitizenRoleRequiredError: If the role is ``citizen``.
            RoleNotHeldError: If the user does not hold the role explicitly.
        """
        require_allowed(IsAdmin(), command.actor, action="revoke roles")
        return await self._change_user(
            command.actor,
            command.user_id,
            command.expected_version,
            lambda user: user.revoke_role(
                command.role, clock=self._clock, ids=self._ids
            ),
        )


class SuspendUserHandler(_IdentityHandler):
    """Suspend a user; platform administrators only.

    Implements: Command Handler.
    """

    async def __call__(self, command: SuspendUser) -> UserDetail:
        """Suspend the user with ``command.reason``.

        Args:
            command: The validated command.

        Returns:
            The suspended user.

        Raises:
            PermissionDeniedError: If the actor is not a platform administrator.
            AccountSuspendedError: If the acting user has been suspended.
            UserNotFoundError: If the target user does not exist.
            PreconditionFailedError: If ``expected_version`` is stale.
            UserSuspendedError: If the target user is already suspended.
        """
        require_allowed(IsAdmin(), command.actor, action="suspend users")
        return await self._change_user(
            command.actor,
            command.user_id,
            command.expected_version,
            lambda user: user.suspend(command.reason, clock=self._clock, ids=self._ids),
        )


class ReinstateUserHandler(_IdentityHandler):
    """Reinstate a suspended user; platform administrators only.

    Implements: Command Handler.
    """

    async def __call__(self, command: ReinstateUser) -> UserDetail:
        """Make the user active again.

        Args:
            command: The validated command.

        Returns:
            The reinstated user.

        Raises:
            PermissionDeniedError: If the actor is not a platform administrator.
            AccountSuspendedError: If the acting user has been suspended.
            UserNotFoundError: If the target user does not exist.
            PreconditionFailedError: If ``expected_version`` is stale.
            UserNotSuspendedError: If the target user is active.
        """
        require_allowed(IsAdmin(), command.actor, action="reinstate users")
        return await self._change_user(
            command.actor,
            command.user_id,
            command.expected_version,
            lambda user: user.reinstate(clock=self._clock, ids=self._ids),
        )


class RenameSelfHandler(_IdentityHandler):
    """Change the acting user's own display name.

    Implements: Command Handler.
    """

    async def __call__(self, command: RenameSelf) -> UserDetail:
        """Rename the acting user.

        Args:
            command: The validated command.

        Returns:
            The user after the change (unchanged if the name is the same).

        Raises:
            PermissionDeniedError: If the actor is anonymous or has no record.
            AccountSuspendedError: If the acting user has been suspended.
            PreconditionFailedError: If ``expected_version`` is stale.
        """
        actor = command.actor
        require_allowed(self_policy(actor), actor, action="rename themselves")
        async with self._uow_factory() as uow:
            user = await _load_acting_user(uow, actor)
            _check_version(command.expected_version, user.version)
            change = user.rename(command.display_name, clock=self._clock, ids=self._ids)
            if change.events:
                user = change.record_into(uow)
                await uow.users.save(user)
            await uow.commit()
        return UserDetail.from_entity(user)


# --------------------------------------------------------------------------- #
# Organisations                                                               #
# --------------------------------------------------------------------------- #


class CreateOrganizationHandler(_IdentityHandler):
    """Create an organisation; its creator becomes its first admin.

    Any authenticated, active user may create one (**proposed**, see the task
    report's open questions).

    Implements: Command Handler.
    """

    async def __call__(self, command: CreateOrganization) -> OrganizationDetail:
        """Create the organisation and the creator's admin membership.

        Args:
            command: The validated command.

        Returns:
            The new organisation, with a member count of one.

        Raises:
            PermissionDeniedError: If the actor is anonymous or has no record.
            AccountSuspendedError: If the acting user has been suspended.
            OrganizationSlugTakenError: If another organisation uses the slug.
        """
        require_allowed(IsAuthenticated(), command.actor, action="create organisations")
        async with self._uow_factory() as uow:
            creator = await _load_acting_user(uow, command.actor)
            if await uow.organizations.get_by_slug(command.slug) is not None:
                raise OrganizationSlugTakenError.for_slug(command.slug)
            organization = (
                OrganizationFactory()
                .create(
                    command.slug,
                    command.name,
                    command.organization_type,
                    ids=self._ids,
                    clock=self._clock,
                )
                .record_into(uow)
            )
            await uow.organizations.add(organization)
            membership = MembershipFactory().create(
                organization,
                creator,
                OrganizationRole.ADMIN,
                ids=self._ids,
                clock=self._clock,
            )
            members = (
                Memberships(organization_id=organization.id)
                .add(membership)
                .record_into(uow)
            )
            await uow.memberships.add(members.get(creator.id))
            await uow.commit()
        return OrganizationDetail.from_entity(organization, len(members.members))


class RenameOrganizationHandler(_IdentityHandler):
    """Rename an organisation; its admins and platform administrators only.

    Implements: Command Handler.
    """

    async def __call__(self, command: RenameOrganization) -> OrganizationDetail:
        """Rename the organisation.

        Args:
            command: The validated command.

        Returns:
            The organisation after the change (unchanged if the name is the same).

        Raises:
            PermissionDeniedError: If the actor may not manage the organisation.
            AccountSuspendedError: If the acting user has been suspended.
            OrganizationNotFoundError: If the organisation does not exist.
            PreconditionFailedError: If ``expected_version`` is stale.
            OrganizationNotActiveError: If it is suspended or retired.
        """
        require_allowed(
            CanManageOrganization(command.organization_id),
            command.actor,
            action="rename organisations",
        )
        async with self._uow_factory() as uow:
            await _load_acting_user(uow, command.actor)
            organization = await _load_organization(uow, command.organization_id)
            _check_version(command.expected_version, organization.version)
            change = organization.rename(command.name, clock=self._clock, ids=self._ids)
            if change.events:
                organization = change.record_into(uow)
                await uow.organizations.save(organization)
            members = await uow.memberships.list_for_organization(organization.id)
            await uow.commit()
        return OrganizationDetail.from_entity(organization, len(members.members))


class AddMemberHandler(_IdentityHandler):
    """Add a user to an organisation; its admins and platform administrators only.

    Implements: Command Handler.
    """

    async def __call__(self, command: AddMember) -> MemberSummary:
        """Add the user with ``command.role``.

        Args:
            command: The validated command.

        Returns:
            The new member.

        Raises:
            PermissionDeniedError: If the actor may not manage the organisation.
            AccountSuspendedError: If the acting user has been suspended.
            OrganizationNotFoundError: If the organisation does not exist.
            UserNotFoundError: If the user does not exist.
            OrganizationNotActiveError: If the organisation is not active.
            UserSuspendedError: If the user is suspended.
            DuplicateMembershipError: If the user is already a member.
        """
        require_allowed(
            CanManageOrganization(command.organization_id),
            command.actor,
            action="add organisation members",
        )
        async with self._uow_factory() as uow:
            await _load_acting_user(uow, command.actor)
            organization = await _load_organization(uow, command.organization_id)
            user = await _load_user(uow, command.user_id)
            members = await uow.memberships.list_for_organization(organization.id)
            change = MembershipFactory().create(
                organization, user, command.role, ids=self._ids, clock=self._clock
            )
            members = members.add(change).record_into(uow)
            membership = members.get(user.id)
            await uow.memberships.add(membership)
            await uow.commit()
        return MemberSummary.from_entities(membership, user)


class ChangeMemberRoleHandler(_IdentityHandler):
    """Change a member's role; its admins and platform administrators only.

    The last-admin rule (Q-I5) applies: the only admin cannot be demoted.

    Implements: Command Handler.
    """

    async def __call__(self, command: ChangeMemberRole) -> MemberSummary:
        """Change the member's role.

        Args:
            command: The validated command.

        Returns:
            The member after the change (unchanged if the role is the same).

        Raises:
            PermissionDeniedError: If the actor may not manage the organisation.
            AccountSuspendedError: If the acting user has been suspended.
            OrganizationNotFoundError: If the organisation does not exist.
            MembershipNotFoundError: If the user is not a member.
            PreconditionFailedError: If ``expected_version`` is stale.
            LastOrganizationAdminError: If this demotes the only admin.
            UserNotFoundError: If the member's user record is missing.
        """
        require_allowed(
            CanManageOrganization(command.organization_id),
            command.actor,
            action="change organisation member roles",
        )
        async with self._uow_factory() as uow:
            await _load_acting_user(uow, command.actor)
            organization = await _load_organization(uow, command.organization_id)
            members = await uow.memberships.list_for_organization(organization.id)
            _check_version(
                command.expected_version, members.get(command.user_id).version
            )
            change = members.change_role(
                command.user_id, command.role, clock=self._clock, ids=self._ids
            )
            membership = change.state.get(command.user_id)
            if change.events:
                change.record_into(uow)
                await uow.memberships.save(membership)
            user = await _load_user(uow, command.user_id)
            await uow.commit()
        return MemberSummary.from_entities(membership, user)


class RemoveMemberHandler(_IdentityHandler):
    """Remove a member; its admins and platform administrators only.

    The last-admin rule (Q-I5) applies: the only admin cannot be removed.

    Implements: Command Handler.
    """

    async def __call__(self, command: RemoveMember) -> None:
        """Remove the member.

        Args:
            command: The validated command.

        Raises:
            PermissionDeniedError: If the actor may not manage the organisation.
            AccountSuspendedError: If the acting user has been suspended.
            OrganizationNotFoundError: If the organisation does not exist.
            MembershipNotFoundError: If the user is not a member.
            PreconditionFailedError: If ``expected_version`` is stale.
            LastOrganizationAdminError: If this removes the only admin.
        """
        require_allowed(
            CanManageOrganization(command.organization_id),
            command.actor,
            action="remove organisation members",
        )
        async with self._uow_factory() as uow:
            await _load_acting_user(uow, command.actor)
            organization = await _load_organization(uow, command.organization_id)
            members = await uow.memberships.list_for_organization(organization.id)
            current = members.get(command.user_id)
            _check_version(command.expected_version, current.version)
            members.remove(
                command.user_id, clock=self._clock, ids=self._ids
            ).record_into(uow)
            await uow.memberships.remove(current.id)
            await uow.commit()
