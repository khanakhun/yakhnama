"""Aggregates of the ``identity`` bounded context: users, organisations, memberships.

Every model is frozen. A state-changing method validates the new state, bumps
``version`` by one, stamps ``updated_at`` from the injected ``Clock`` and returns an
``AggregateChange`` holding the new instance and its events; the caller's instance is
never modified. A change that is already in effect (granting a role the user holds,
renaming to the same name) returns the instance unchanged with no events, so a retried
command is harmless.

The one exception is ``User.touch``: recording when a user was last seen is
bookkeeping, not a change of the user, so it bumps neither ``version`` nor
``updated_at`` and emits no event. Otherwise every authenticated request would move
the user's ``ETag`` and make every ``If-Match`` on the user fail.

A user is a *mirror* of an identity at an OpenID Connect provider, keyed by
``(issuer, subject)``. It stores no personal data beyond an optional display name.

Patterns: Entity, Aggregate Root, Domain Events.
"""

from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from yakhnama.modules.identity.domain.errors import (
    AccountSuspendedError,
    CitizenRoleRequiredError,
    DuplicateMembershipError,
    LastOrganizationAdminError,
    MembershipNotFoundError,
    OrganizationNotActiveError,
    OrganizationNotSuspendedError,
    RoleNotHeldError,
    UserNotSuspendedError,
    UserSuspendedError,
)
from yakhnama.modules.identity.domain.events import (
    MembershipRemoved,
    MembershipRoleChanged,
    OrganizationEvent,
    OrganizationReinstated,
    OrganizationRenamed,
    OrganizationRetired,
    OrganizationSuspended,
    UserReinstated,
    UserRenamed,
    UserRoleGranted,
    UserRoleRevoked,
    UserSuspended,
)
from yakhnama.modules.identity.domain.value_objects import (
    Actor,
    DisplayName,
    ExternalIdentity,
    Issuer,
    MembershipRef,
    OrganizationName,
    OrganizationRole,
    OrganizationSlug,
    OrganizationStatus,
    OrganizationType,
    RecordVersion,
    Role,
    StatusReason,
    Subject,
    UserStatus,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import InvariantViolationError
from yakhnama.shared_kernel.events import AggregateChange, DomainEvent
from yakhnama.shared_kernel.ids import EntityId, IdGenerator

_DISPLAY_NAME: TypeAdapter[str] = TypeAdapter(DisplayName)
_ORGANIZATION_NAME: TypeAdapter[str] = TypeAdapter(OrganizationName)
_STATUS_REASON: TypeAdapter[str] = TypeAdapter(StatusReason)


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _evolve[ModelT: BaseModel](
    model: ModelT, now: datetime, version: int, **updates: object
) -> ModelT:
    # model_validate, not model_copy: model_copy skips validation and would let a
    # change break an invariant.
    fields = {name: getattr(model, name) for name in type(model).model_fields}
    return model.model_validate(
        {**fields, **updates, "version": version + 1, "updated_at": now}
    )


# --------------------------------------------------------------------------- #
# User                                                                        #
# --------------------------------------------------------------------------- #


class User(BaseModel):
    """A person known to Yakhnama through an OpenID Connect provider.

    Invariants, checked on every construction:

    - ``(issuer, subject)`` is the user's external identity; no method changes it;
    - an active user always holds ``citizen``;
    - ``status_reason`` is set exactly when ``status`` is ``suspended``;
    - ``updated_at`` and ``last_seen_at`` are never before ``created_at``.

    Implements: Entity / Aggregate Root.

    Attributes:
        id: Stable identity (UUIDv7).
        subject: The OIDC ``sub`` claim.
        issuer: The OIDC ``iss`` claim.
        display_name: Optional display name chosen by the user; the only personal
            data stored.
        roles: Platform roles held explicitly (implied roles are not stored).
        status: ``active`` or ``suspended``.
        status_reason: Why the user was suspended; ``None`` while active.
        version: Optimistic-concurrency version, 1 at creation, +1 per change.
        created_at: When the user was first seen, UTC.
        updated_at: When the user last changed, UTC.
        last_seen_at: When the user last made an authenticated request, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    subject: Subject
    issuer: Issuer
    display_name: DisplayName | None = None
    roles: frozenset[Role] = Field(max_length=len(Role))
    status: UserStatus = UserStatus.ACTIVE
    status_reason: StatusReason | None = None
    version: RecordVersion = 1
    created_at: AwareDatetime
    updated_at: AwareDatetime
    last_seen_at: AwareDatetime

    _normalise_to_utc = field_validator(
        "created_at", "updated_at", "last_seen_at", mode="after"
    )(_to_utc)

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        if self.is_active and Role.CITIZEN not in self.roles:
            message = "an active user always holds the citizen role"
            raise ValueError(message)
        if (self.status_reason is not None) != (self.status is UserStatus.SUSPENDED):
            message = "status_reason must be set exactly when the user is suspended"
            raise ValueError(message)
        if self.updated_at < self.created_at or self.last_seen_at < self.created_at:
            message = "updated_at and last_seen_at must not be earlier than created_at"
            raise ValueError(message)
        return self

    # ----------------------------------------------------------------------- #
    # Queries                                                                 #
    # ----------------------------------------------------------------------- #

    @property
    def is_active(self) -> bool:
        """Tell whether the user may act.

        Returns:
            ``True`` while ``status`` is ``active``.
        """
        return self.status is UserStatus.ACTIVE

    @property
    def external_identity(self) -> ExternalIdentity:
        """Return the user's identity at the provider.

        Returns:
            The ``(issuer, subject)`` pair.
        """
        return ExternalIdentity(issuer=self.issuer, subject=self.subject)

    def to_actor(self, memberships: Iterable["Membership"] = ()) -> Actor:
        """Return the actor every policy evaluates for a request by this user.

        The caller passes the memberships of organisations that are active; this
        method cannot see an organisation's status.

        Args:
            memberships: The user's memberships.

        Returns:
            An authenticated actor with the user's roles and memberships.

        Raises:
            AccountSuspendedError: If the user is suspended; a suspended user never
                gets an actor, so no policy can allow them anything.
            InvariantViolationError: If a membership belongs to another user.
        """
        if not self.is_active:
            raise AccountSuspendedError.for_id(self.id)
        pairs: set[tuple[EntityId, OrganizationRole]] = set()
        for membership in memberships:
            if membership.user_id != self.id:
                message = f"membership {membership.id} belongs to another user"
                raise InvariantViolationError(
                    message, details={"membership_id": str(membership.id)}
                )
            pairs.add((membership.organization_id, membership.role))
        return Actor(user_id=self.id, roles=self.roles, memberships=frozenset(pairs))

    # ----------------------------------------------------------------------- #
    # Changes                                                                 #
    # ----------------------------------------------------------------------- #

    def touch(self, *, clock: Clock) -> AggregateChange["User"]:
        """Record that the user was just seen.

        Neither ``version`` nor ``updated_at`` moves and no event is emitted (see
        the module docstring). ``last_seen_at`` never moves backwards, so a clock
        behind the stored value leaves the user unchanged. Suspended users are
        touched too, so the record shows they are still trying to sign in.

        Args:
            clock: Source of ``last_seen_at``.

        Returns:
            The user with the new ``last_seen_at``, and no events.
        """
        now = clock.now()
        if now <= self.last_seen_at:
            return AggregateChange[User](state=self)
        fields = {name: getattr(self, name) for name in type(self).model_fields}
        state = self.model_validate({**fields, "last_seen_at": now})
        return AggregateChange[User](state=state)

    def rename(
        self, display_name: str | None, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["User"]:
        """Set, change or clear the display name.

        Args:
            display_name: The new name, or ``None`` to clear it.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of event ids.

        Returns:
            The renamed user and ``UserRenamed``, or the unchanged user and no
            events if the name is the same after normalisation.

        Raises:
            UserSuspendedError: If the user is suspended.
            pydantic.ValidationError: If the name is empty or too long after
                normalisation, or contains a control character.
        """
        self._require_active()
        normalised = (
            None
            if display_name is None
            else _DISPLAY_NAME.validate_python(display_name)
        )
        if normalised == self.display_name:
            return AggregateChange[User](state=self)
        now = clock.now()
        state = _evolve(self, now, self.version, display_name=normalised)
        event = state._event(
            UserRenamed, ids, now, has_display_name=normalised is not None
        )
        return AggregateChange[User](state=state, events=(event,))

    def grant_role(
        self, role: Role, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["User"]:
        """Grant a platform role explicitly.

        A role that is only implied (``moderator`` for an ``admin``) is still
        stored, so it survives a later revocation of the implying role.

        Args:
            role: The role to grant.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of event ids.

        Returns:
            The user and ``UserRoleGranted``, or the unchanged user and no events if
            the role is already held explicitly.

        Raises:
            UserSuspendedError: If the user is suspended.
        """
        self._require_active()
        if role in self.roles:
            return AggregateChange[User](state=self)
        now = clock.now()
        state = _evolve(self, now, self.version, roles=self.roles | {role})
        event = state._event(UserRoleGranted, ids, now, role=role)
        return AggregateChange[User](state=state, events=(event,))

    def revoke_role(
        self, role: Role, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["User"]:
        """Revoke a platform role held explicitly.

        Args:
            role: The role to revoke.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of event ids.

        Returns:
            The user and ``UserRoleRevoked``.

        Raises:
            UserSuspendedError: If the user is suspended.
            CitizenRoleRequiredError: If ``role`` is ``citizen``.
            RoleNotHeldError: If the user does not hold ``role`` explicitly (an
                implied role cannot be revoked on its own).
        """
        self._require_active()
        if role is Role.CITIZEN:
            message = "the citizen role cannot be revoked; suspend the user instead"
            raise CitizenRoleRequiredError(message, details={"user_id": str(self.id)})
        if role not in self.roles:
            message = f"user {self.id} does not hold the role {role.value!r}"
            raise RoleNotHeldError(
                message, details={"user_id": str(self.id), "role": role.value}
            )
        now = clock.now()
        state = _evolve(self, now, self.version, roles=self.roles - {role})
        event = state._event(UserRoleRevoked, ids, now, role=role)
        return AggregateChange[User](state=state, events=(event,))

    def suspend(
        self, reason: str, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["User"]:
        """Suspend the user: they keep their roles but can no longer act.

        Args:
            reason: Why, 1 to 500 characters after normalisation; kept on the user,
                never in the event.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of event ids.

        Returns:
            The suspended user and ``UserSuspended``.

        Raises:
            UserSuspendedError: If the user is already suspended.
            pydantic.ValidationError: If ``reason`` is empty or too long.
        """
        self._require_active()
        normalised = _STATUS_REASON.validate_python(reason)
        now = clock.now()
        state = _evolve(
            self,
            now,
            self.version,
            status=UserStatus.SUSPENDED,
            status_reason=normalised,
        )
        return AggregateChange[User](
            state=state, events=(state._event(UserSuspended, ids, now),)
        )

    def reinstate(self, *, clock: Clock, ids: IdGenerator) -> AggregateChange["User"]:
        """Make a suspended user active again, with the roles they held.

        ``citizen`` is restored if it is missing, so the active-user invariant
        holds even for a record suspended before it was introduced.

        Args:
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of event ids.

        Returns:
            The active user and ``UserReinstated``.

        Raises:
            UserNotSuspendedError: If the user is already active.
        """
        if self.is_active:
            raise UserNotSuspendedError.for_id(self.id)
        now = clock.now()
        state = _evolve(
            self,
            now,
            self.version,
            status=UserStatus.ACTIVE,
            status_reason=None,
            roles=self.roles | {Role.CITIZEN},
        )
        return AggregateChange[User](
            state=state, events=(state._event(UserReinstated, ids, now),)
        )

    # ----------------------------------------------------------------------- #
    # Internals                                                               #
    # ----------------------------------------------------------------------- #

    def _require_active(self) -> None:
        if not self.is_active:
            raise UserSuspendedError.for_id(self.id)

    def _event[EventT: DomainEvent](
        self,
        event_class: type[EventT],
        ids: IdGenerator,
        now: datetime,
        **fields: object,
    ) -> EventT:
        return event_class.model_validate(
            {
                "event_id": ids.new_id(),
                "occurred_at": now,
                "aggregate_id": self.id,
                "version": self.version,
                **fields,
            }
        )


# --------------------------------------------------------------------------- #
# Organisation                                                                #
# --------------------------------------------------------------------------- #


class Organization(BaseModel):
    """A group whose members report and work together, such as an NGO or agency.

    Invariants: ``status_reason`` is set exactly when ``status`` is not ``active``;
    ``updated_at`` is never before ``created_at``. Transitions: ``active`` ↔
    ``suspended``; ``active`` or ``suspended`` → ``retired``, which is final. An
    organisation is never deleted.

    Implements: Entity / Aggregate Root.

    Attributes:
        id: Stable identity (UUIDv7).
        slug: URL-safe handle, unique among organisations.
        name: Display name, 1 to 200 characters.
        organization_type: The kind of organisation.
        status: ``active``, ``suspended`` or ``retired``.
        status_reason: Why it was suspended or retired; ``None`` while active.
        version: Optimistic-concurrency version, 1 at creation, +1 per change.
        created_at: When the organisation was created, UTC.
        updated_at: When the organisation last changed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    slug: OrganizationSlug
    name: OrganizationName
    organization_type: OrganizationType
    status: OrganizationStatus = OrganizationStatus.ACTIVE
    status_reason: StatusReason | None = None
    version: RecordVersion = 1
    created_at: AwareDatetime
    updated_at: AwareDatetime

    _normalise_to_utc = field_validator("created_at", "updated_at", mode="after")(
        _to_utc
    )

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        if (self.status_reason is not None) != (not self.is_active):
            message = "status_reason must be set exactly when status is not 'active'"
            raise ValueError(message)
        if self.updated_at < self.created_at:
            message = "updated_at must not be earlier than created_at"
            raise ValueError(message)
        return self

    @property
    def is_active(self) -> bool:
        """Tell whether the organisation is active.

        Returns:
            ``True`` while ``status`` is ``active``.
        """
        return self.status is OrganizationStatus.ACTIVE

    def rename(
        self, name: str, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["Organization"]:
        """Change the display name.

        Args:
            name: The new name, 1 to 200 characters after normalisation.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of event ids.

        Returns:
            The renamed organisation and ``OrganizationRenamed``, or the unchanged
            organisation and no events if the name is the same.

        Raises:
            OrganizationNotActiveError: If the organisation is suspended or retired.
            pydantic.ValidationError: If ``name`` is empty or too long.
        """
        self._require_active()
        normalised = _ORGANIZATION_NAME.validate_python(name)
        if normalised == self.name:
            return AggregateChange[Organization](state=self)
        now = clock.now()
        state = _evolve(self, now, self.version, name=normalised)
        event = state._event(OrganizationRenamed, ids, now)
        return AggregateChange[Organization](state=state, events=(event,))

    def suspend(
        self, reason: str, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["Organization"]:
        """Suspend the organisation until it is reinstated.

        Args:
            reason: Why, 1 to 500 characters; kept on the aggregate only.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of event ids.

        Returns:
            The suspended organisation and ``OrganizationSuspended``.

        Raises:
            OrganizationNotActiveError: If it is already suspended or retired.
            pydantic.ValidationError: If ``reason`` is empty or too long.
        """
        self._require_active()
        return self._move_to(OrganizationStatus.SUSPENDED, reason, clock=clock, ids=ids)

    def reinstate(
        self, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["Organization"]:
        """Make a suspended organisation active again.

        Args:
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of event ids.

        Returns:
            The active organisation and ``OrganizationReinstated``.

        Raises:
            OrganizationNotSuspendedError: If it is active or retired.
        """
        if self.status is not OrganizationStatus.SUSPENDED:
            raise OrganizationNotSuspendedError.for_status(self.id, self.status.value)
        return self._move_to(OrganizationStatus.ACTIVE, None, clock=clock, ids=ids)

    def retire(
        self, reason: str, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["Organization"]:
        """Retire the organisation for good; it is kept, never deleted.

        Args:
            reason: Why, 1 to 500 characters; kept on the aggregate only.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of event ids.

        Returns:
            The retired organisation and ``OrganizationRetired``.

        Raises:
            OrganizationNotActiveError: If it is already retired.
            pydantic.ValidationError: If ``reason`` is empty or too long.
        """
        if self.status is OrganizationStatus.RETIRED:
            raise OrganizationNotActiveError.for_status(self.id, self.status.value)
        return self._move_to(OrganizationStatus.RETIRED, reason, clock=clock, ids=ids)

    def _move_to(
        self,
        status: OrganizationStatus,
        reason: str | None,
        *,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange["Organization"]:
        normalised = None if reason is None else _STATUS_REASON.validate_python(reason)
        now = clock.now()
        state = _evolve(
            self, now, self.version, status=status, status_reason=normalised
        )
        event_class = _ORGANIZATION_STATUS_EVENTS[status]
        return AggregateChange[Organization](
            state=state, events=(state._event(event_class, ids, now),)
        )

    def _require_active(self) -> None:
        if not self.is_active:
            raise OrganizationNotActiveError.for_status(self.id, self.status.value)

    def _event[EventT: DomainEvent](
        self,
        event_class: type[EventT],
        ids: IdGenerator,
        now: datetime,
        **fields: object,
    ) -> EventT:
        return event_class.model_validate(
            {
                "event_id": ids.new_id(),
                "occurred_at": now,
                "aggregate_id": self.id,
                "slug": self.slug,
                "version": self.version,
                **fields,
            }
        )


_ORGANIZATION_STATUS_EVENTS: dict[OrganizationStatus, type[OrganizationEvent]] = {
    OrganizationStatus.ACTIVE: OrganizationReinstated,
    OrganizationStatus.SUSPENDED: OrganizationSuspended,
    OrganizationStatus.RETIRED: OrganizationRetired,
}

# --------------------------------------------------------------------------- #
# Memberships                                                                 #
# --------------------------------------------------------------------------- #


class Membership(BaseModel):
    """One user's membership of one organisation.

    Created by ``MembershipFactory``, added and changed through ``Memberships``, which
    enforces the invariants that span every membership of an organisation.

    Implements: Entity.

    Attributes:
        id: Stable identity (UUIDv7).
        organization_id: The organisation.
        user_id: The member.
        role: ``member`` or ``admin`` of the organisation.
        version: Optimistic-concurrency version, 1 at creation, +1 per change.
        created_at: When the user joined, UTC.
        updated_at: When the membership last changed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    organization_id: EntityId
    user_id: EntityId
    role: OrganizationRole = OrganizationRole.MEMBER
    version: RecordVersion = 1
    created_at: AwareDatetime
    updated_at: AwareDatetime

    _normalise_to_utc = field_validator("created_at", "updated_at", mode="after")(
        _to_utc
    )

    @model_validator(mode="after")
    def _check_timestamps(self) -> Self:
        if self.updated_at < self.created_at:
            message = "updated_at must not be earlier than created_at"
            raise ValueError(message)
        return self

    @property
    def ref(self) -> MembershipRef:
        """Return the membership's natural key.

        Returns:
            The ``(organization_id, user_id)`` pair.
        """
        return MembershipRef(organization_id=self.organization_id, user_id=self.user_id)

    @property
    def is_admin(self) -> bool:
        """Tell whether the member administers the organisation.

        Returns:
            ``True`` for the ``admin`` role.
        """
        return self.role is OrganizationRole.ADMIN

    def change_role(
        self, role: OrganizationRole, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["Membership"]:
        """Change the member's role, without the organisation-wide checks.

        Use ``Memberships.change_role`` from handlers; it applies the last-admin
        rule before calling this method.

        Args:
            role: The new role.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of event ids.

        Returns:
            The membership and ``MembershipRoleChanged``, or the unchanged
            membership and no events if the role is the same.
        """
        if role is self.role:
            return AggregateChange[Membership](state=self)
        now = clock.now()
        state = _evolve(self, now, self.version, role=role)
        event = MembershipRoleChanged(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=state.id,
            organization_id=state.organization_id,
            user_id=state.user_id,
            role=role,
            previous_role=self.role,
            version=state.version,
        )
        return AggregateChange[Membership](state=state, events=(event,))


class Memberships(BaseModel):
    """Every membership of one organisation, and the rules that span them.

    Invariants: every membership belongs to ``organization_id``; one membership per
    user; membership ids are unique. Changes also apply the **proposed** last-admin
    rule: an organisation that has an admin never drops to none.

    Concurrency is the persistence layer's concern: two concurrent additions of the
    same user must be stopped by a unique ``(organization_id, user_id)`` constraint,
    and a removal or demotion must lock the organisation's memberships so two
    admins cannot demote each other at once.

    Implements: Aggregate Root.

    Attributes:
        organization_id: The organisation.
        members: Its memberships, in the order they were added.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    organization_id: EntityId
    members: tuple[Membership, ...] = ()

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        foreign = [
            str(member.id)
            for member in self.members
            if member.organization_id != self.organization_id
        ]
        if foreign:
            message = f"memberships {foreign} belong to another organisation"
            raise ValueError(message)
        repeated_users = [
            str(user_id)
            for user_id, count in Counter(m.user_id for m in self.members).items()
            if count > 1
        ]
        if repeated_users:
            message = f"users {repeated_users} have more than one membership"
            raise ValueError(message)
        if len({member.id for member in self.members}) != len(self.members):
            message = "membership ids must be unique"
            raise ValueError(message)
        return self

    @property
    def admin_count(self) -> int:
        """Return how many members administer the organisation.

        Returns:
            The number of memberships with the ``admin`` role.
        """
        return sum(1 for member in self.members if member.is_admin)

    def find(self, user_id: EntityId) -> Membership | None:
        """Return a user's membership, if any.

        Args:
            user_id: The user.

        Returns:
            The membership, or ``None``.
        """
        return next((m for m in self.members if m.user_id == user_id), None)

    def get(self, user_id: EntityId) -> Membership:
        """Return a user's membership.

        Args:
            user_id: The user.

        Returns:
            The membership.

        Raises:
            MembershipNotFoundError: If the user is not a member.
        """
        membership = self.find(user_id)
        if membership is None:
            raise MembershipNotFoundError.for_member(self.organization_id, user_id)
        return membership

    def add(
        self, change: AggregateChange[Membership]
    ) -> AggregateChange["Memberships"]:
        """Add a membership created by ``MembershipFactory``, one per user.

        Args:
            change: The new membership and its ``MembershipAdded`` event.

        Returns:
            The collection including the membership, with the change's events.

        Raises:
            DuplicateMembershipError: If the user is already a member.
            InvariantViolationError: If the membership is for another organisation.
        """
        membership = change.state
        if membership.organization_id != self.organization_id:
            message = f"membership {membership.id} is for another organisation"
            raise InvariantViolationError(
                message, details={"organization_id": str(self.organization_id)}
            )
        if self.find(membership.user_id) is not None:
            raise DuplicateMembershipError.for_member(
                self.organization_id, membership.user_id
            )
        state = self.model_validate(
            {
                "organization_id": self.organization_id,
                "members": (*self.members, membership),
            }
        )
        return AggregateChange[Memberships](state=state, events=change.events)

    def change_role(
        self,
        user_id: EntityId,
        role: OrganizationRole,
        *,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange["Memberships"]:
        """Change a member's role.

        Args:
            user_id: The member.
            role: The new role.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of event ids.

        Returns:
            The collection and ``MembershipRoleChanged``, or the unchanged
            collection and no events if the role is the same.

        Raises:
            MembershipNotFoundError: If the user is not a member.
            LastOrganizationAdminError: If this demotes the only admin.
        """
        current = self.get(user_id)
        if current.is_admin and role is not OrganizationRole.ADMIN:
            self._require_another_admin(current)
        change = current.change_role(role, clock=clock, ids=ids)
        if not change.events:
            return AggregateChange[Memberships](state=self)
        state = self._replace(current, change.state)
        return AggregateChange[Memberships](state=state, events=change.events)

    def remove(
        self, user_id: EntityId, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["Memberships"]:
        """Remove a member; the ``MembershipRemoved`` event is the audit record.

        Args:
            user_id: The member.
            clock: Source of ``occurred_at``.
            ids: Source of event ids.

        Returns:
            The collection without the member and ``MembershipRemoved``.

        Raises:
            MembershipNotFoundError: If the user is not a member.
            LastOrganizationAdminError: If this removes the only admin.
        """
        current = self.get(user_id)
        if current.is_admin:
            self._require_another_admin(current)
        state = self.model_validate(
            {
                "organization_id": self.organization_id,
                "members": tuple(m for m in self.members if m is not current),
            }
        )
        event = MembershipRemoved(
            event_id=ids.new_id(),
            occurred_at=clock.now(),
            aggregate_id=current.id,
            organization_id=current.organization_id,
            user_id=current.user_id,
            role=current.role,
            version=current.version,
        )
        return AggregateChange[Memberships](state=state, events=(event,))

    def _require_another_admin(self, admin: Membership) -> None:
        if self.admin_count <= 1:
            raise LastOrganizationAdminError.for_member(
                self.organization_id, admin.user_id
            )

    def _replace(self, old: Membership, new: Membership) -> Self:
        return self.model_validate(
            {
                "organization_id": self.organization_id,
                "members": tuple(new if m is old else m for m in self.members),
            }
        )
