"""Authorisation of the geography write side, through the identity facade.

Every geography command handler receives an ``AuthorisationPolicy`` from the
composition root and asks it about the command's ``actor`` before reading or
staging anything, through ``require_allowed``. The policy that guards changes to
the place hierarchy is ``CanManageReferenceData`` (platform administrators only);
``reference_data_policy`` returns it so the composition root does not repeat the
choice. Handlers deny by default: only what the policy explicitly allows passes.

Patterns: Policy.
"""

from yakhnama.modules.identity.public import (
    AuthorisationPolicy,
    CanManageReferenceData,
    require_allowed,
)

__all__ = ["AuthorisationPolicy", "reference_data_policy", "require_allowed"]


def reference_data_policy() -> AuthorisationPolicy:
    """Return the policy guarding every change to the places.

    Returns:
        ``CanManageReferenceData()``.
    """
    return CanManageReferenceData()
