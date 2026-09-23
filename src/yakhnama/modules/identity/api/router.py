"""HTTP routes of the identity context and the moderation scaffold.

``router`` serves ``/api/v1/me``, ``/api/v1/organizations/...`` and the
administrator-only ``/api/v1/users/...``; ``moderation_router`` serves
``/api/v1/moderation/...``, where every route requires ``CanModerate``.

Authorisation lives in the command handlers (``CanManageOrganization``, ``IsAdmin``,
self rules); reads check the identity read policies here, before the query service
is called, as ``IdentityQueryService`` requires. Errors are never mapped here: the
handlers, policies and this router raise ``YakhnamaError`` subclasses and the
exception handlers in ``main.py`` render Problem Details.

Concurrency: ``PATCH /me`` and ``PATCH /organizations/{id}`` require ``If-Match``
(428 without it, 412 when stale). The member and administrator routes accept an
optional ``If-Match`` and check it when sent. A member's tag names the member's user
id and the membership version. ``POST`` routes are made idempotent by the platform
middleware when an ``Idempotency-Key`` (a UUID) is sent.

Patterns: none from the catalog (thin transport layer over commands and queries).
"""

from typing import Annotated, Any, Final
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Header, Query, Response, status

from yakhnama.modules.identity.api.dependencies import (
    CurrentActor,
    IdentityApiServices,
    OptionalActor,
    Services,
)
from yakhnama.modules.identity.api.schemas import (
    AddMemberRequest,
    ChangeMemberRoleRequest,
    CreateOrganizationRequest,
    GrantRoleRequest,
    ListMembersParameters,
    MemberPage,
    ModerationStatus,
    RenameOrganizationRequest,
    SuspendUserRequest,
    UpdateMeRequest,
)
from yakhnama.modules.identity.public import (
    Actor,
    AddMember,
    AddMemberHandler,
    CanModerate,
    ChangeMemberRole,
    ChangeMemberRoleHandler,
    CreateOrganization,
    CreateOrganizationHandler,
    GrantRole,
    GrantRoleHandler,
    ListOrganizationMembers,
    MeDetail,
    MemberSummary,
    OrganizationDetail,
    ReinstateUser,
    ReinstateUserHandler,
    RemoveMember,
    RemoveMemberHandler,
    RenameOrganization,
    RenameOrganizationHandler,
    RenameSelf,
    RenameSelfHandler,
    RevokeRole,
    RevokeRoleHandler,
    Role,
    SuspendUser,
    SuspendUserHandler,
    UserDetail,
    member_list_policy,
    organization_read_policy,
    require_allowed,
)
from yakhnama.platform.auth.tokens import REJECTION_MESSAGE
from yakhnama.platform.etag import (
    expected_version_from_if_match,
    make_etag,
    set_etag,
)
from yakhnama.shared_kernel.errors import AuthenticationError, NotFoundError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import PageRequest

API_PREFIX: Final = "/api/v1"
ORGANIZATIONS_PATH: Final = f"{API_PREFIX}/organizations"
LINK_HEADER: Final = "Link"
LOCATION_HEADER: Final = "Location"
IF_MATCH_MAX_LENGTH: Final = 512
USER_NOT_FOUND_MESSAGE: Final = "The user record does not exist."
ORGANIZATION_NOT_FOUND_MESSAGE: Final = "No organisation has this id."

# Any: the value type of FastAPI's own ``responses`` argument.
_PROBLEM: Final[dict[str, Any]] = {
    "description": "RFC 9457 Problem Details",
    "content": {"application/problem+json": {}},
}
_COMMON_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_401_UNAUTHORIZED: _PROBLEM,
    status.HTTP_403_FORBIDDEN: _PROBLEM,
    status.HTTP_422_UNPROCESSABLE_CONTENT: _PROBLEM,
    status.HTTP_429_TOO_MANY_REQUESTS: _PROBLEM,
    status.HTTP_503_SERVICE_UNAVAILABLE: _PROBLEM,
}
_READ_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_404_NOT_FOUND: _PROBLEM
}
_CHANGE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_404_NOT_FOUND: _PROBLEM,
    status.HTTP_409_CONFLICT: _PROBLEM,
    status.HTTP_412_PRECONDITION_FAILED: _PROBLEM,
}
_CONDITIONAL_RESPONSES: Final[dict[int | str, dict[str, Any]]] = _CHANGE_RESPONSES | {
    status.HTTP_428_PRECONDITION_REQUIRED: _PROBLEM,
}
_CREATE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_400_BAD_REQUEST: _PROBLEM,
    status.HTTP_404_NOT_FOUND: _PROBLEM,
    status.HTTP_409_CONFLICT: _PROBLEM,
}

