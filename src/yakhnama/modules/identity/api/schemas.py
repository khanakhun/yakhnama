"""Request and response bodies of the identity HTTP API.

Responses are the application's DTOs as they are (``MeDetail``,
``OrganizationDetail``, ``MemberSummary``, ``UserDetail``): they are the public
read models and hold no subject, issuer or suspension reason. Request bodies reuse
the domain's constrained types, so the API and the commands agree on every bound;
the commands validate again when they are built.

Patterns: API Schema.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from yakhnama.modules.identity.application.dto import MemberSummary
from yakhnama.modules.identity.domain.value_objects import (
    DisplayName,
    OrganizationName,
    OrganizationSlug,
    StatusReason,
)
from yakhnama.modules.identity.public import OrganizationRole, OrganizationType, Role
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import (
    DEFAULT_PAGE_LIMIT,
    MAX_CURSOR_LENGTH,
    MAX_PAGE_LIMIT,
)

ORGANIZATION_SLUG_MAX_LENGTH = 64

BoundedSlug = Annotated[
    OrganizationSlug, StringConstraints(max_length=ORGANIZATION_SLUG_MAX_LENGTH)
]


class UpdateMeRequest(BaseModel):
    """Body of ``PATCH /api/v1/me``.

    Implements: API Schema.

    Attributes:
        display_name: The new display name, 1 to 120 characters after NFC and
            stripping, or ``null`` to clear it. Required, so clearing is explicit.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    display_name: DisplayName | None


class CreateOrganizationRequest(BaseModel):
    """Body of ``POST /api/v1/organizations``.

    Implements: API Schema.

    Attributes:
        slug: URL-safe handle, 2 to 64 lower-case letters, digits or ``-``.
        name: Display name, 1 to 200 characters.
        organization_type: The kind of organisation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: BoundedSlug
    name: OrganizationName
    organization_type: OrganizationType


class RenameOrganizationRequest(BaseModel):
    """Body of ``PATCH /api/v1/organizations/{organization_id}``.

    Implements: API Schema.

    Attributes:
        name: The new display name, 1 to 200 characters.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: OrganizationName


class AddMemberRequest(BaseModel):
    """Body of ``POST /api/v1/organizations/{organization_id}/members``.

    Implements: API Schema.

    Attributes:
        user_id: The user to add (UUIDv7).
        role: Their role in the organisation, ``member`` by default.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: EntityId
    role: OrganizationRole = OrganizationRole.MEMBER


class ChangeMemberRoleRequest(BaseModel):
    """Body of ``PATCH /api/v1/organizations/{organization_id}/members/{user_id}``.

    Implements: API Schema.

    Attributes:
        role: The member's new role.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: OrganizationRole


class GrantRoleRequest(BaseModel):
    """Body of ``POST /api/v1/users/{user_id}/roles``.

    Implements: API Schema.

    Attributes:
        role: The platform role to grant.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Role


class SuspendUserRequest(BaseModel):
    """Body of ``POST /api/v1/users/{user_id}/suspension``.

    Implements: API Schema.

    Attributes:
        reason: Why, 1 to 500 characters; kept on the user, never returned.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: StatusReason


class ListMembersParameters(BaseModel):
    """Query string of ``GET /api/v1/organizations/{organization_id}/members``.

    Implements: API Schema.

    Attributes:
        cursor: Opaque cursor from a previous page.
        limit: Page size, 1 to 200.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cursor: Annotated[str | None, Field(max_length=MAX_CURSOR_LENGTH)] = None
    limit: Annotated[int, Field(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT


class MemberPage(BaseModel):
    """One page of an organisation's members, oldest membership first.

    Implements: API Schema.

    Attributes:
        items: The members on this page.
        next_cursor: Cursor of the next page, or ``None`` on the last page.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[MemberSummary, ...] = Field(max_length=MAX_PAGE_LIMIT)
    next_cursor: str | None = Field(max_length=MAX_CURSOR_LENGTH)


class ModerationStatus(BaseModel):
    """Body of ``GET /api/v1/moderation/ping``.

    Implements: API Schema.

    Attributes:
        status: Always ``ok``: the caller may moderate.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ok"] = "ok"
