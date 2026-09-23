"""Write requests accepted by the exchange command handlers.

``RequestExport``, ``CancelExport`` and ``RequestImport`` come from the API with the
caller as ``actor``; ``RunExport`` and ``RunImport`` are internal, enqueued as the
``exchange.run_export`` and ``exchange.run_import`` tasks, carry only the job id and
rebuild the requesting actor when they run.

Patterns: Command.
"""

from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from yakhnama.modules.exchange.application.ports import IMPORT_KEY_PREFIX
from yakhnama.modules.exchange.domain.value_objects import (
    ArtifactRef,
    ExportDataset,
    ExportFilters,
    ExportFormat,
    ImportFormat,
)
from yakhnama.modules.identity.public import Actor
from yakhnama.shared_kernel.ids import EntityId

INLINE_IMPORT_MAX_BYTES: Final = 5 * 1024 * 1024
"""Largest file a moderator may send inline with an import request: 5 MiB
(**proposed**, Phase 4 plan T3a); larger files are uploaded to storage first."""


class RequestExport(BaseModel):
    """Queue an export of one dataset in one format.

    Implements: Command.

    Attributes:
        actor: The requesting user; ``events`` and ``claims`` need an
            authenticated user, ``reports`` a moderator (**proposed**).
        dataset: Which dataset.
        format: Which file format.
        filters: Which rows to select.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    dataset: ExportDataset
    format: ExportFormat
    filters: ExportFilters = ExportFilters()


class RunExport(BaseModel):
    """Produce the file of a queued export.

    Internal: the ``exchange.run_export`` task; no endpoint accepts it.

    Implements: Command.

    Attributes:
        job_id: The export job.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: EntityId


class CancelExport(BaseModel):
    """Cancel an export no worker has started.

    Implements: Command.

    Attributes:
        actor: The requesting user or a moderator.
        job_id: The export job.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    job_id: EntityId


class RequestImport(BaseModel):
    """Queue an import of historical events and their claims.

    Exactly one of ``artifact`` and ``inline_csv`` is given. An ``artifact`` is a
    file already uploaded under ``imports/`` with its declared size and digest,
    which the import checks before trusting the file; ``inline_csv`` is a small CSV
    file sent with the request, stored by the handler.

    Implements: Command.

    Attributes:
        actor: The requesting moderator.
        format: The file's format.
        artifact: The uploaded file, if it was uploaded first.
        inline_csv: The file's bytes, at most ``INLINE_IMPORT_MAX_BYTES``, only
            for the ``csv`` format.
        dry_run: ``True`` to validate only; no default, so a caller always says.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    format: ImportFormat
    artifact: ArtifactRef | None = None
    inline_csv: bytes | None = Field(
        default=None, min_length=1, max_length=INLINE_IMPORT_MAX_BYTES
    )
    dry_run: bool

    @model_validator(mode="after")
    def _check_file(self) -> Self:
        if (self.artifact is None) == (self.inline_csv is None):
            message = "give exactly one of artifact and inline_csv"
            raise ValueError(message)
        if self.inline_csv is not None and self.format is not ImportFormat.CSV:
            message = "inline_csv is accepted only for the csv format"
            raise ValueError(message)
        if self.artifact is not None and not self.artifact.object_key.startswith(
            IMPORT_KEY_PREFIX
        ):
            message = f"an import file must be stored under {IMPORT_KEY_PREFIX}"
            raise ValueError(message)
        return self


class RunImport(BaseModel):
    """Validate, and unless it is a dry run write, the file of a queued import.

    Internal: the ``exchange.run_import`` task; no endpoint accepts it.

    Implements: Command.

    Attributes:
        job_id: The import job.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: EntityId
