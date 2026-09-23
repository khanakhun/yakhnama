# Data dictionary: media

The `media` module holds `MediaAsset`s: one uploaded file (photo, video or document),
normally attached to a report. The **original** is private. A separate **public
copy**, re-encoded without any EXIF metadata, is published only after a clean malware
scan, a moderator's approval and with no blocking sensitivity flag. It was introduced
in Phase 3. Column conventions are in [README.md](README.md). **Proposed** marks a
default the maintainer has not confirmed yet; the open questions at the end of this
page list each one.

Source code: `src/yakhnama/modules/media/domain/`.

## Personal data

- **EXIF location** can reveal where a person lives or was. It is read from the
  private original, stored on the asset for triage and moderation, and never
  published, never put in an event and never written to a log. The public copy has no
  EXIF block at all: that is a property of the file the transcoder adapter writes.
- **Object keys** are built from the asset id (`media/original/<id>`,
  `media/public/<id>`), never from the uploader's file name. Keys never appear in
  events or errors, so a subscriber cannot fetch a private original.
- Imagery of injured, dead or identifiable people is flagged by a moderator
  (`sensitivity`); nothing detects it automatically.

## MediaAsset (aggregate root)

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `id` | `UUID` (v7) | — | Stable identity. | Platform `IdGenerator`. | Phase 3 |
| `owner_id` | `UUID` (v7) | — | The uploading user. | Authenticated actor. | Phase 3 |
| `report_id` | `UUID` (v7), nullable | — | The report the asset belongs to. | Application, at request. | Phase 3 |
| `source_id` | `UUID` (v7) | — | The `provenance` source the asset is attributed to. | Application, at request. | Phase 3 |
| `original_key` | `str`, object key | — | Storage key of the private original: `media/original/<id>` (**proposed** layout, Q-M6). Keys match `^[a-z0-9][a-z0-9/_.-]{3,255}$` and never contain `..`. | Platform. | Phase 3 |
| `public_key` | `str`, object key, nullable | — | Storage key of the EXIF-stripped public copy, `media/public/<id>`. Set only while the asset is publishable; cleared when it stops being so. | Platform, after transcoding. | Phase 3 |
| `sha256` | `str`, 64 hex, nullable | — | SHA-256 of the stored original, lower case. Set once the upload completed. | Platform, computed on the stored bytes. | Phase 3 |
| `mime_type` | `MimeType` | — | Declared by the uploader at request; replaced at completion by the type detected from the file's magic bytes. Allow-list below. | Uploader, then platform. | Phase 3 |
| `byte_size` | `int`, 1–52 428 800, nullable | byte | Size of the stored original (maximum 50 MiB, **proposed**, Q-M2). Set once the upload completed. | Platform. | Phase 3 |
| `exif.taken_at` | `DateWithPrecision`, nullable | UTC + precision | EXIF capture time. | EXIF reader adapter, from the original. | Phase 3 |
| `exif.location` | `Coordinates` (WGS84), nullable | degrees | EXIF GPS position. **Private**. | EXIF reader adapter, from the original. | Phase 3 |
| `exif.camera` | `str`, safe single-line text 1–120, nullable | — | EXIF camera make and model. | EXIF reader adapter, from the original. | Phase 3 |
| `upload_status` | `UploadStatus` | — | `requested`, `completed` or `failed`. | Platform. | Phase 3 |
| `scan_status` | `ScanStatus` | — | Malware scanner verdict: `pending`, `clean`, `infected`, `unavailable`. Only `clean` allows publication. | `MalwareScanner` port. | Phase 3 |
| `moderation_status` | `ModerationStatus` | — | `pending`, `approved`, `rejected`, `quarantined`. | Moderator (and the platform, for an infected scan). | Phase 3 |
| `sensitivity` | `SensitivityFlag` | — | `none`, `injured_or_deceased`, `identifiable_people`, `other` (**proposed** values, Q-M4). | Moderator. | Phase 3 |
| `moderation_reason` | `str`, safe single-line text 1–500, nullable | — | Why the asset was rejected, quarantined or approved. Required for `rejected` and `quarantined`. | Moderator, or the fixed text "The malware scanner reported an infection." | Phase 3 |
| `version` | `int`, 1–2³¹−1 | count | Optimistic-concurrency version, +1 per change with an effect. | Platform. | Phase 3 |
| `created_at` | `datetime` (UTC) | UTC | When the upload was requested. | Platform `Clock`. | Phase 3 |
| `updated_at` | `datetime` (UTC) | UTC | When the asset last changed; never before `created_at`. | Platform `Clock`. | Phase 3 |

### MimeType (proposed allow-list, Q-M1)

`image/jpeg`, `image/png`, `image/webp`, `video/mp4`, `application/pdf`.

### Lifecycle

