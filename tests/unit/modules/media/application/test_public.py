"""Unit tests for the ``media`` facade."""

import importlib

import pytest

FACADE = importlib.import_module("yakhnama.modules.media.public")


@pytest.mark.parametrize("name", FACADE.__all__)
def test_media_public_exported_name_resolves(name: str) -> None:
    exported = getattr(FACADE, name)

    assert exported is not None


def test_media_public_all_has_no_duplicates() -> None:
    names = list(FACADE.__all__)

    assert len(names) == len(set(names))
