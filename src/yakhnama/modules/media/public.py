"""Public facade of the ``media`` module.

Other modules, the API and the composition root import only this file: the
commands, query, DTOs and handlers, the ports the composition root binds (storage,
EXIF, MIME, the malware scanner, the report-source lookup) and the read port the
reports adapters use for photo evidence and media ownership.

Patterns: Facade.
"""

from yakhnama.modules.media.application.authorisation import (
    moderation_policy,
    original_view_policy,
    public_view_policy,
    uploader_policy,
)
from yakhnama.modules.media.application.commands import (
    CompleteUpload,
    ModerateMedia,
    RecordScanResult,
    RequestUpload,
)
from yakhnama.modules.media.application.dto import (
    HttpHeader,
    MediaAssetDetail,
    MediaAssetRecord,
    PresignedDownload,
    PresignedUpload,
    StoredObject,
    UploadGrant,
)
from yakhnama.modules.media.application.handlers import (
    CONTENT_CHANGED_QUARANTINE_REASON,
    UPLOAD_SOURCE_TITLE,
    CompleteUploadHandler,
    ModerateMediaHandler,
    RecordScanResultHandler,
    RequestUploadHandler,
)
from yakhnama.modules.media.application.ports import (
    SCAN_TASK,
    ExifReader,
    MalwareScanner,
    MediaAssetRepository,
    MediaQueryService,
    MediaUnitOfWork,
    MediaUnitOfWorkFactory,
    MimeSniffer,
    ReportSourceLookup,
    StoragePort,
)
from yakhnama.modules.media.application.queries import GetMediaAsset
from yakhnama.modules.media.application.query_services import (
    AuthorisedMediaQueryService,
)
from yakhnama.modules.media.domain.entities import MediaAsset
from yakhnama.modules.media.domain.errors import (
    InfectedMediaError,
    InvalidModerationDecisionError,
    InvalidScanVerdictError,
    MediaAssetNotFoundError,
    MediaContentChangedError,
    MediaNotPublishableError,
    MediaUploadNotCompletedError,
    MediaUploadNotPendingError,
)
from yakhnama.modules.media.domain.value_objects import (
    MAX_MEDIA_BYTES,
    PUBLISHABLE_MIME_TYPES,
    ExifFacts,
    MimeType,
    ModerationStatus,
    ScanStatus,
    SensitivityFlag,
    UploadStatus,
    original_object_key,
    public_object_key,
    upload_object_key,
)

__all__ = [
    "CONTENT_CHANGED_QUARANTINE_REASON",
    "MAX_MEDIA_BYTES",
    "PUBLISHABLE_MIME_TYPES",
    "SCAN_TASK",
    "UPLOAD_SOURCE_TITLE",
    "AuthorisedMediaQueryService",
    "CompleteUpload",
    "CompleteUploadHandler",
    "ExifFacts",
    "ExifReader",
    "GetMediaAsset",
    "HttpHeader",
    "InfectedMediaError",
    "InvalidModerationDecisionError",
    "InvalidScanVerdictError",
    "MalwareScanner",
    "MediaAsset",
    "MediaAssetDetail",
    "MediaAssetNotFoundError",
    "MediaAssetRecord",
    "MediaAssetRepository",
    "MediaContentChangedError",
    "MediaNotPublishableError",
    "MediaQueryService",
    "MediaUnitOfWork",
    "MediaUnitOfWorkFactory",
    "MediaUploadNotCompletedError",
    "MediaUploadNotPendingError",
    "MimeSniffer",
    "MimeType",
    "ModerateMedia",
    "ModerateMediaHandler",
    "ModerationStatus",
    "PresignedDownload",
    "PresignedUpload",
    "RecordScanResult",
    "RecordScanResultHandler",
    "ReportSourceLookup",
    "RequestUpload",
    "RequestUploadHandler",
    "ScanStatus",
    "SensitivityFlag",
    "StoragePort",
    "StoredObject",
    "UploadGrant",
    "UploadStatus",
    "moderation_policy",
    "original_object_key",
    "original_view_policy",
    "public_object_key",
    "public_view_policy",
    "upload_object_key",
    "uploader_policy",
]
