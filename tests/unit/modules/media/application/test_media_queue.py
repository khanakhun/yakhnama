"""Unit tests for the moderators' media queue (``list_media_queue``)."""

import pytest

from tests.fakes.media import InMemoryMediaQueryService
from tests.unit.modules.media.application.support import (
    MODERATOR,
    OTHER_CITIZEN,
    OWNER,
    REPORT_ID,
    Harness,
)
from yakhnama.modules.identity.public import Actor
from yakhnama.modules.media.application.commands import RequestUpload
from yakhnama.modules.media.application.queries import ListMediaQueue
from yakhnama.modules.media.application.query_services import (
    AuthorisedMediaQueryService,
)
from yakhnama.modules.media.domain.value_objects import (
    MimeType,
    ModerationStatus,
    ScanStatus,
)
from yakhnama.shared_kernel.errors import PermissionDeniedError, ValidationError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import PageRequest


def _queries(
    harness: Harness, listing: dict[EntityId, EntityId] | None = None
) -> AuthorisedMediaQueryService:
    return AuthorisedMediaQueryService(
        InMemoryMediaQueryService(harness.uow, listing), harness.storage
    )


async def test_list_media_queue_moderator_gets_completed_assets_oldest_first() -> None:
    harness = Harness()
    first = await harness.uploaded()
    second = await harness.published()
    await harness.request()(
        RequestUpload(actor=OWNER, mime_type=MimeType.JPEG, byte_size=1024)
    )

    page = await _queries(harness).list_media_queue(ListMediaQueue(actor=MODERATOR))

    assert [item.id for item in page.items] == [first.id, second.id]
    assert page.next_cursor is None


async def test_list_media_queue_never_presigns_links() -> None:
    harness = Harness()
    await harness.published()

    page = await _queries(harness).list_media_queue(ListMediaQueue(actor=MODERATOR))

    assert page.items[0].public_download is None
    assert page.items[0].original_download is None
    assert harness.storage.presigned_gets == []


async def test_list_media_queue_filters_by_moderation_and_scan_status() -> None:
    harness = Harness()
    pending = await harness.uploaded()
    approved = await harness.published()

    by_status = await _queries(harness).list_media_queue(
        ListMediaQueue(actor=MODERATOR, moderation_status=ModerationStatus.PENDING)
    )
    by_scan = await _queries(harness).list_media_queue(
        ListMediaQueue(actor=MODERATOR, scan_status=ScanStatus.CLEAN)
    )

    assert [item.id for item in by_status.items] == [pending.id]
    assert [item.id for item in by_scan.items] == [approved.id]


async def test_list_media_queue_pages_with_a_cursor() -> None:
    harness = Harness()
    assets = [await harness.uploaded() for _ in range(3)]
    queries = _queries(harness)

    first = await queries.list_media_queue(
        ListMediaQueue(actor=MODERATOR, page=PageRequest(limit=2))
    )
    second = await queries.list_media_queue(
        ListMediaQueue(
            actor=MODERATOR, page=PageRequest(limit=2, cursor=first.next_cursor)
        )
    )

    assert [item.id for item in first.items] == [assets[0].id, assets[1].id]
    assert [item.id for item in second.items] == [assets[2].id]
    assert second.next_cursor is None


async def test_list_media_queue_resolves_report_of_asset_uploaded_before_it() -> None:
    harness = Harness()
    asset = await harness.uploaded()

    page = await _queries(harness, {asset.id: REPORT_ID}).list_media_queue(
        ListMediaQueue(actor=MODERATOR)
    )

    assert asset.report_id is None
    assert page.items[0].report_id == REPORT_ID


@pytest.mark.parametrize("actor", [Actor.anonymous(), OWNER, OTHER_CITIZEN])
async def test_list_media_queue_non_moderator_is_refused(actor: Actor) -> None:
    harness = Harness()
    await harness.uploaded()

    with pytest.raises(PermissionDeniedError):
        await _queries(harness).list_media_queue(ListMediaQueue(actor=actor))


async def test_list_media_queue_invalid_cursor_is_refused() -> None:
    harness = Harness()

    with pytest.raises(ValidationError):
        await _queries(harness).list_media_queue(
            ListMediaQueue(
                actor=MODERATOR, page=PageRequest(limit=2, cursor="not-a-cursor")
            )
        )
