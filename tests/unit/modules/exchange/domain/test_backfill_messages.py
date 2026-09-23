"""Issue messages of the backfill contract never echo a cell (security review).

Every message is a fixed sentence chosen by the error's type; a random marker put
into any cell, in any of the shapes a value error could echo, must never appear in
a message.
"""

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.unit.modules.exchange.application.support import VALID_CELLS
from yakhnama.modules.exchange.domain.backfill import (
    BACKFILL_COLUMNS,
    ImportedEventDraft,
    claim_column,
)
from yakhnama.modules.exchange.domain.value_objects import RowIssue

# Upper-case letters and digits: every fixed message is lower case, so a marker can
# only appear in a message if a cell was echoed.
MARKERS = st.text(
    alphabet=st.sampled_from("ABCDEFGHJKLMNPQRSTUVWXYZ23456789"),
    min_size=10,
    max_size=16,
)


def _shapes(marker: str) -> list[str]:
    return [
        marker,
        f"12{marker}",
        f"-{marker}",
        f"2022-07-15T06:00:00{marker}",
        f"https://{marker}.example/{marker}",
        f"pk.gb.{marker.lower()}",
        json.dumps({"type": marker, "coordinates": [74.5, 36.5]}),
        json.dumps({"type": "Point", "coordinates": [marker, 36.5]}),
        json.dumps({"type": "Point", "coordinates": [74.5, 36.5], marker: 1}),
        json.dumps({"type": "Polygon", "coordinates": [[[marker]]]}),
        f"{{{marker}",
    ]


def _issues(cells: dict[str, str]) -> tuple[RowIssue, ...]:
    outcome = ImportedEventDraft.from_flat_row(1, cells)
    return () if isinstance(outcome, ImportedEventDraft) else outcome


@settings(max_examples=300)
@given(
    marker=MARKERS,
    column=st.sampled_from(BACKFILL_COLUMNS),
    shape=st.integers(min_value=0, max_value=10),
)
def test_from_flat_row_issue_messages_never_contain_a_cell_marker(
    marker: str, column: str, shape: int
) -> None:
    cells = VALID_CELLS | {column: _shapes(marker)[shape]}
    if column == "geometry":
        # A geometry replaces the point, so the point columns are cleared.
        cells |= {"longitude": "", "latitude": ""}

    issues = _issues(cells)

    for issue in issues:
        assert marker not in issue.message
        assert marker.lower() not in issue.message


@pytest.mark.parametrize(
    ("changes", "column"),
    [
        (
            {
                claim_column(1, "value_kind"): "measurement",
                claim_column(1, "unit"): "QZXWUNIT",
            },
            claim_column(1, "unit"),
        ),
        ({claim_column(1, "value_kind"): "QZXWKIND"}, claim_column(1, "value_kind")),
        ({"longitude": "999.25"}, "longitude"),
        ({"source_url": "ftp://QZXWHOST.example"}, "source_url"),
    ],
)
def test_from_flat_row_known_echoing_errors_get_fixed_messages(
    changes: dict[str, str], column: str
) -> None:
    issues = _issues(VALID_CELLS | changes)

    found = [issue for issue in issues if issue.field == column]
    assert found, issues
    for issue in issues:
        assert "QZXW" not in issue.message
        assert "999" not in issue.message
