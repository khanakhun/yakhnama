"""Authorised read use cases of the media module.

``AuthorisedMediaQueryService`` decides which copies the actor may download and
presigns only those: the public copy for anyone once it is published, the private
original (which still carries EXIF, GPS included) for its uploader and moderators
only. An asset the actor may not see is reported as missing.

Patterns: Query Service, Policy.
"""

from yakhnama.modules.media.application.authorisation import (
    original_view_policy,
    public_view_policy,
)
from yakhnama.modules.media.application.dto import MediaAssetDetail
from yakhnama.modules.media.application.ports import MediaQueryService, StoragePort
from yakhnama.modules.media.application.queries import GetMediaAsset
from yakhnama.modules.media.domain.errors import MediaAssetNotFoundError
from yakhnama.modules.media.domain.value_objects import UploadStatus


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
