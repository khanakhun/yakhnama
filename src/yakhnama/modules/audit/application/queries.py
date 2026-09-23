"""Read requests accepted by the audit query services.

Patterns: Query.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.audit.domain.value_objects import AuditTarget
from yakhnama.modules.identity.public import Actor
from yakhnama.shared_kernel.pagination import PageRequest


class ListAuditEntriesForTarget(BaseModel):
    """Ask for one page of the entries about one aggregate, newest first.

    Implements: Query.

    Attributes:
        actor: Who asks; a platform administrator.
        target: The aggregate type and id.
        page: Page size and cursor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    target: AuditTarget
    page: PageRequest = PageRequest()


class ListRecentAuditEntries(BaseModel):
    """Ask for one page of the whole log, newest first.

    Implements: Query.

    Attributes:
        actor: Who asks; a platform administrator.
        page: Page size and cursor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    page: PageRequest = PageRequest()
