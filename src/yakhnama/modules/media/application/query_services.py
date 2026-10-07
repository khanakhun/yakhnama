"""Authorised read use cases of the media module.

``AuthorisedMediaQueryService`` decides which copies the actor may download and
presigns only those: the public copy for anyone once it is published, the private
original (which still carries EXIF, GPS included) for its uploader and moderators
only. An asset the actor may not see is reported as missing.

The moderators' queue (``list_media_queue``) is refused to everyone else and never
presigns anything: a page of up to 200 links would be expensive to sign and would
hand out originals nobody opened. A moderator gets the links of one asset from
``get_media_asset``.

Patterns: Query Service, Policy.
"""

from yakhnama.modules.media.application.authorisation import (
    moderation_policy,
    original_view_policy,
    public_view_policy,
    require_allowed,
)
from yakhnama.modules.media.application.dto import MediaAssetDetail
from yakhnama.modules.media.application.ports import MediaQueryService, StoragePort
from yakhnama.modules.media.application.queries import GetMediaAsset, ListMediaQueue
from yakhnama.modules.media.domain.errors import MediaAssetNotFoundError
from yakhnama.modules.media.domain.value_objects import UploadStatus
from yakhnama.shared_kernel.pagination import Page


class AuthorisedMediaQueryService:
    """Answer media queries with presigned links the actor may use.

    Implements: Query Service.
    """

    def __init__(self, query_service: MediaQueryService, storage: StoragePort) -> None:
        """Create the service.

        Args:
            query_service: The read port.
            storage: Presigns the downloads.
        """
        self._query_service = query_service
        self._storage = storage

    async def get_media_asset(self, query: GetMediaAsset) -> MediaAssetDetail:
        """Return one asset with the download links the actor may use.

        Args:
            query: The actor and the asset id.

        Returns:
            For the uploader and moderators, the asset with the original's link
            (once uploaded) and the public link (once published); for anyone else,
            a published asset with its public link only.

        Raises:
            MediaAssetNotFoundError: If the asset does not exist, or the actor may
                not see it.
        """
        record = await self._query_service.get_asset(query.asset_id)
        if record is None:
            raise MediaAssetNotFoundError.for_id(query.asset_id)
        is_privileged = original_view_policy(record.owner_id).is_allowed(query.actor)
        if not is_privileged and not (
            record.is_published and public_view_policy().is_allowed(query.actor)
        ):
            raise MediaAssetNotFoundError.for_id(query.asset_id)
        public_download = (
            None
            if record.public_key is None
            else await self._storage.presign_get(record.public_key)
        )
        original_download = (
            await self._storage.presign_get(record.original_key)
            if is_privileged and record.upload_status is UploadStatus.COMPLETED
            else None
        )
        return MediaAssetDetail.from_record(
            record,
            public_download=public_download,
            original_download=original_download,
        )

    async def list_media_queue(self, query: ListMediaQueue) -> Page[MediaAssetDetail]:
        """Return one page of the moderators' queue, oldest first, without links.

        Args:
            query: The actor, filters and page request.

        Returns:
            Completed uploads with their statuses and report; no download links.

        Raises:
            PermissionDeniedError: If the actor may not moderate.
            ValidationError: If the cursor is invalid.
        """
        require_allowed(moderation_policy(), query.actor, action="read the media queue")
        page = await self._query_service.list_queue(
            moderation_status=query.moderation_status,
            scan_status=query.scan_status,
            page=query.page,
        )
        return Page[MediaAssetDetail](
            items=tuple(MediaAssetDetail.from_record(record) for record in page.items),
            next_cursor=page.next_cursor,
        )
