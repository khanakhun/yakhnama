"""Unit tests for ``yakhnama.shared_kernel.pagination``."""

import base64
import string
from uuid import UUID

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.pagination import (
    DEFAULT_PAGE_LIMIT,
    MAX_CURSOR_LENGTH,
    MAX_PAGE_LIMIT,
    SORT_KEY_MAX_LENGTH,
    CursorDirection,
    CursorPayload,
    Page,
    PageRequest,
    decode_cursor,
    encode_cursor,
)

# RFC 9562 appendix A.6 example: a well-formed UUIDv7.
LAST_ID = UUID("017f22e2-79b0-7cc3-98c4-dc0c0c07398f")


def _as_uuid7(identifier: UUID) -> UUID:
    # Force the version nibble to 7 and the variant bits to 0b10.
    value = identifier.int & ~(0xF << 76) & ~(0b11 << 62)
    return UUID(int=value | (0x7 << 76) | (0b10 << 62))


payloads = st.builds(
    CursorPayload,
    sort_key=st.text(max_size=SORT_KEY_MAX_LENGTH),
    last_id=st.uuids().map(_as_uuid7),
    direction=st.sampled_from(CursorDirection),
)


BASE64URL_CHARACTERS = (
    string.ascii_uppercase + string.ascii_lowercase + string.digits + "-_"
)


def _payload_or_none(sort_key: str) -> CursorPayload | None:
    try:
        return CursorPayload(sort_key=sort_key, last_id=LAST_ID)
    except PydanticValidationError:
        return None


def _decode_or_none(token: str) -> CursorPayload | None:
    try:
        return decode_cursor(token)
    except ValidationError:
        return None


def _base64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@given(payload=payloads)
def test_encode_cursor_decode_round_trip_returns_equal_payload(
    payload: CursorPayload,
) -> None:
    token = encode_cursor(payload)

    decoded = decode_cursor(token)

    assert decoded == payload
    assert len(token) <= MAX_CURSOR_LENGTH
    assert "=" not in token


@given(
    payload=payloads,
    position=st.integers(min_value=0),
    replacement=st.sampled_from(BASE64URL_CHARACTERS),
)
def test_decode_cursor_single_character_change_is_rejected_or_canonical(
    payload: CursorPayload, position: int, replacement: str
) -> None:
    # The cursor is unsigned (see the module docstring), so an edit may land on another
    # valid payload; what must never happen is a token that decodes although it is not
    # exactly the canonical encoding of what it decodes to.
    token = encode_cursor(payload)
    index = position % len(token)
    assume(token[index] != replacement)
    tampered = token[:index] + replacement + token[index + 1 :]

    decoded = _decode_or_none(tampered)

    assert decoded is None or (
        encode_cursor(decoded) == tampered and decoded != payload
    )


@given(
    payload=payloads,
    position=st.integers(min_value=0),
    intruder=st.characters().filter(lambda char: char not in BASE64URL_CHARACTERS),
)
def test_decode_cursor_character_outside_alphabet_raises_validation_error(
    payload: CursorPayload, position: int, intruder: str
) -> None:
    token = encode_cursor(payload)
    index = position % len(token)
    tampered = token[:index] + intruder + token[index + 1 :]

    with pytest.raises(ValidationError):
        decode_cursor(tampered)


@given(payload=payloads, cut=st.integers(min_value=1))
def test_decode_cursor_truncated_token_raises_validation_error(
    payload: CursorPayload, cut: int
) -> None:
    token = encode_cursor(payload)
    truncated = token[: len(token) - 1 - cut % len(token)]

    with pytest.raises(ValidationError):
        decode_cursor(truncated)


@pytest.mark.parametrize(
    "token",
    [
        "",
        "a" * (MAX_CURSOR_LENGTH + 1),
        "not a cursor!",
        "abc=",
        "a",
        _base64url(b"\xff\xfe"),
        _base64url(b"[]"),
        _base64url(b'{"sort_key": "a", "last_id": "not-a-uuid"}'),
        _base64url(
            b'{"sort_key":"a","last_id":"017f22e2-79b0-7cc3-98c4-dc0c0c07398f",'
            b'"direction":"forward","extra":1}'
        ),
    ],
)
def test_decode_cursor_malformed_token_raises_validation_error(token: str) -> None:
    with pytest.raises(ValidationError, match="cursor is invalid"):
        decode_cursor(token)