router = APIRouter(prefix=API_PREFIX, tags=["identity"], responses=_COMMON_RESPONSES)
moderation_router = APIRouter(
    prefix=f"{API_PREFIX}/moderation",
    tags=["moderation"],
    responses=_COMMON_RESPONSES,
)

IfMatch = Annotated[
    str | None,
    Header(
        alias="If-Match",
        max_length=IF_MATCH_MAX_LENGTH,
        description='The resource\'s ETag, for example "<id>:3".',
    ),
]
IdempotencyKey = Annotated[
    UUID | None,
    Header(
        alias="Idempotency-Key",
        description=(
            "A UUID chosen by the client; replaying it with the same request "
            "returns the stored response."
        ),
    ),
]


def optional_expected_version(if_match: str | None, entity_id: UUID) -> int | None:
    """Return the version named by an optional ``If-Match``.

    Args:
        if_match: The header, or ``None`` when the client sent none.
        entity_id: The id the tag must name.

    Returns:
        The version, or ``None`` when no ``If-Match`` was sent.

    Raises:
        PreconditionFailedError: If the header does not name ``entity_id`` with a
            valid version.
    """
    if if_match is None:
        return None
    return expected_version_from_if_match(if_match, entity_id)


def _next_link(path: str, parameters: ListMembersParameters, cursor: str) -> str:
    following = parameters.model_copy(update={"cursor": cursor})
    query = urlencode(following.model_dump(mode="json", exclude_none=True))
    return f'<{path}?{query}>; rel="next"'


def _member_etag(member: MemberSummary) -> str:
    return make_etag(member.version, member.user_id)


def authenticated_user_id(actor: Actor) -> UUID:
    """Return the user id of an actor that must be authenticated.

    Args:
        actor: An actor from ``current_actor``.

    Returns:
        Its user id.

    Raises:
        AuthenticationError: If the actor is anonymous; ``current_actor`` never
            yields one, so this only narrows the type.
    """
    if actor.user_id is None:
        raise AuthenticationError(REJECTION_MESSAGE)
    return actor.user_id


async def _read_me(actor: Actor, services: IdentityApiServices) -> MeDetail:
    detail = await services.identity_query_service.get_me(authenticated_user_id(actor))
    if detail is None:
        raise NotFoundError(USER_NOT_FOUND_MESSAGE)
    return detail


def _user_response(response: Response, detail: UserDetail) -> UserDetail:
    set_etag(response, make_etag(detail.version, detail.id))
    return detail


# --------------------------------------------------------------------------- #
# The current user                                                            #
# --------------------------------------------------------------------------- #


@router.get("/me", responses=_READ_RESPONSES)
async def read_me(
    actor: CurrentActor, response: Response, services: Services
) -> MeDetail:
    """Return the caller's own record, mirroring them on first sight.

    Args:
        actor: The authenticated caller.
        response: Used to set the ``ETag`` header.
        services: Identity services bound by the composition root.

    Returns:
        The caller's record with roles and memberships.
    """
    detail = await _read_me(actor, services)
    set_etag(response, make_etag(detail.version, detail.id))
    return detail


@router.patch("/me", responses=_CONDITIONAL_RESPONSES)
async def update_me(
    body: UpdateMeRequest,
    actor: CurrentActor,
    response: Response,
    services: Services,
    if_match: IfMatch = None,
) -> MeDetail:
    """Set or clear the caller's display name if they hold the current version.

    Args:
        body: The new display name.
        actor: The authenticated caller.
        response: Used to set the new ``ETag``.
        services: Identity services bound by the composition root.
        if_match: The ETag of ``GET /me`` the change is based on.

    Returns:
        The caller's updated record.

    Raises:
        PreconditionRequiredError: Without ``If-Match``.
        PreconditionFailedError: If ``If-Match`` is stale or names another user.
    """
    expected_version = expected_version_from_if_match(
        if_match, authenticated_user_id(actor)
    )
    await RenameSelfHandler(
        services.identity_uow_factory, services.clock, services.id_generator
    )(
        RenameSelf(
            actor=actor,
            display_name=body.display_name,
            expected_version=expected_version,
        )
    )
    detail = await _read_me(actor, services)
    set_etag(response, make_etag(detail.version, detail.id))
    return detail


