"""Unit tests for the events facade."""

from yakhnama.modules.events import public


def test_public_facade_exports_every_listed_name() -> None:
    missing = [name for name in public.__all__ if not hasattr(public, name)]

    assert missing == []