def test_decode_cursor_non_canonical_json_raises_validation_error() -> None:
    last_id = LAST_ID
    reordered = (
        f'{{"last_id":"{last_id}","sort_key":"a","direction":"forward"}}'
    ).encode()

    with pytest.raises(ValidationError):
        decode_cursor(_base64url(reordered))


def test_cursor_payload_default_direction_is_forward() -> None:
    payload = CursorPayload(sort_key="2026-09-23", last_id=LAST_ID)

    direction = payload.direction

    assert direction is CursorDirection.FORWARD


def test_cursor_payload_overlong_sort_key_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        CursorPayload(sort_key="x" * (SORT_KEY_MAX_LENGTH + 1), last_id=LAST_ID)


def test_page_request_defaults_first_page_of_default_size() -> None:
    request = PageRequest()

    decoded = request.decode_cursor()

    assert request.limit == DEFAULT_PAGE_LIMIT
    assert decoded is None


def test_page_request_with_cursor_decodes_it() -> None:
    payload = CursorPayload(sort_key="hunza", last_id=LAST_ID)
    request = PageRequest(limit=10, cursor=encode_cursor(payload))

    decoded = request.decode_cursor()

    assert decoded == payload


def test_page_request_invalid_cursor_raises_validation_error_on_decode() -> None:
    request = PageRequest(cursor="tampered")

    with pytest.raises(ValidationError):
        request.decode_cursor()


@given(limit=st.integers(min_value=1, max_value=MAX_PAGE_LIMIT))
def test_page_request_limit_within_bounds_is_accepted(limit: int) -> None:
    request = PageRequest(limit=limit)

    assert request.limit == limit


@given(
    limit=st.one_of(st.integers(max_value=0), st.integers(min_value=MAX_PAGE_LIMIT + 1))
)
def test_page_request_limit_out_of_bounds_raises_validation_error(limit: int) -> None:
    with pytest.raises(PydanticValidationError):
        PageRequest(limit=limit)


def test_page_holds_items_and_next_cursor() -> None:
    token = encode_cursor(CursorPayload(sort_key="b", last_id=LAST_ID))

    page = Page[int](items=(1, 2, 3), next_cursor=token)

    assert page.items == (1, 2, 3)
    assert page.next_cursor == token
    assert Page[int](items=()).next_cursor is None


def test_page_more_items_than_max_limit_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        Page[int](items=tuple(range(MAX_PAGE_LIMIT + 1)))


def test_cursor_payload_uuid4_last_id_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError, match="UUIDv7"):
        CursorPayload(
            sort_key="a", last_id=UUID("0b0a3e6e-0000-4000-8000-000000000000")
        )


@pytest.mark.parametrize("character", ["\x00", "语", '"', "a"])
def test_cursor_payload_maximal_sort_key_round_trips_or_fails_at_construction(
    character: str,
) -> None:
    sort_key = character * SORT_KEY_MAX_LENGTH

    payload = _payload_or_none(sort_key)

    if payload is None:
        with pytest.raises(PydanticValidationError, match="exceed"):
            CursorPayload(sort_key=sort_key, last_id=LAST_ID)
    else:
        token = encode_cursor(payload)
        assert len(token) <= MAX_CURSOR_LENGTH
        assert decode_cursor(token) == payload


@pytest.mark.parametrize(
    ("character", "fits"), [("\x00", False), ("语", False), ('"', True)]
)
def test_cursor_payload_escaped_or_wide_sort_key_outcome_is_pinned(
    character: str, *, fits: bool
) -> None:
    sort_key = character * SORT_KEY_MAX_LENGTH

    payload = _payload_or_none(sort_key)

    assert (payload is not None) is fits
