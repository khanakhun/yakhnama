"""Read requests accepted by the identity query service.

Patterns: Query.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.identity.domain.value_objects import Actor
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import PageRequest


class GetMe(BaseModel):
    """Ask for the acting user's own record.

    Implements: Query.

    Attributes:
        actor: The authenticated actor; its ``user_id`` names the record.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor


class GetOrganization(BaseModel):
    """Ask for one organisation, whatever its status.

    Implements: Query.

    Attributes:
        organization_id: The organisation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    organization_id: EntityId


class ListOrganizationMembers(BaseModel):
    """Ask for one page of an organisation's members, oldest membership first.

    Implements: Query.

    Attributes:
        organization_id: The organisation.
        page: Page size and cursor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    organization_id: EntityId
    page: PageRequest = PageRequest()
