"""Read requests accepted by the media query services.

Patterns: Query.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.identity.public import Actor
from yakhnama.modules.media.domain.value_objects import ModerationStatus, ScanStatus
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import PageRequest


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


class ListMediaQueue(BaseModel):
    """Ask for one page of the moderators' media queue, oldest first.

    Only assets whose upload completed are queued: before that there is no file
    to look at, and a failed upload never will have one.

    Implements: Query.

    Attributes:
        actor: Who asks; moderators only.
        moderation_status: Only assets with this moderation status, if set.
        scan_status: Only assets with this scan verdict, if set.
        page: Page size and cursor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    moderation_status: ModerationStatus | None = None
    scan_status: ScanStatus | None = None
    page: PageRequest = PageRequest()
