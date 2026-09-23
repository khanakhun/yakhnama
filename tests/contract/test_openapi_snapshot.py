"""The committed OpenAPI document is the API contract; the app must match it.

A failure means the HTTP API changed. Regenerate the snapshot with
``poetry run poe openapi-snapshot`` and review the diff line by line: a removed
path or field, or a narrowed type, is a breaking change that needs an ADR.
"""

import difflib
import json
from pathlib import Path
from typing import Final

from yakhnama.platform.openapi_snapshot import render_openapi_snapshot

SNAPSHOT: Final = Path(__file__).parent / "openapi.json"
DIFF_CONTEXT_LINES: Final = 3
MAX_DIFF_LINES: Final = 60
REGENERATE_HINT: Final = (
    "The OpenAPI document no longer matches tests/contract/openapi.json. Run "
    "`poetry run poe openapi-snapshot`, review the diff (removals and narrowed types "
    "are breaking changes and need an ADR) and commit the updated snapshot."
)


def test_openapi_snapshot_matches_the_app() -> None:
    committed = SNAPSHOT.read_text(encoding="utf-8")

    current = render_openapi_snapshot()

    diff = list(
        difflib.unified_diff(
            committed.splitlines(),
            current.splitlines(),
            "committed",
            "current",
            n=DIFF_CONTEXT_LINES,
            lineterm="",
        )
    )
    assert current == committed, "\n".join([REGENERATE_HINT, *diff[:MAX_DIFF_LINES]])


def test_openapi_snapshot_is_versioned_under_api_v1() -> None:
    document = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    api_paths = [path for path in document["paths"] if not path.startswith("/health/")]

    assert api_paths
    assert all(path.startswith("/api/v1/") for path in api_paths)
    assert document["components"]["securitySchemes"]["bearerAuth"]["scheme"] == (
        "bearer"
    )
