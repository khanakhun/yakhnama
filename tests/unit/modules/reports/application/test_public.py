"""Unit tests for the ``reports`` facade."""

import importlib

import pytest

FACADE = importlib.import_module("yakhnama.modules.reports.public")


@pytest.mark.parametrize("name", FACADE.__all__)
def test_reports_public_exported_name_resolves(name: str) -> None:
    exported = getattr(FACADE, name)

    assert exported is not None


def test_reports_public_all_has_no_duplicates() -> None:
    names = list(FACADE.__all__)

    assert len(names) == len(set(names))
