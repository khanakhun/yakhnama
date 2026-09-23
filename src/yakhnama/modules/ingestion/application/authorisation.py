"""Who may change the ingestion catalog and start runs (**proposed** rules).

- **Catalog and runs.** Registering, deprecating and retiring datasets, recording
  versions, cataloguing rasters, loading the reference file and requesting runs
  are platform administration: ``IsAdmin`` only. Ingested data is trusted by the
  platform once stored, so who may bring it in is kept narrow; a "curator" role is
  an open question.
- **Executing a run.** ``ExecuteIngestionRun`` has no actor: it is the system task
  ``ingestion.run``, enqueued by ``RunIngestion`` and never routed from the API.
- **Reads.** The catalog, run reports, observations and rasters are open data read
  through ``IngestionQueryService`` without a policy, as ``CanReadVerifiedData``
  allows for verified data (open question: whether run reports stay public).

Patterns: Policy.
"""

from yakhnama.modules.identity.public import (
    AuthorisationPolicy,
    IsAdmin,
    require_allowed,
)

__all__ = ["catalog_policy", "require_allowed"]


def catalog_policy() -> AuthorisationPolicy:
    """Return the policy guarding every ingestion command with an actor.

    Returns:
        ``IsAdmin()``.
    """
    return IsAdmin()
