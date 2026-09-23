"""Unit tests for the ETag and ``If-Match`` helpers."""

from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from starlette.requests import Request
from starlette.responses import Response

from yakhnama.platform.etag import (
    expected_version_from_if_match,
    if_match_matches,
    make_etag,
    parse_if_match,
    require_if_match,
    set_etag,
)
from yakhnama.shared_kernel.errors import (
    PreconditionFailedError,
    PreconditionRequiredError,
)

ENTITY_ID = UUID("0192f4c1-0000-7000-8000-000000000001")
OTHER_ID = UUID("0192f4c1-0000-7000-8000-000000000002")
# A digit to str.isdigit but not an ASCII version number.
ARABIC_INDIC_ONE = "\u0661"


def _request(if_match: str | None) -> Request:
    headers = [] if if_match is None else [(b"if-match", if_match.encode())]
    return Request({"type": "http", "method": "PATCH", "headers": headers})


def test_make_etag_is_quoted_id_and_version() -> None:
    etag = make_etag(3, ENTITY_ID)

    assert etag == f'"{ENTITY_ID}:3"'


def test_make_etag_negative_version_raises() -> None:
    with pytest.raises(ValueError, match="negative"):
        make_etag(-1, ENTITY_ID)


@given(version=st.integers(min_value=0, max_value=10**18 - 1))
def test_expected_version_round_trips_make_etag(version: int) -> None:
    etag = make_etag(version, ENTITY_ID)

    result = expected_version_from_if_match(etag, ENTITY_ID)

    assert result == version


def test_set_etag_sets_header() -> None:
    response = Response()

    set_etag(response, make_etag(1, ENTITY_ID))

    assert response.headers["etag"] == f'"{ENTITY_ID}:1"'


def test_parse_if_match_splits_and_trims_tags() -> None:
    tags = parse_if_match(' "a:1" , W/"b:2",, ')

    assert tags == ('"a:1"', 'W/"b:2"')


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (f'"{ENTITY_ID}:2"', True),
        (f'"x", "{ENTITY_ID}:2"', True),
        ("*", True),
        (f'W/"{ENTITY_ID}:2"', False),
        (f'"{ENTITY_ID}:1"', False),
        (f'"{OTHER_ID}:2"', False),
    ],
)
def test_if_match_matches_uses_strong_comparison(
    header: str, *, expected: bool
) -> None:
    result = if_match_matches(header, make_etag(2, ENTITY_ID))

    assert result is expected


def test_require_if_match_matching_header_passes() -> None:
    require_if_match(_request(make_etag(2, ENTITY_ID)), make_etag(2, ENTITY_ID))


@pytest.mark.parametrize("header", [None, "", "   "])
def test_require_if_match_missing_header_raises_precondition_required(
    header: str | None,
) -> None:
    with pytest.raises(PreconditionRequiredError):
        require_if_match(_request(header), make_etag(2, ENTITY_ID))


def test_require_if_match_stale_header_raises_precondition_failed() -> None:
    with pytest.raises(PreconditionFailedError):
        require_if_match(_request(make_etag(1, ENTITY_ID)), make_etag(2, ENTITY_ID))


@pytest.mark.parametrize("header", [None, " "])
def test_expected_version_missing_header_raises_precondition_required(
    header: str | None,
) -> None:
    with pytest.raises(PreconditionRequiredError):
        expected_version_from_if_match(header, ENTITY_ID)


@pytest.mark.parametrize(
    "header",
    [
        "*",
        f'"{ENTITY_ID}:1", "{ENTITY_ID}:2"',
        f'W/"{ENTITY_ID}:1"',
        f'"{OTHER_ID}:1"',
        f'"{ENTITY_ID}:"',
        f'"{ENTITY_ID}:-1"',
        f'"{ENTITY_ID}:1x"',
        f'"{ENTITY_ID}:{ARABIC_INDIC_ONE}"',
        f"{ENTITY_ID}:1",
        f'"{ENTITY_ID}:{"9" * 19}"',
    ],
)
def test_expected_version_unusable_header_raises_precondition_failed(
    header: str,
) -> None:
    with pytest.raises(PreconditionFailedError):
        expected_version_from_if_match(header, ENTITY_ID)
