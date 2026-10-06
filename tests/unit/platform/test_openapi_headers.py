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