# --------------------------------------------------------------------------- #
# Organisations                                                               #
# --------------------------------------------------------------------------- #


@router.post(
    "/organizations",
    status_code=status.HTTP_201_CREATED,
    responses=_CREATE_RESPONSES,
)
async def create_organization(
    body: CreateOrganizationRequest,
    actor: CurrentActor,
    response: Response,
    services: Services,
    idempotency_key: IdempotencyKey = None,
) -> OrganizationDetail:
    """Create an organisation whose creator becomes its first admin.

    Args:
        body: Slug, name and type.
        actor: The authenticated caller.
        response: Used to set ``Location`` and ``ETag``.
        services: Identity services bound by the composition root.
        idempotency_key: Documented here; the idempotency middleware acts on it.

    Returns:
        The created organisation.
    """
    del idempotency_key
    detail = await CreateOrganizationHandler(
        services.identity_uow_factory, services.clock, services.id_generator
    )(
        CreateOrganization(
            actor=actor,
            slug=body.slug,
            name=body.name,
            organization_type=body.organization_type,
        )
    )
    response.headers[LOCATION_HEADER] = f"{ORGANIZATIONS_PATH}/{detail.id}"
    set_etag(response, make_etag(detail.version, detail.id))
    return detail


@router.get("/organizations/{organization_id}", responses=_READ_RESPONSES)
async def read_organization(
    organization_id: EntityId,
    actor: OptionalActor,
    response: Response,
    services: Services,
) -> OrganizationDetail:
    """Return one organisation, whatever its status, with its ``ETag``.

    Anonymous callers may read it (``organization_read_policy``, proposed).

    Args:
        organization_id: The organisation.
        actor: The caller, anonymous or authenticated.
        response: Used to set the ``ETag`` header.
        services: Identity services bound by the composition root.

    Returns:
        The organisation with its member count.

    Raises:
        PermissionDeniedError: If the read policy refuses the caller.
        NotFoundError: If no organisation has this id.
    """
    require_allowed(organization_read_policy(), actor, action="read organisations")
    detail = await services.identity_query_service.get_organization(organization_id)
    if detail is None:
        raise NotFoundError(ORGANIZATION_NOT_FOUND_MESSAGE)
    set_etag(response, make_etag(detail.version, detail.id))
    return detail


@router.patch("/organizations/{organization_id}", responses=_CONDITIONAL_RESPONSES)
async def rename_organization(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    organization_id: EntityId,
    body: RenameOrganizationRequest,
    actor: CurrentActor,
    response: Response,
    services: Services,
    if_match: IfMatch = None,
) -> OrganizationDetail:
    """Rename an organisation if the caller holds its current version.

    Args:
        organization_id: The organisation.
        body: The new name.
        actor: The authenticated caller; ``CanManageOrganization`` decides.
        response: Used to set the new ``ETag``.
        services: Identity services bound by the composition root.
        if_match: The organisation's ETag the change is based on.

    Returns:
        The renamed organisation.

    Raises:
        PreconditionRequiredError: Without ``If-Match``.
        PreconditionFailedError: If ``If-Match`` is stale.
    """
    expected_version = expected_version_from_if_match(if_match, organization_id)
    detail = await RenameOrganizationHandler(
        services.identity_uow_factory, services.clock, services.id_generator
    )(
        RenameOrganization(
            actor=actor,
            organization_id=organization_id,
            name=body.name,
            expected_version=expected_version,
        )
    )
    set_etag(response, make_etag(detail.version, detail.id))
    return detail


# --------------------------------------------------------------------------- #
# Members                                                                     #
# --------------------------------------------------------------------------- #


