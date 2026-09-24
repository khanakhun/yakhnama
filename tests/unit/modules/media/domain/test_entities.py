"""Unit tests for ``yakhnama.modules.media.domain.entities``."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from tests.factories.base import FACTORY_IDS
from tests.factories.media import MediaAssetTestFactory, stored_file, synthetic_sha256
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.media.domain.entities import (
    INFECTED_QUARANTINE_REASON,
    MediaAsset,
)
from yakhnama.modules.media.domain.errors import (
    InfectedMediaError,
    InvalidModerationDecisionError,
    InvalidScanVerdictError,
    MediaNotPublishableError,
    MediaUploadNotCompletedError,
    MediaUploadNotPendingError,
)
from yakhnama.modules.media.domain.events import (
    MediaModerated,
    MediaPublished,
    MediaQuarantined,
    MediaScanned,
    UploadCompleted,
    UploadFailed,
)
from yakhnama.modules.media.domain.value_objects import (
    ExifFacts,
    MimeType,
    ModerationStatus,
    ScanStatus,
    SensitivityFlag,
    UploadStatus,
    public_object_key,
)
from yakhnama.shared_kernel.value_objects import Coordinates

CREATED_AT = datetime(2026, 9, 1, tzinfo=UTC)
CHANGED_AT = datetime(2026, 9, 2, tzinfo=UTC)
# A synthetic test point, not a real place.
EXIF_LOCATION = Coordinates(longitude=74.6, latitude=36.3)


def _clock() -> SteppingClock:
    return SteppingClock(CHANGED_AT, timedelta(seconds=1))


def _ids() -> SequentialIdGenerator:
    return SequentialIdGenerator(seed=11)


def _requested(**fields: object) -> MediaAsset:
    return MediaAssetTestFactory.build(
        factory_use_construct=False, **{"created_at": CREATED_AT, **fields}
    )


def _completed(asset: MediaAsset | None = None) -> MediaAsset:
    asset = _requested() if asset is None else asset
    return asset.complete_upload(stored_file(), clock=_clock(), ids=_ids()).state


def _clean() -> MediaAsset:
    return _completed().mark_scan(ScanStatus.CLEAN, clock=_clock(), ids=_ids()).state


def _approved(sensitivity: SensitivityFlag = SensitivityFlag.NONE) -> MediaAsset:
    return (
        _clean()
        .moderate(
            ModerationStatus.APPROVED, sensitivity, None, clock=_clock(), ids=_ids()
        )
        .state
    )


def _published() -> MediaAsset:
    asset = _approved()
    key = public_object_key(asset.id)
    return asset.publish_public_copy(key, clock=_clock(), ids=_ids()).state


# --------------------------------------------------------------------------- #
# Invariants                                                                  #
# --------------------------------------------------------------------------- #


def test_media_asset_from_factory_is_requested_and_unpublished() -> None:
    asset = _requested()

    assert asset.upload_status is UploadStatus.REQUESTED
    assert asset.is_published is False
    assert asset.is_publishable is False


def test_media_asset_with_offset_timestamps_normalises_to_utc() -> None:
    offset = CREATED_AT.astimezone(timezone(timedelta(hours=5)))

    asset = _requested(created_at=offset, updated_at=offset)

    assert asset.created_at.tzinfo is UTC


@pytest.mark.parametrize(
    "fields",
    [
        {"sha256": "a" * 64},
        {"byte_size": 10},
        {"exif": ExifFacts(camera="Test Camera")},
        {"scan_status": ScanStatus.CLEAN},
        {"moderation_status": ModerationStatus.APPROVED},
        {"upload_status": UploadStatus.COMPLETED},
        {"updated_at": CREATED_AT - timedelta(seconds=1)},
    ],
    ids=lambda fields: next(iter(fields)),
)
def test_media_asset_breaking_an_invariant_raises_validation_error(
    fields: dict[str, object],
) -> None:
    with pytest.raises(PydanticValidationError):
        _requested(**fields)


def test_media_asset_public_key_without_approval_raises_validation_error() -> None:
    asset = _clean()

    with pytest.raises(PydanticValidationError):
        MediaAsset.model_validate(
            {**asset.model_dump(), "public_key": public_object_key(asset.id)}
        )


def test_media_asset_rejected_without_reason_raises_validation_error() -> None:
    asset = _clean()

    with pytest.raises(PydanticValidationError):
        MediaAsset.model_validate(
            {**asset.model_dump(), "moderation_status": ModerationStatus.REJECTED}
        )


def test_media_asset_assignment_raises_validation_error() -> None:
    asset = _requested()

    with pytest.raises(PydanticValidationError):
        asset.public_key = "media/public/x"  # type: ignore[misc]  # reason: frozen check


# --------------------------------------------------------------------------- #
# Upload                                                                      #
# --------------------------------------------------------------------------- #


def test_media_asset_complete_upload_records_facts_and_emits_event() -> None:
    asset = _requested()
    exif = ExifFacts(location=EXIF_LOCATION)
    stored = stored_file(mime_type=MimeType.PNG, byte_size=2048, exif=exif)

    change = asset.complete_upload(stored, clock=_clock(), ids=_ids())

    state = change.state
    assert state.upload_status is UploadStatus.COMPLETED
    assert state.sha256 == stored.sha256
    assert state.byte_size == 2048
    assert state.mime_type is MimeType.PNG
    assert state.exif == exif
    assert state.version == asset.version + 1
    (event,) = change.events
    assert isinstance(event, UploadCompleted)
    assert event.has_exif is True
    assert event.byte_size == 2048
    payload = event.model_dump_json()
    assert str(EXIF_LOCATION.latitude) not in payload
    assert stored.sha256 not in payload
    assert "media/" not in payload


def test_media_asset_complete_upload_with_empty_exif_stores_none() -> None:
    stored = stored_file(exif=ExifFacts())

    change = _requested().complete_upload(stored, clock=_clock(), ids=_ids())

    assert change.state.exif is None
    assert change.events[0].model_dump()["has_exif"] is False


@pytest.mark.parametrize("status", [UploadStatus.COMPLETED, UploadStatus.FAILED])
def test_media_asset_complete_upload_twice_raises_not_pending(
    status: UploadStatus,
) -> None:
    asset = (
        _completed()
        if status is UploadStatus.COMPLETED
        else _requested().fail_upload(clock=_clock(), ids=_ids()).state
    )

    with pytest.raises(MediaUploadNotPendingError):
        asset.complete_upload(stored_file(), clock=_clock(), ids=_ids())


def test_media_asset_fail_upload_marks_failed_and_emits_event() -> None:
    change = _requested().fail_upload(clock=_clock(), ids=_ids())

    assert change.state.upload_status is UploadStatus.FAILED
    assert isinstance(change.events[0], UploadFailed)


def test_media_asset_fail_upload_after_completion_raises_not_pending() -> None:
    with pytest.raises(MediaUploadNotPendingError):
        _completed().fail_upload(clock=_clock(), ids=_ids())


# --------------------------------------------------------------------------- #
# Scan                                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", [ScanStatus.CLEAN, ScanStatus.UNAVAILABLE])
def test_media_asset_mark_scan_records_verdict_and_emits_event(
    status: ScanStatus,
) -> None:
    change = _completed().mark_scan(status, clock=_clock(), ids=_ids())

    assert change.state.scan_status is status
    (event,) = change.events
    assert isinstance(event, MediaScanned)
    assert event.scan_status is status


def test_media_asset_mark_scan_same_verdict_returns_unchanged() -> None:
    asset = _clean()

    change = asset.mark_scan(ScanStatus.CLEAN, clock=_clock(), ids=_ids())

    assert change.state is asset
    assert change.events == ()


def test_media_asset_mark_scan_pending_raises_invalid_verdict() -> None:
    with pytest.raises(InvalidScanVerdictError):
        _completed().mark_scan(ScanStatus.PENDING, clock=_clock(), ids=_ids())


def test_media_asset_mark_scan_before_completion_raises_not_completed() -> None:
    with pytest.raises(MediaUploadNotCompletedError):
        _requested().mark_scan(ScanStatus.CLEAN, clock=_clock(), ids=_ids())


def test_media_asset_mark_scan_infected_on_published_quarantines_and_withdraws() -> (
    None
):
    asset = _published()

    change = asset.mark_scan(ScanStatus.INFECTED, clock=_clock(), ids=_ids())

    state = change.state
    assert state.moderation_status is ModerationStatus.QUARANTINED
    assert state.moderation_reason == INFECTED_QUARANTINE_REASON
    assert state.public_key is None
    scanned, quarantined = change.events
    assert isinstance(scanned, MediaScanned)
    assert isinstance(quarantined, MediaQuarantined)
    assert quarantined.is_public_copy_withdrawn is True


def test_media_asset_mark_scan_infected_when_quarantined_emits_only_scan() -> None:
    asset = _completed().quarantine("Complaint.", clock=_clock(), ids=_ids()).state

    change = asset.mark_scan(ScanStatus.INFECTED, clock=_clock(), ids=_ids())

    assert change.state.moderation_reason == "Complaint."
    assert [type(event) for event in change.events] == [MediaScanned]


def test_media_asset_mark_scan_clean_after_infected_keeps_quarantine() -> None:
    asset = _completed().mark_scan(ScanStatus.INFECTED, clock=_clock(), ids=_ids())

    change = asset.state.mark_scan(ScanStatus.CLEAN, clock=_clock(), ids=_ids())

    assert change.state.moderation_status is ModerationStatus.QUARANTINED
    assert change.state.is_publishable is False


# --------------------------------------------------------------------------- #
# Moderation and quarantine                                                   #
# --------------------------------------------------------------------------- #


def test_media_asset_moderate_approve_records_decision_and_event() -> None:
    change = _clean().moderate(
        ModerationStatus.APPROVED,
        SensitivityFlag.OTHER,
        "  Looks fine.  ",
        clock=_clock(),
        ids=_ids(),
    )

    assert change.state.moderation_status is ModerationStatus.APPROVED
    assert change.state.sensitivity is SensitivityFlag.OTHER
    assert change.state.moderation_reason == "Looks fine."
    (event,) = change.events
    assert isinstance(event, MediaModerated)
    assert event.is_public_copy_withdrawn is False
    assert "Looks fine" not in event.model_dump_json()


def test_media_asset_moderate_same_decision_returns_unchanged() -> None:
    asset = _approved()

    change = asset.moderate(
        ModerationStatus.APPROVED,
        SensitivityFlag.NONE,
        None,
        clock=_clock(),
        ids=_ids(),
    )

    assert change.state is asset
    assert change.events == ()


def test_media_asset_moderate_reject_published_withdraws_public_copy() -> None:
    change = _published().moderate(
        ModerationStatus.REJECTED,
        SensitivityFlag.NONE,
        "Unrelated to the report.",
        clock=_clock(),
        ids=_ids(),
    )

    assert change.state.public_key is None
    assert change.events[0].model_dump()["is_public_copy_withdrawn"] is True


@pytest.mark.parametrize(
    "sensitivity",
    [SensitivityFlag.INJURED_OR_DECEASED, SensitivityFlag.IDENTIFIABLE_PEOPLE],
)
def test_media_asset_moderate_blocking_sensitivity_withdraws_public_copy(
    sensitivity: SensitivityFlag,
) -> None:
    change = _published().moderate(
        ModerationStatus.APPROVED, sensitivity, None, clock=_clock(), ids=_ids()
    )

    assert change.state.public_key is None
    assert change.state.is_publishable is False


@pytest.mark.parametrize(
    "status", [ModerationStatus.PENDING, ModerationStatus.QUARANTINED]
)
def test_media_asset_moderate_with_non_decision_raises_invalid_decision(
    status: ModerationStatus,
) -> None:
    with pytest.raises(InvalidModerationDecisionError):
        _clean().moderate(
            status, SensitivityFlag.NONE, "reason", clock=_clock(), ids=_ids()
        )


def test_media_asset_moderate_reject_without_reason_raises_invalid_decision() -> None:
    with pytest.raises(InvalidModerationDecisionError):
        _clean().moderate(
            ModerationStatus.REJECTED,
            SensitivityFlag.NONE,
            None,
            clock=_clock(),
            ids=_ids(),
        )


def test_media_asset_moderate_before_completion_raises_not_completed() -> None:
    with pytest.raises(MediaUploadNotCompletedError):
        _requested().moderate(
            ModerationStatus.APPROVED,
            SensitivityFlag.NONE,
            None,
            clock=_clock(),
            ids=_ids(),
        )


def test_media_asset_moderate_approve_infected_raises_infected_error() -> None:
    asset = _completed().mark_scan(ScanStatus.INFECTED, clock=_clock(), ids=_ids())

    with pytest.raises(InfectedMediaError):
        asset.state.moderate(
            ModerationStatus.APPROVED,
            SensitivityFlag.NONE,
            None,
            clock=_clock(),
            ids=_ids(),
        )


def test_media_asset_moderate_approve_lifts_manual_quarantine() -> None:
    asset = _clean().quarantine("Complaint.", clock=_clock(), ids=_ids()).state

    change = asset.moderate(
        ModerationStatus.APPROVED,
        SensitivityFlag.NONE,
        None,
        clock=_clock(),
        ids=_ids(),
    )

    assert change.state.moderation_status is ModerationStatus.APPROVED
    assert change.state.is_publishable is True


def test_media_asset_quarantine_published_withdraws_and_emits_event() -> None:
    change = _published().quarantine("Complaint.", clock=_clock(), ids=_ids())

    assert change.state.moderation_status is ModerationStatus.QUARANTINED
    assert change.state.public_key is None
    (event,) = change.events
    assert isinstance(event, MediaQuarantined)
    assert event.is_public_copy_withdrawn is True
    assert "Complaint" not in event.model_dump_json()


def test_media_asset_quarantine_twice_returns_unchanged() -> None:
    asset = _completed().quarantine("Complaint.", clock=_clock(), ids=_ids()).state

    change = asset.quarantine("Again.", clock=_clock(), ids=_ids())

    assert change.state is asset
    assert change.events == ()


def test_media_asset_quarantine_before_completion_raises_not_completed() -> None:
    with pytest.raises(MediaUploadNotCompletedError):
        _requested().quarantine("Complaint.", clock=_clock(), ids=_ids())


# --------------------------------------------------------------------------- #
# Publication                                                                 #
# --------------------------------------------------------------------------- #


def test_media_asset_publish_public_copy_records_key_and_emits_event() -> None:
    asset = _approved()

    change = asset.publish_public_copy(
        public_object_key(asset.id), clock=_clock(), ids=_ids()
    )

    assert change.state.public_key == public_object_key(asset.id)
    assert change.state.is_published is True
    (event,) = change.events
    assert isinstance(event, MediaPublished)
    assert "media/" not in event.model_dump_json()


def test_media_asset_publish_twice_returns_unchanged() -> None:
    asset = _published()

    change = asset.publish_public_copy(
        public_object_key(asset.id), clock=_clock(), ids=_ids()
    )

    assert change.state is asset
    assert change.events == ()


def test_media_asset_publish_with_original_key_raises_validation_error() -> None:
    asset = _approved()

    with pytest.raises(PydanticValidationError):
        asset.publish_public_copy(asset.original_key, clock=_clock(), ids=_ids())


@pytest.mark.parametrize(
    "asset_builder",
    [
        _requested,
        _completed,
        _clean,
        lambda: (
            _completed()
            .mark_scan(ScanStatus.UNAVAILABLE, clock=_clock(), ids=_ids())
            .state.moderate(
                ModerationStatus.APPROVED,
                SensitivityFlag.NONE,
                None,
                clock=_clock(),
                ids=_ids(),
            )
            .state
        ),
        lambda: _approved(SensitivityFlag.INJURED_OR_DECEASED),
    ],
    ids=["requested", "unscanned", "unmoderated", "scan-unavailable", "sensitive"],
)
def test_media_asset_publish_when_not_publishable_raises_not_publishable(
    asset_builder: Callable[[], MediaAsset],
) -> None:
    asset = asset_builder()

    with pytest.raises(MediaNotPublishableError):
        asset.publish_public_copy(
            public_object_key(asset.id), clock=_clock(), ids=_ids()
        )


# --------------------------------------------------------------------------- #
# Deduplication                                                               #
# --------------------------------------------------------------------------- #


def test_media_asset_is_duplicate_of_same_owner_and_digest_returns_true() -> None:
    owner_id, digest = FACTORY_IDS.new_id(), synthetic_sha256()
    first = _requested(owner_id=owner_id).complete_upload(
        stored_file(sha256=digest), clock=_clock(), ids=_ids()
    )
    second = _requested(owner_id=owner_id).complete_upload(
        stored_file(sha256=digest.upper()), clock=_clock(), ids=_ids()
    )

    is_duplicate = second.state.is_duplicate_of(first.state)

    assert is_duplicate is True


def test_media_asset_is_duplicate_of_other_owner_returns_false() -> None:
    digest = synthetic_sha256()
    first = (
        _requested()
        .complete_upload(stored_file(sha256=digest), clock=_clock(), ids=_ids())
        .state
    )
    second = (
        _requested()
        .complete_upload(stored_file(sha256=digest), clock=_clock(), ids=_ids())
        .state
    )

    is_duplicate = second.is_duplicate_of(first)

    assert is_duplicate is False


def test_media_asset_is_duplicate_of_itself_or_unknown_digest_returns_false() -> None:
    asset = _completed()
    pending = _requested(owner_id=asset.owner_id)

    results = (asset.is_duplicate_of(asset), pending.is_duplicate_of(pending))

    assert results == (False, False)


# --------------------------------------------------------------------------- #
# State machine property                                                      #
# --------------------------------------------------------------------------- #

_OPERATIONS = st.lists(
    st.sampled_from(
        [
            "complete",
            "fail",
            "scan_clean",
            "scan_infected",
            "scan_unavailable",
            "approve",
            "approve_sensitive",
            "reject",
            "quarantine",
            "publish",
        ]
    ),
    max_size=12,
)


def _apply(asset: MediaAsset, operation: str) -> MediaAsset:
    clock, ids = _clock(), _ids()
    actions = {
        "complete": lambda: asset.complete_upload(stored_file(), clock=clock, ids=ids),
        "fail": lambda: asset.fail_upload(clock=clock, ids=ids),
        "scan_clean": lambda: asset.mark_scan(ScanStatus.CLEAN, clock=clock, ids=ids),
        "scan_infected": lambda: asset.mark_scan(
            ScanStatus.INFECTED, clock=clock, ids=ids
        ),
        "scan_unavailable": lambda: asset.mark_scan(
            ScanStatus.UNAVAILABLE, clock=clock, ids=ids
        ),
        "approve": lambda: asset.moderate(
            ModerationStatus.APPROVED, SensitivityFlag.NONE, None, clock=clock, ids=ids
        ),
        "approve_sensitive": lambda: asset.moderate(
            ModerationStatus.APPROVED,
            SensitivityFlag.IDENTIFIABLE_PEOPLE,
            None,
            clock=clock,
            ids=ids,
        ),
        "reject": lambda: asset.moderate(
            ModerationStatus.REJECTED, SensitivityFlag.NONE, "No.", clock=clock, ids=ids
        ),
        "quarantine": lambda: asset.quarantine("Held.", clock=clock, ids=ids),
        "publish": lambda: asset.publish_public_copy(
            public_object_key(asset.id), clock=clock, ids=ids
        ),
    }
    try:
        return actions[operation]().state
    except (
        InfectedMediaError,
        MediaNotPublishableError,
        MediaUploadNotCompletedError,
        MediaUploadNotPendingError,
    ):
        return asset


@settings(max_examples=150, deadline=None)
@given(operations=_OPERATIONS)
def test_media_asset_any_operation_sequence_keeps_publication_safe(
    operations: list[str],
) -> None:
    asset = _requested()

    for operation in operations:
        asset = _apply(asset, operation)

    if asset.is_published:
        assert asset.scan_status is ScanStatus.CLEAN
        assert asset.moderation_status is ModerationStatus.APPROVED
        assert asset.sensitivity is SensitivityFlag.NONE
    if asset.scan_status is ScanStatus.INFECTED:
        assert asset.moderation_status is not ModerationStatus.APPROVED
        assert asset.is_published is False


@pytest.mark.parametrize("mime_type", [MimeType.MP4, MimeType.PDF])
def test_media_asset_approved_clean_video_or_pdf_is_never_publishable(
    mime_type: MimeType,
) -> None:
    completed = _requested().complete_upload(
        stored_file(mime_type=mime_type), clock=_clock(), ids=_ids()
    )
    asset = (
        completed.state.mark_scan(ScanStatus.CLEAN, clock=_clock(), ids=_ids())
        .state.moderate(
            ModerationStatus.APPROVED,
            SensitivityFlag.NONE,
            None,
            clock=_clock(),
            ids=_ids(),
        )
        .state
    )

    assert asset.moderation_status is ModerationStatus.APPROVED
    assert not asset.is_publishable
    with pytest.raises(MediaNotPublishableError):
        asset.publish_public_copy(
            public_object_key(asset.id), clock=_clock(), ids=_ids()
        )


@pytest.mark.parametrize("mime_type", [MimeType.MP4, MimeType.PDF])
def test_media_asset_video_or_pdf_with_public_key_violates_invariant(
    mime_type: MimeType,
) -> None:
    published = _published()

    with pytest.raises(PydanticValidationError, match="publishable"):
        MediaAsset.model_validate({**published.model_dump(), "mime_type": mime_type})
