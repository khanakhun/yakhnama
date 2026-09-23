"""Unit tests for ``AuthorisedMediaQueryService`` and which links it hands out."""

import pytest

from tests.unit.modules.media.application.support import (
    MISSING_ID,
    MODERATOR,
    OTHER_CITIZEN,
    OWNER,
    Harness,
)
from yakhnama.modules.identity.public import Actor
from yakhnama.modules.media.application.commands import RequestUpload
from yakhnama.modules.media.application.queries import GetMediaAsset
from yakhnama.modules.media.domain.errors import MediaAssetNotFoundError
from yakhnama.modules.media.domain.value_objects import (
    MimeType,
    original_object_key,
    public_object_key,
)


async def test_get_media_asset_published_anonymous_gets_public_link_only() -> None:
    harness = Harness()
    asset = await harness.published()

    result = await harness.queries().get_media_asset(
        GetMediaAsset(actor=Actor.anonymous(), asset_id=asset.id)
    )

    assert result.public_download is not None
    assert public_object_key(asset.id) in result.public_download.url
    assert result.original_download is None
    assert harness.storage.presigned_gets == [public_object_key(asset.id)]


@pytest.mark.parametrize("actor", [OWNER, MODERATOR])
async def test_get_media_asset_owner_or_moderator_gets_both_links(
    actor: Actor,
) -> None:
    harness = Harness()
    asset = await harness.published()

    result = await harness.queries().get_media_asset(
        GetMediaAsset(actor=actor, asset_id=asset.id)
    )

    assert result.original_download is not None
    assert original_object_key(asset.id) in result.original_download.url
    assert result.public_download is not None


async def test_get_media_asset_unpublished_owner_gets_original_only() -> None:
    harness = Harness()
    asset = await harness.uploaded()

    result = await harness.queries().get_media_asset(
        GetMediaAsset(actor=OWNER, asset_id=asset.id)
    )

    assert result.original_download is not None
    assert result.public_download is None


async def test_get_media_asset_requested_owner_gets_no_link() -> None:
    harness = Harness()
    grant = await harness.request()(RequestUpload(actor=OWNER, mime_type=MimeType.JPEG))

    result = await harness.queries().get_media_asset(
        GetMediaAsset(actor=OWNER, asset_id=grant.asset_id)
    )

    assert result.original_download is None
    assert result.public_download is None
    assert harness.storage.presigned_gets == []


@pytest.mark.parametrize("actor", [Actor.anonymous(), OTHER_CITIZEN])
async def test_get_media_asset_unpublished_stranger_is_told_it_does_not_exist(
    actor: Actor,
) -> None:
    harness = Harness()
    asset = await harness.uploaded()

    with pytest.raises(MediaAssetNotFoundError):
        await harness.queries().get_media_asset(
            GetMediaAsset(actor=actor, asset_id=asset.id)
        )

    assert harness.storage.presigned_gets == []


async def test_get_media_asset_missing_raises_not_found() -> None:
    harness = Harness()

    with pytest.raises(MediaAssetNotFoundError):
        await harness.queries().get_media_asset(
            GetMediaAsset(actor=MODERATOR, asset_id=MISSING_ID)
        )