@router.get("/organizations/{organization_id}/members")
async def list_members(
    organization_id: EntityId,
    parameters: Annotated[ListMembersParameters, Query()],
    actor: CurrentActor,
    response: Response,
    services: Services,
) -> MemberPage:
    """List an organisation's members, oldest membership first.

    Only its members and platform administrators may list them
    (``member_list_policy``, proposed), because the list shows display names.

    Args:
        organization_id: The organisation.
        parameters: Cursor and limit.
        actor: The authenticated caller.
        response: Used to set the ``Link`` header of the next page.
        services: Identity services bound by the composition root.

    Returns:
        One page of members.

    Raises:
        PermissionDeniedError: If the caller is neither a member nor an admin.
    """
    require_allowed(
        member_list_policy(organization_id),
        actor,
        action="list organisation members",
    )
    page = await services.identity_query_service.list_members(
        ListOrganizationMembers(
            organization_id=organization_id,
            page=PageRequest(limit=parameters.limit, cursor=parameters.cursor),
        )
    )
    if page.next_cursor is not None:
        path = f"{ORGANIZATIONS_PATH}/{organization_id}/members"
        response.headers[LINK_HEADER] = _next_link(path, parameters, page.next_cursor)
    return MemberPage(items=page.items, next_cursor=page.next_cursor)


@router.post(
    "/organizations/{organization_id}/members",
    status_code=status.HTTP_201_CREATED,
    responses=_CREATE_RESPONSES,
)
async def add_member(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    organization_id: EntityId,
    body: AddMemberRequest,
    actor: CurrentActor,
    response: Response,
    services: Services,
    idempotency_key: IdempotencyKey = None,
) -> MemberSummary:
    """Add a user to an organisation.

    Args:
        organization_id: The organisation.
        body: The user and their role.
        actor: The authenticated caller; ``CanManageOrganization`` decides.
        response: Used to set the member's ``ETag``.
        services: Identity services bound by the composition root.
        idempotency_key: Documented here; the idempotency middleware acts on it.

    Returns:
        The new member.
    """
    del idempotency_key
    member = await AddMemberHandler(
        services.identity_uow_factory, services.clock, services.id_generator
    )(
        AddMember(
            actor=actor,
            organization_id=organization_id,
            user_id=body.user_id,
            role=body.role,
        )
    )
    set_etag(response, _member_etag(member))
    return member


@router.patch(
    "/organizations/{organization_id}/members/{user_id}",
    responses=_CHANGE_RESPONSES,
)
async def change_member_role(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    organization_id: EntityId,
    user_id: EntityId,
    body: ChangeMemberRoleRequest,
    actor: CurrentActor,
    response: Response,
    services: Services,
    if_match: IfMatch = None,
) -> MemberSummary:
    """Change a member's role in an organisation.

    Args:
        organization_id: The organisation.
        user_id: The member.
        body: The new role.
        actor: The authenticated caller; ``CanManageOrganization`` decides.
        response: Used to set the member's new ``ETag``.
        services: Identity services bound by the composition root.
        if_match: Optional member ETag the change is based on.

    Returns:
        The member after the change.
    """
    member = await ChangeMemberRoleHandler(
        services.identity_uow_factory, services.clock, services.id_generator
    )(
        ChangeMemberRole(
            actor=actor,
            organization_id=organization_id,
            user_id=user_id,
            role=body.role,
            expected_version=optional_expected_version(if_match, user_id),
        )
    )
    set_etag(response, _member_etag(member))
    return member


@router.delete(
    "/organizations/{organization_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_CHANGE_RESPONSES,
)
async def remove_member(
    organization_id: EntityId,
    user_id: EntityId,
    actor: CurrentActor,
    services: Services,
    if_match: IfMatch = None,
) -> None:
    """Remove a member from an organisation.

    Args:
        organization_id: The organisation.
        user_id: The member.
        actor: The authenticated caller; ``CanManageOrganization`` decides.
        services: Identity services bound by the composition root.
        if_match: Optional member ETag the removal is based on.
    """
    await RemoveMemberHandler(
        services.identity_uow_factory, services.clock, services.id_generator
    )(
        RemoveMember(
            actor=actor,
            organization_id=organization_id,
            user_id=user_id,
            expected_version=optional_expected_version(if_match, user_id),
        )
    )


# --------------------------------------------------------------------------- #
# Users (administrators only; the handlers check IsAdmin)                     #
# --------------------------------------------------------------------------- #


