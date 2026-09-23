"""Unit tests for ``yakhnama.modules.media.domain.errors``."""

import pytest

from tests.factories.base import FACTORY_IDS
from yakhnama.modules.media.domain.errors import (
    InfectedMediaError,
    InvalidModerationDecisionError,
    InvalidScanVerdictError,
    MediaAssetNotFoundError,
    MediaNotPublishableError,
    MediaUploadNotCompletedError,
    MediaUploadNotPendingError,
)
from yakhnama.shared_kernel.errors import (
    InvalidTransitionError,
    NotFoundError,
    ValidationError,
    YakhnamaError,
)

ASSET_ID = FACTORY_IDS.new_id()


@pytest.mark.parametrize(
    ("error", "family"),
    [
        (MediaAssetNotFoundError.for_id(ASSET_ID), NotFoundError),
        (
            MediaUploadNotPendingError.for_asset(ASSET_ID, "completed"),
            InvalidTransitionError,
        ),
        (
            MediaUploadNotCompletedError.for_asset(ASSET_ID, "requested"),
            InvalidTransitionError,
        ),
        (
            MediaNotPublishableError.for_asset(ASSET_ID, "pending", "approved"),
            InvalidTransitionError,
        ),
        (InfectedMediaError.for_asset(ASSET_ID), InvalidTransitionError),
        (InvalidScanVerdictError.for_asset(ASSET_ID), ValidationError),
        (
            InvalidModerationDecisionError.for_asset(ASSET_ID, "a fixed message"),
            ValidationError,
        ),
    ],
    ids=lambda value: type(value).__name__,
)
def test_media_error_builders_set_family_and_asset_id(
    error: YakhnamaError, family: type[YakhnamaError]
) -> None:
    details = dict(error.details)

    assert isinstance(error, family)
    assert details["media_id"] == str(ASSET_ID)
    assert str(ASSET_ID) not in error.message


def test_media_not_publishable_details_carry_both_statuses() -> None:
    error = MediaNotPublishableError.for_asset(ASSET_ID, "pending", "approved")

    details = dict(error.details)

    assert details["scan_status"] == "pending"
    assert details["moderation_status"] == "approved"
