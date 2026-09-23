"""Unit tests for ``yakhnama.modules.media.domain.value_objects``."""

import re
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from tests.factories.base import FACTORY_IDS
from yakhnama.modules.media.domain.value_objects import (
    CAMERA_MAX_LENGTH,
    MAX_MEDIA_BYTES,
    ByteSize,
    ExifFacts,
    MediaAttribution,
    MimeType,
    ObjectKey,
    Sha256,
    StoredFile,
    original_object_key,
    public_object_key,
)
from yakhnama.shared_kernel.value_objects import (
    Coordinates,
    DatePrecision,
    DateWithPrecision,
)

_OBJECT_KEY: TypeAdapter[str] = TypeAdapter(ObjectKey)
_SHA256: TypeAdapter[str] = TypeAdapter(Sha256)
_BYTE_SIZE: TypeAdapter[int] = TypeAdapter(ByteSize)

_KEY_TEXT = st.from_regex(re.compile(r"[a-z0-9][a-z0-9/_-]{3,120}"), fullmatch=True)


@given(key=_KEY_TEXT)
def test_object_key_matching_pattern_is_accepted(key: str) -> None:
    result = _OBJECT_KEY.validate_python(key)

    assert result == key


@pytest.mark.parametrize(
    "key",
    [
        "abc",
        "Media/original/x",
        "/media/original/x",
        "media/../secret",
        "media/original/a..b",
        "media original",
        "a" * 257,
    ],
)
def test_object_key_invalid_raises_validation_error(key: str) -> None:
    with pytest.raises(PydanticValidationError):
        _OBJECT_KEY.validate_python(key)


@given(prefix=_KEY_TEXT, suffix=_KEY_TEXT)
def test_object_key_with_parent_segment_is_always_rejected(
    prefix: str, suffix: str
) -> None:
    with pytest.raises(PydanticValidationError):
        _OBJECT_KEY.validate_python(f"{prefix}/../{suffix}")


def test_variant_object_keys_are_valid_distinct_and_derived_from_id() -> None:
    asset_id = FACTORY_IDS.new_id()

    original, public = original_object_key(asset_id), public_object_key(asset_id)

    assert original == f"media/original/{asset_id}"
    assert public == f"media/public/{asset_id}"
    assert _OBJECT_KEY.validate_python(original) == original
    assert _OBJECT_KEY.validate_python(public) == public


@given(digest=st.binary(min_size=32, max_size=32))
def test_sha256_in_upper_case_is_folded_to_lower_case(digest: bytes) -> None:
    upper = digest.hex().upper()

    result = _SHA256.validate_python(upper)

    assert result == digest.hex()


@pytest.mark.parametrize("value", ["", "a" * 63, "a" * 65, "g" * 64, 12])
def test_sha256_malformed_raises_validation_error(value: object) -> None:
    with pytest.raises(PydanticValidationError):
        _SHA256.validate_python(value)


@given(size=st.integers(min_value=1, max_value=MAX_MEDIA_BYTES))
def test_byte_size_within_bounds_is_accepted(size: int) -> None:
    result = _BYTE_SIZE.validate_python(size)

    assert result == size


@pytest.mark.parametrize("size", [0, -1, MAX_MEDIA_BYTES + 1])
def test_byte_size_out_of_bounds_raises_validation_error(size: int) -> None:
    with pytest.raises(PydanticValidationError):
        _BYTE_SIZE.validate_python(size)


def test_mime_type_outside_allow_list_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        StoredFile.model_validate(
            {"sha256": "a" * 64, "byte_size": 1, "mime_type": "image/svg+xml"}
        )


def test_mime_type_allow_list_is_the_proposed_five_types() -> None:
    values = {mime_type.value for mime_type in MimeType}

    assert values == {
        "image/jpeg",
        "image/png",
        "image/webp",
        "video/mp4",
        "application/pdf",
    }


def test_exif_facts_without_values_is_empty() -> None:
    facts = ExifFacts()

    assert facts.is_empty is True


@pytest.mark.parametrize(
    "fields",
    [
        {
            "taken_at": DateWithPrecision(
                value=datetime(2026, 9, 1, tzinfo=UTC), precision=DatePrecision.EXACT
            )
        },
        {"location": Coordinates(longitude=74.6, latitude=36.3)},
        {"camera": "Test Camera 1"},
    ],
    ids=lambda fields: next(iter(fields)),
)
def test_exif_facts_with_any_value_is_not_empty(fields: dict[str, object]) -> None:
    facts = ExifFacts.model_validate(fields)

    assert facts.is_empty is False


@pytest.mark.parametrize(
    "camera", ["x" * (CAMERA_MAX_LENGTH + 1), "cam" + chr(0) + "era", " "]
)
def test_exif_facts_with_invalid_camera_raises_validation_error(camera: str) -> None:
    with pytest.raises(PydanticValidationError):
        ExifFacts(camera=camera)


def test_media_attribution_without_report_defaults_to_none() -> None:
    attribution = MediaAttribution(
        owner_id=FACTORY_IDS.new_id(), source_id=FACTORY_IDS.new_id()
    )

    assert attribution.report_id is None
