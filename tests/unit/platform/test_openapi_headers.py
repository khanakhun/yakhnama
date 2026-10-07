"""Unit tests for ``yakhnama.platform.openapi_headers``."""

from typing import Any

import pytest

from yakhnama.platform.openapi_headers import (
    ETAG,
    IDEMPOTENT_REPLAYED,
    LINK,
    LOCATION,
    RETRY_AFTER,
    declare_middleware_headers,
    header_responses,
)


def _document() -> dict[str, Any]:
    return {
        "paths": {
            "/api/v1/things": {
                "post": {
                    "security": [{"bearerAuth": []}],
                    "responses": {
                        "201": {"description": "Created"},
                        "409": {"description": "Conflict"},
                        "429": {"description": "Too many"},
                    },
                },
                "get": {
                    "security": [{"bearerAuth": []}],
                    "responses": {"200": {"description": "OK"}, "409": {}},
                },
            },
            "/api/v1/guest": {
                "post": {
                    "responses": {"200": {"description": "OK"}, "409": {}},
                },
            },
        }
    }


def test_header_responses_declares_each_named_header_on_one_status() -> None:
    responses = header_responses(201, LOCATION, ETAG)

    assert list(responses) == [201]
    assert set(responses[201]["headers"]) == {LOCATION, ETAG}
    assert responses[201]["headers"][ETAG]["schema"] == {"type": "string"}


def test_header_responses_unknown_header_raises_key_error() -> None:
    with pytest.raises(KeyError):
        header_responses(200, "X-Unknown")


def test_declare_middleware_headers_adds_retry_after_and_replay_marker() -> None:
    document = declare_middleware_headers(_document())

    things = document["paths"]["/api/v1/things"]
    post = things["post"]["responses"]
    assert set(post["201"]["headers"]) == {IDEMPOTENT_REPLAYED}
    assert set(post["409"]["headers"]) == {RETRY_AFTER}
    assert set(post["429"]["headers"]) == {RETRY_AFTER}
    # A GET is never replayed and never meets idempotency-key-in-use.
    assert "headers" not in things["get"]["responses"]["200"]
    assert "headers" not in things["get"]["responses"]["409"]
    # An anonymous POST is not handled by the idempotency middleware.
    guest = document["paths"]["/api/v1/guest"]["post"]["responses"]
    assert "headers" not in guest["200"]
    assert "headers" not in guest["409"]


def test_declare_middleware_headers_keeps_route_headers() -> None:
    document = _document()
    document["paths"]["/api/v1/things"]["post"]["responses"]["201"]["headers"] = {
        LINK: {"schema": {"type": "string"}}
    }

    declare_middleware_headers(document)

    headers = document["paths"]["/api/v1/things"]["post"]["responses"]["201"]["headers"]
    assert set(headers) == {LINK, IDEMPOTENT_REPLAYED}


def test_declare_middleware_headers_accepts_a_document_without_paths() -> None:
    assert declare_middleware_headers({"openapi": "3.1.0"}) == {"openapi": "3.1.0"}


def test_declare_middleware_headers_retry_after_on_409_names_key_in_use() -> None:
    document = declare_middleware_headers(_document())

    post = document["paths"]["/api/v1/things"]["post"]["responses"]
    conflict_retry = post["409"]["headers"][RETRY_AFTER]
    assert "idempotency-key-in-use" in conflict_retry["description"]
    assert (
        "idempotency-key-in-use"
        not in (post["429"]["headers"][RETRY_AFTER]["description"])
    )


def test_declare_middleware_headers_adds_idempotency_problems_to_posts() -> None:
    document: dict[str, Any] = {
        "paths": {
            "/api/v1/marks": {
                "post": {
                    "security": [{"bearerAuth": []}],
                    "responses": {"200": {"description": "OK"}},
                },
            },
            "/api/v1/guest": {"post": {"responses": {"200": {"description": "OK"}}}},
        }
    }

    declare_middleware_headers(document)

    marks = document["paths"]["/api/v1/marks"]["post"]["responses"]
    assert set(marks) == {"200", "400", "409"}
    for status_code in ("400", "409"):
        assert marks[status_code]["content"] == {"application/problem+json": {}}
    assert "invalid-idempotency-key" in marks["400"]["description"]
    assert set(marks["409"]["headers"]) == {RETRY_AFTER}
    assert set(document["paths"]["/api/v1/guest"]["post"]["responses"]) == {"200"}


def test_declare_middleware_headers_keeps_a_routes_own_409_and_400() -> None:
    document = _document()
    responses = document["paths"]["/api/v1/things"]["post"]["responses"]
    responses["400"] = {"description": "Own 400"}

    declare_middleware_headers(document)

    assert responses["409"]["description"] == "Conflict"
    assert responses["400"] == {"description": "Own 400"}