| operation | allowed from | effect |
|-----------|--------------|--------|
| `MediaAssetFactory.request_upload` | — | New asset, `requested`, key `media/original/<id>`. |
| `complete_upload(stored)` | `requested` | Records digest, size, detected type, EXIF (empty EXIF stored as `null`). |
| `fail_upload` | `requested` | `failed`. |
| `mark_scan(verdict)` | completed | Records `clean`, `infected` or `unavailable`. `infected` also quarantines and withdraws any public copy (**proposed**, Q-M3). A later `clean` never lifts a quarantine. |
| `moderate(approved \| rejected, sensitivity, reason)` | completed | Approval may lift a quarantine but is refused while `infected`. Rejection or a blocking sensitivity withdraws a public copy (Q-M8). |
| `quarantine(reason)` | completed | `quarantined`; withdraws a public copy. |
| `publish_public_copy(key)` | publishable | Records `media/public/<id>`. Publishable = completed, `clean`, `approved`, and sensitivity not `injured_or_deceased` or `identifiable_people` (**proposed**, Q-M5). |

### Deduplication

The same SHA-256 from the **same owner** reuses the existing asset
(`MediaAsset.is_duplicate_of`). Files from different owners are never duplicates, so
one user's upload never reveals or reuses another's (Q-M7). The lookup is done by the
application.

## Events

All events carry `event_id`, `occurred_at`, `aggregate_id` (the asset id),
`aggregate_type` = `media_asset` and `version`. None carries an object key, a digest,
an EXIF fact or a moderation reason.

| event type | when | extra payload |
|------------|------|---------------|
| `media.upload_requested` | An upload slot was created. | `owner_id`, `report_id`, `source_id`, `mime_type` |
| `media.upload_completed` | The original arrived. | `mime_type`, `byte_size`, `has_exif` |
| `media.upload_failed` | The original never arrived or did not match (Q-M10). | — |
| `media.media_scanned` | The scanner returned a verdict. | `scan_status` |
| `media.media_moderated` | A moderator decided. | `moderation_status`, `sensitivity`, `is_public_copy_withdrawn` |
| `media.media_published` | The public copy was published. | — |
| `media.media_quarantined` | The asset was isolated. | `is_public_copy_withdrawn` |

`is_public_copy_withdrawn` tells a subscriber to delete the public object.

## Errors

| error | family | raised when |
|-------|--------|-------------|
| `MediaAssetNotFoundError` | not found | No asset has the id. |
| `MediaUploadNotPendingError` | invalid transition | Completing or failing an upload that already ended. |
| `MediaUploadNotCompletedError` | invalid transition | Scanning, moderating or quarantining before the upload completed. |
| `MediaNotPublishableError` | invalid transition | Publishing without a clean scan, an approval, or with a blocking sensitivity. |
| `InfectedMediaError` | invalid transition | Approving an infected asset. |
| `InvalidScanVerdictError` | validation | Recording `pending` as a scan result. |
| `InvalidModerationDecisionError` | validation | A decision other than approved or rejected, or a rejection without a reason. |

## Open questions raised by this module

| # | Question | Proposed default | Blocking |
|---|----------|------------------|----------|
| Q-M1 | Which media types may be uploaded? | `image/jpeg`, `image/png`, `image/webp`, `video/mp4`, `application/pdf`. HEIC (iPhone default) is not included. | no |
| Q-M2 | Largest accepted upload. | 50 MiB. | no |
| Q-M3 | Does an infected scan quarantine automatically? | Yes, with a fixed reason, and any public copy is withdrawn. | no |
| Q-M4 | Which sensitivity values exist? | `none`, `injured_or_deceased`, `identifiable_people`, `other`; set by a moderator only (Phase 3 plan §6). | no |
| Q-M5 | May sensitive imagery be published? | No: `injured_or_deceased` and `identifiable_people` keep an approved asset private until the maintainer decides how such imagery is shown (blurred, behind a warning, never). | no (safety-relevant) |
| Q-M6 | Object key layout. | `media/original/<id>` and `media/public/<id>`, never from file names. | no |
| Q-M7 | Deduplication scope. | Same SHA-256 and same owner only. | no |
| Q-M8 | Can a moderator's approval lift a quarantine? Can a clean rescan? | A moderator's approval can (unless the scan says infected); a clean rescan cannot. | no |
| Q-M9 | The development `NoOpScanner` cannot vouch for a file. If it reports `unavailable`, nothing can be published in development; if it reports `clean`, the domain cannot tell a real verdict from a no-op. | The domain treats only `clean` as publishable; the scanner adapter and settings decide what the NoOp reports, and production must refuse to start with the NoOp. | yes, for the T5 scanner adapter |
| Q-M10 | `UploadFailed` is not in the Phase 3 plan's event list; it is needed so a `failed` upload has a transition and an audit trail. | Keep it. | no |
