"""Unit tests for the impacts facade."""

from yakhnama.modules.impacts import public


def test_public_facade_exports_every_listed_name() -> None:
    missing = [name for name in public.__all__ if not hasattr(public, name)]

    assert missing == []
