"""Request bodies, query strings and responses of the exchange HTTP API.

Request bodies reuse the domain's value objects (``ExportFilters``, the format and
dataset codes, ``Sha256``) so the API and the commands agree on every bound.
Responses are the application's DTOs, flattened where the API adds a field (the
download link of a completed export).

An import file is uploaded first (``POST /moderation/imports/uploads``) to a key
the server chooses, ``imports/<upload_id>/source<extension>``; the import request
then names exactly that key, so a client can never point an import at an export,
a media object or a key of its own invention.

Patterns: API Schema.
"""

import re
from typing import Annotated, Final, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from yakhnama.modules.exchange.domain.value_objects import MediaTypeName, Sha256
from yakhnama.modules.exchange.public import (
    DEFAULT_FORMAT_REGISTRY,
    IMPORT_KEY_PREFIX,
    ExportDataset,
    ExportFilters,
    ExportFormat,
    ExportJobDetail,
    ExportJobSummary,
    ImportFormat,
)
from yakhnama.modules.media.public import HttpHeader
from yakhnama.shared_kernel.pagination import (
    DEFAULT_PAGE_LIMIT,
    MAX_CURSOR_LENGTH,
    MAX_PAGE_LIMIT,
)

IMPORT_UPLOAD_MAX_BYTES: Final = 50 * 1024 * 1024
"""Largest import file a presigned upload accepts: 50 MiB (**proposed**).

An import holds at most ``IMPORT_MAX_ROWS`` (10 000) rows; 50 MiB leaves about
5 KiB per row, generous for the backfill columns, while keeping a worker's
validation pass short."""

PRESIGNED_URL_MAX_LENGTH: Final = 4096
UPLOAD_HEADERS_MAX: Final = 16

_UUID_PATTERN: Final = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_IMPORT_EXTENSIONS: Final = tuple(
    DEFAULT_FORMAT_REGISTRY.import_descriptor(code.value).extension
    for code in ImportFormat
)
IMPORT_UPLOAD_KEY_PATTERN: Final = (
    rf"^{re.escape(IMPORT_KEY_PREFIX)}{_UUID_PATTERN}/source"
    rf"(?:{'|'.join(re.escape(extension) for extension in _IMPORT_EXTENSIONS)})$"
)
"""Exactly the keys ``POST /moderation/imports/uploads`` grants."""

ImportUploadKey = Annotated[
    str, StringConstraints(max_length=256, pattern=IMPORT_UPLOAD_KEY_PATTERN)
]


class RequestExportRequest(BaseModel):
    """Body of ``POST /api/v1/exports``.

    Implements: API Schema.

    Attributes:
        dataset: ``events``, ``claims`` or ``reports`` (moderators only).
        format: ``json``, ``geojson``, ``csv`` or ``geoparquet``.
        filters: Which rows to select; every filter is optional.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset: ExportDataset
    format: ExportFormat
    filters: ExportFilters = ExportFilters()


class ExportJobResponse(ExportJobDetail):
    """One export job, with a short-lived download link once completed.

    The metadata sidecar (licence, citation, filters, row count, checksum) is
    inline in ``sidecar`` once the job is completed.

    Implements: API Schema.

    Attributes:
        download_url: Presigned link to the file, only while ``status`` is
            ``completed``; ask again for a fresh one when it expires.
    """

    download_url: (
        Annotated[str, StringConstraints(min_length=1, max_length=4096)] | None
    ) = None


class ListExportJobsParameters(BaseModel):
    """Query string of ``GET /api/v1/exports``.

    Implements: API Schema.

    Attributes:
        cursor: Opaque cursor from a previous page.
        limit: Page size, 1 to 200.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cursor: Annotated[str | None, Field(max_length=MAX_CURSOR_LENGTH)] = None
    limit: Annotated[int, Field(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT


class ExportJobPage(BaseModel):
    """One page of export jobs, newest request first.

    Implements: API Schema.

    Attributes:
        items: The jobs on this page.
        next_cursor: Cursor of the next page, or ``None`` on the last page.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[ExportJobSummary, ...] = Field(max_length=MAX_PAGE_LIMIT)
    next_cursor: str | None = Field(max_length=MAX_CURSOR_LENGTH)


class RequestImportUploadRequest(BaseModel):
    """Body of ``POST /api/v1/moderation/imports/uploads``.

    Implements: API Schema.

    Attributes:
        format: The format of the file about to be uploaded.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    format: ImportFormat


class ImportUploadGrantResponse(BaseModel):
    """Where and how to upload an import file.

    Implements: API Schema.

    Attributes:
        object_key: The key the file is stored under; send it back in
            ``POST /moderation/imports``.
        upload_url: The presigned ``PUT`` URL.
        headers: Headers the upload must carry.
        media_type: The ``Content-Type`` the upload must declare.
        max_bytes: Largest accepted file.
        expires_at: When the URL stops working, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    object_key: ImportUploadKey
    upload_url: Annotated[
        str, StringConstraints(min_length=1, max_length=PRESIGNED_URL_MAX_LENGTH)
    ]
    headers: tuple[HttpHeader, ...] = Field(max_length=UPLOAD_HEADERS_MAX)
    media_type: MediaTypeName
    max_bytes: int = Field(ge=1, le=IMPORT_UPLOAD_MAX_BYTES)
    expires_at: AwareDatetime


class ImportArtifactRequest(BaseModel):
    """The uploaded file an import reads, as its uploader measured it.

    The import checks the stored file against ``byte_size`` and ``sha256`` before
    trusting it, so a file changed after the request is never imported.

    Implements: API Schema.

    Attributes:
        object_key: The key ``POST /moderation/imports/uploads`` granted.
        byte_size: The file's size in bytes.
        sha256: SHA-256 of the file, 64 lower-case hex digits.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    object_key: ImportUploadKey
    byte_size: int = Field(ge=1, le=IMPORT_UPLOAD_MAX_BYTES)
    sha256: Sha256


class RequestImportRequest(BaseModel):
    """Body of ``POST /api/v1/moderation/imports``.

    Implements: API Schema.

    Attributes:
        format: The file's format; it must match the uploaded key's extension.
        artifact: The uploaded file.
        dry_run: ``True`` to validate only; required, so a client always says.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    format: ImportFormat
    artifact: ImportArtifactRequest
    dry_run: bool

    @model_validator(mode="after")
    def _check_extension(self) -> Self:
        extension = DEFAULT_FORMAT_REGISTRY.import_descriptor(
            self.format.value
        ).extension
        if not self.artifact.object_key.endswith(f"/source{extension}"):
            message = "the uploaded file's extension does not match the format"
            raise ValueError(message)
        return self
