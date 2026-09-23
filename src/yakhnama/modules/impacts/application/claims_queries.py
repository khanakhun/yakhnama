"""Read requests for impact claims, best figures and infrastructure assets.

Patterns: Query.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.identity.public import Actor
from yakhnama.modules.impacts.domain.value_objects import MetricCode
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import PageRequest


class GetEventImpacts(BaseModel):
    """Ask for an event's best figures per metric and its claims.

    Implements: Query.

    Attributes:
        actor: Who asks; a non-moderator only reads publicly visible events.
        event_id: The hazard event.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    event_id: EntityId


class ListClaims(BaseModel):
    """Ask for one page of an event's claims, in recording order.

    Implements: Query.

    Attributes:
        actor: Who asks; a non-moderator only reads publicly visible events.
        event_id: The hazard event.
        metric_code: Only claims of this metric, if set.
        include_retracted: Also return retracted claims; ``True`` by default so
            the record shows every figure ever claimed.
        page: Page size and cursor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    event_id: EntityId
    metric_code: MetricCode | None = None
    include_retracted: bool = True
    page: PageRequest = PageRequest()


class GetInfrastructureAsset(BaseModel):
    """Ask for one infrastructure asset; assets are public reference data.

    Implements: Query.

    Attributes:
        asset_id: The asset.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    asset_id: EntityId