@router.post("/users/{user_id}/roles", responses=_CHANGE_RESPONSES)
async def grant_role(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    user_id: EntityId,
    body: GrantRoleRequest,
    actor: CurrentActor,
    response: Response,
    services: Services,
    if_match: IfMatch = None,
) -> UserDetail:
    """Grant a platform role to a user.

    Args:
        user_id: The user.
        body: The role.
        actor: The authenticated caller; administrators only.
        response: Used to set the user's new ``ETag``.
        services: Identity services bound by the composition root.
        if_match: Optional user ETag the change is based on.

    Returns:
        The user after the change.
    """
    detail = await GrantRoleHandler(
        services.identity_uow_factory, services.clock, services.id_generator
    )(
        GrantRole(
            actor=actor,
            user_id=user_id,
            role=body.role,
            expected_version=optional_expected_version(if_match, user_id),
        )
    )
    return _user_response(response, detail)


@router.delete("/users/{user_id}/roles/{role}", responses=_CHANGE_RESPONSES)
async def revoke_role(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    user_id: EntityId,
    role: Role,
    actor: CurrentActor,
    response: Response,
    services: Services,
    if_match: IfMatch = None,
) -> UserDetail:
    """Revoke a platform role from a user; ``citizen`` cannot be revoked.

    Args:
        user_id: The user.
        role: The role to revoke.
        actor: The authenticated caller; administrators only.
        response: Used to set the user's new ``ETag``.
        services: Identity services bound by the composition root.
        if_match: Optional user ETag the change is based on.

    Returns:
        The user after the change.
    """
    detail = await RevokeRoleHandler(
        services.identity_uow_factory, services.clock, services.id_generator
    )(
        RevokeRole(
            actor=actor,
            user_id=user_id,
            role=role,
            expected_version=optional_expected_version(if_match, user_id),
        )
    )
    return _user_response(response, detail)


@router.post("/users/{user_id}/suspension", responses=_CHANGE_RESPONSES)
async def suspend_user(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    user_id: EntityId,
    body: SuspendUserRequest,
    actor: CurrentActor,
    response: Response,
    services: Services,
    if_match: IfMatch = None,
) -> UserDetail:
    """Suspend a user so they can no longer act.

    Args:
        user_id: The user.
        body: The reason, kept on the user and never returned.
        actor: The authenticated caller; administrators only.
        response: Used to set the user's new ``ETag``.
        services: Identity services bound by the composition root.
        if_match: Optional user ETag the change is based on.

    Returns:
        The suspended user.
    """
    detail = await SuspendUserHandler(
        services.identity_uow_factory, services.clock, services.id_generator
    )(
        SuspendUser(
            actor=actor,
            user_id=user_id,
            reason=body.reason,
            expected_version=optional_expected_version(if_match, user_id),
        )
    )
    return _user_response(response, detail)


@router.delete("/users/{user_id}/suspension", responses=_CHANGE_RESPONSES)
async def reinstate_user(
    user_id: EntityId,
    actor: CurrentActor,
    response: Response,
    services: Services,
    if_match: IfMatch = None,
) -> UserDetail:
    """Make a suspended user active again.

    Args:
        user_id: The user.
        actor: The authenticated caller; administrators only.
        response: Used to set the user's new ``ETag``.
        services: Identity services bound by the composition root.
        if_match: Optional user ETag the change is based on.

    Returns:
        The reinstated user.
    """
    detail = await ReinstateUserHandler(
        services.identity_uow_factory, services.clock, services.id_generator
    )(
        ReinstateUser(
            actor=actor,
            user_id=user_id,
            expected_version=optional_expected_version(if_match, user_id),
        )
    )
    return _user_response(response, detail)


# --------------------------------------------------------------------------- #
# Moderation                                                                  #
# --------------------------------------------------------------------------- #


@moderation_router.get("/ping")
async def moderation_ping(actor: CurrentActor) -> ModerationStatus:
    """Confirm the caller may use the moderation API.

    Args:
        actor: The authenticated caller; ``CanModerate`` decides.

    Returns:
        ``{"status": "ok"}``.

    Raises:
        PermissionDeniedError: If the caller is neither moderator nor admin.
    """
    require_allowed(CanModerate(), actor, action="use the moderation API")
    return ModerationStatus()
