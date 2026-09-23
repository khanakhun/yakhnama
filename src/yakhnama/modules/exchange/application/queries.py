"""Read requests accepted by the exchange query service.

Patterns: Query.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.identity.public import Actor
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import PageRequest


class GetExportJob(BaseModel):
    """Ask for one export job.

    Implements: Query.

    Attributes:
        actor: Who asks; only the requesting user and moderators see a job.
        job_id: The job.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    job_id: EntityId


class ListExportJobs(BaseModel):
    """Ask for one page of export jobs, newest first.

    Implements: Query.

    Attributes:
        actor: Who asks; a moderator sees every job, anyone else their own.
        page: Page size and cursor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    page: PageRequest = PageRequest()


class GetImportJob(BaseModel):
    """Ask for one import job with its validation report.

    Implements: Query.

    Attributes:
        actor: Who asks; moderators only.
        job_id: The job.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    job_id: EntityId
