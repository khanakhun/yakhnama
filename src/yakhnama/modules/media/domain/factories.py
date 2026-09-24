"""Creation of media assets.

Patterns: Factory.
"""

from yakhnama.modules.media.domain.entities import MediaAsset
from yakhnama.modules.media.domain.events import UploadRequested
from yakhnama.modules.media.domain.value_objects import (
    MediaAttribution,
    MimeType,
    original_object_key,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import IdGenerator


class MediaAssetFactory:
    """Create media assets awaiting their upload.

    Implements: Factory.
    """

    def request_upload(
        self,
        attribution: MediaAttribution,
        mime_type: MimeType,
        *,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange[MediaAsset]:
        """Create an asset in ``requested`` state with its private original key.

        The key is derived from the new id (``original_object_key``), never from a
        file name, so it carries no personal data and cannot collide.

        Args:
            attribution: Owner, source and report of the asset.
            mime_type: The media type the uploader declares; checked again against
                the file's content at completion.
            clock: Source of every timestamp.
            ids: Source of the asset id and the event id.

        Returns:
            The new asset and ``UploadRequested``.
        """
        now = clock.now()
        asset_id = ids.new_id()
        asset = MediaAsset(
            id=asset_id,
            owner_id=attribution.owner_id,
            report_id=attribution.report_id,
            source_id=attribution.source_id,
            original_key=original_object_key(asset_id),
            mime_type=mime_type,
            created_at=now,
            updated_at=now,
        )
        event = UploadRequested(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=asset.id,
            version=asset.version,
            owner_id=asset.owner_id,
            report_id=asset.report_id,
            source_id=asset.source_id,
            mime_type=mime_type,
        )
        return AggregateChange[MediaAsset](state=asset, events=(event,))
