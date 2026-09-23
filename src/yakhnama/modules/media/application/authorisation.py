"""Who may upload, moderate and read media assets (**proposed** rules).

- **Upload** (request and complete). Any authenticated user for themselves; only
  the uploader completes their own upload (``IsSelf``). Attaching to a report
  needs the report to be the uploader's own.
- **Moderate.** ``CanModerate``.
- **Read.** Anyone (``CanReadVerifiedData``) once a public copy is published, and
  then only the public copy; the uploader and moderators also get the private
  original. Anyone else is told the asset does not exist.
- **Scan results.** ``RecordScanResult`` has no actor: it is a system task.

Patterns: Policy.
"""

from yakhnama.modules.identity.public import (
    ActorPolicy,
    CanModerate,
    CanReadVerifiedData,
    IsSelf,
    require_allowed,
)
from yakhnama.shared_kernel.ids import EntityId

__all__ = [
    "moderation_policy",
    "original_view_policy",
    "public_view_policy",
    "require_allowed",
    "uploader_policy",
]


def uploader_policy(owner_id: EntityId) -> ActorPolicy:
    """Return who may complete an upload: its uploader.

    Args:
        owner_id: The asset's owner.

    Returns:
        ``IsSelf(owner_id)``.
    """
    return IsSelf(owner_id)


def moderation_policy() -> ActorPolicy:
    """Return who may moderate media.

    Returns:
        ``CanModerate()``.
    """
    return CanModerate()


def original_view_policy(owner_id: EntityId) -> ActorPolicy:
    """Return who may download an asset's private original.

    Args:
        owner_id: The asset's owner.

    Returns:
        ``IsSelf(owner_id) | CanModerate()``.
    """
    return IsSelf(owner_id) | CanModerate()


def public_view_policy() -> ActorPolicy:
    """Return who may download a published public copy.

    Returns:
        ``CanReadVerifiedData()``: everyone.
    """
    return CanReadVerifiedData()
