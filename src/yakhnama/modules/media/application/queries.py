"""Read requests accepted by the media query services.

Patterns: Query.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.identity.public import Actor
from yakhnama.shared_kernel.ids import EntityId


class GetMediaAsset(BaseModel):
    """Ask for one media asset and the download links the actor may use.

    Implements: Query.

    Attributes:
        actor: Who asks; anonymous callers may read published assets.
        asset_id: The asset.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    asset_id: EntityId
