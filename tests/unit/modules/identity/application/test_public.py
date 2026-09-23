"""Unit tests for the identity facade."""

from yakhnama.modules.identity import public


def test_public_exports_every_name_in_all() -> None:
    missing = [name for name in public.__all__ if not hasattr(public, name)]

    assert missing == []


def test_public_exports_what_other_modules_and_the_api_need() -> None:
    needed = {
        "Actor",
        "AuthorisationPolicy",
        "CanManageReferenceData",
        "EnsureUserFromPrincipal",
        "EnsureUserFromPrincipalHandler",
        "IdentityQueryService",
        "require_allowed",
    }

    assert needed <= set(public.__all__)
