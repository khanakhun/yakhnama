# Data dictionary: reports

The `reports` module holds `Report`s: one raw observation from one person or
organisation, never trusted by default and **never edited after submission**.
Corrections are new revisions; withdrawal is a status change with a reason. Triage
attaches suggestions for moderators and never changes a report's status. It was
introduced in Phase 3. Column conventions are in [README.md](README.md). **Proposed**
marks a default the maintainer has not confirmed yet; the open questions at the end of
this page list each one.

Source code: `src/yakhnama/modules/reports/domain/`.

## Personal data

A report holds two kinds of personal data: the reporter's **position**
(`observation`) and their **words** (`description`, `withdrawal_reason`).

- The position is stored exact and is private. Public payloads round it to
  `public_coordinate_decimals`, and a place is never created from it (Phase 3 plan §2).
- Neither the position, the accuracy, the description nor the withdrawal reason ever
  appears in a domain event, an error message or a triage flag detail. Events carry
  ids, counts, statuses and flag kinds only.
- Triage may *flag* a phone number, email address or CNIC number in a description,
  but never repeats it and never redacts it: the report is the reporter's own record.

## Report (aggregate root)

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `id` | `UUID` (v7) | — | Identity of this revision. For revision 1 it is the **client-generated** id (`ClientReportId`), so a retried submission carries the same id and `SubmitReport` is idempotent on it. The timestamp inside the id is the client's and is never used as a time. A revision gets a new id from the platform. | Reporting client (revision 1); platform `IdGenerator` (later revisions, Q-R8). | Phase 3 |
| `reporter_id` | `UUID` (v7) | — | The user who reported. Every revision keeps it. | Authenticated actor. | Phase 3 |
| `organization_id` | `UUID` (v7), nullable | — | The organisation the user reported for. | Reporter's choice among their memberships (checked by the application). | Phase 3 |
| `source_id` | `UUID` (v7) | — | The `provenance` source record the report is attributed to. Every revision keeps it (Q-R13). | Application, at submission. | Phase 3 |
| `observed_at` | `DateWithPrecision` | UTC + precision | When the reporter observed it, with how precisely that is known. Not checked against `submitted_at` (Q-R10). | Reporter. | Phase 3 |
| `observation.coordinates` | `Coordinates` (WGS84) | degrees | Where the reporter was. **Private**; rounded in public payloads. | Reporter's device or a pin on a map. | Phase 3 |
| `observation.accuracy` | `GpsAccuracy`, nullable | metre | The device's horizontal accuracy radius, 0 to 10 000 m. `null` when unknown (manual pin, or the device does not say). | Reporter's device. | Phase 3 |
| `description` | `str`, safe text 1–4000 | — | What the reporter saw, in their words. Unicode NFC, trimmed; line breaks kept as `LF`; other control characters, lone surrogates and bidirectional overrides refused (`shared_kernel.text`). | Reporter. | Phase 3 |
| `original_language` | `LanguageCode` | — | Language and script of `description` (BCP 47 subset, for example `ur-Arab`, `scl`). | Reporter's client. | Phase 3 |
| `hazard_guess.hazard_code` | `str`, nullable | — | Code of the hazard type the reporter thinks they saw, same format as `hazards.HazardCode`. A guess, never a classification. | Reporter. | Phase 3 |
| `hazard_guess.confidence` | `Confidence` | — | The reporter's own confidence in the guess: `low`, `medium`, `high`. | Reporter. | Phase 3 |
| `place_hint` | `str`, nullable | — | Code of a place the reporter picked, same format as `geography.PlaceCode`. Only a hint. | Reporter. | Phase 3 |
| `media_ids` | list of `UUID` (v7), 0–20, unique | — | Media assets attached to this revision (**proposed** maximum, Q-R7). | Reporter, after uploading through `media`. | Phase 3 |
| `status` | `ReportStatus` | — | Lifecycle status, see below. Not the verification status. | Derived from the lifecycle. | Phase 3 |
| `revision` | `int`, 1–1000 | count | Position in the revision chain: 1 for the original, +1 per correction. | Platform. | Phase 3 |
| `supersedes_id` | `UUID` (v7), nullable | — | The revision this one replaces. Set exactly when `revision` > 1. | Platform. | Phase 3 |
| `superseded_by_id` | `UUID` (v7), nullable | — | The revision that replaced this one. Set exactly when `status` is `superseded`. | Platform. | Phase 3 |
| `withdrawal_reason` | `str`, safe single-line text 1–500, nullable | — | Why the reporter withdrew the report. Set exactly when `status` is `withdrawn`. | Reporter. | Phase 3 |
| `triage` | `TriageResult`, nullable | — | The latest triage result. A later run replaces it (Q-R11). Never set on a draft. | Triage chain. | Phase 3 |
| `submitted_at` | `datetime` (UTC), nullable | UTC | When the platform accepted the submission. Unset for a draft, set once submitted or superseded, either for a withdrawn report (a draft can be withdrawn). | Platform `Clock`. | Phase 3 |
| `version` | `int`, 1–2³¹−1 | count | Optimistic-concurrency version, +1 per change with an effect. | Platform. | Phase 3 |
| `created_at` | `datetime` (UTC) | UTC | When this record was created. | Platform `Clock`. | Phase 3 |
| `updated_at` | `datetime` (UTC) | UTC | When this record last changed; never before `created_at`. | Platform `Clock`. | Phase 3 |

### ReportStatus

| value | meaning | allowed changes |
|-------|---------|-----------------|
| `draft` | Written but not submitted; private to the reporter. | `submit` → `submitted`; `withdraw` → `withdrawn`. |
| `submitted` | The current revision of an observation. Content is frozen. | `attach_triage` (status unchanged); `revise` creates the next revision; `mark_superseded` → `superseded`; `withdraw` → `withdrawn`. |
| `superseded` | Replaced by a newer revision. Kept for the record. | None. |
| `withdrawn` | Taken back by the reporter, with a reason. Kept for the record; never deleted. | None. |

### Revisions

`Report.revise(content)` returns a **new** report (new id, `revision + 1`,
`supersedes_id` = old id, submitted at once, version 1, no triage) and
`ReportRevised`. The handler then calls `old.mark_superseded(new)`, which returns the
old report as `superseded` and `ReportSuperseded`, and stores both in the same unit of
work. A revision identical to the current content is refused. `ReportContent` holds
the fields a revision replaces: `observed_at`, `observation`, `description`,
`original_language`, `hazard_guess`, `place_hint`, `media_ids`.

## Triage

Triage runs the rules below in this order over one submitted report and attaches the
result. **It only suggests**: no rule blocks, edits, rejects or verifies a report, and
no rule stops the others.

### TriageResult and TriageFlag

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `triage.flags` | list of `TriageFlag`, 0–32 | — | Suspicions raised, in rule order. Empty means no rule raised one, not that the report is verified. | Triage chain. | Phase 3 |
| `triage.evaluated_at` | `datetime` (UTC) | UTC | When the chain ran. | Platform `Clock`. | Phase 3 |
| `flag.kind` | `exif_implausible` \| `duplicate_suspected` \| `pii_detected` \| `spam_suspected` | — | What is suspected. | Triage rule. | Phase 3 |
| `flag.detail` | `str`, safe single-line text 1–500 | — | Why, for a moderator. Written by the rule, never copied from the report: it names what was found but never the value, the coordinates or a precise distance. | Triage rule. | Phase 3 |
| `flag.confidence` | `Confidence` | — | How strongly the rule's signal supports the suspicion (**proposed** per rule, Q-R6). | Triage rule. | Phase 3 |
| `flag.related_report_id` | `UUID` (v7), nullable | — | The other report involved; set by `duplicate_suspected` to the nearest match. | Triage rule. | Phase 3 |

### Rules (every threshold proposed)

| rule | flag kind | fires when | confidence | question |
|------|-----------|-----------|------------|----------|
| `ExifPlausibilityRule` | `exif_implausible` | Any attached photo's EXIF capture time is more than **7 days** from `observed_at`, or its EXIF position is more than **5 km** from the observation point. Photos without EXIF are never flagged. Times are compared only when both are known to the day or better. | medium | Q-R2, Q-R5 |
| `DuplicateSuspicionRule` | `duplicate_suspected` | Another report (not itself, not the revision it replaces) was observed within **2 km** and **24 hours**. Times compared only when both are known to the day or better. | low | Q-R1, Q-R5 |
| `PiiScrubRule` | `pii_detected` | The description matches a Pakistani phone number (mobile `03XX-XXXXXXX` with `0`, `+92` or `0092`; landline with `+92` or `0092`), an email address, or a CNIC `NNNNN-NNNNNNN-N`. Unicode digits (including Urdu digits) count. Flags only. | medium | Q-R3 |
| `SpamRule` | `spam_suspected` | The description is shorter than **10** characters, has more than **3** links, or repeats one character **10** or more times in a row. | low | Q-R4 |

Distances are great-circle distances by the haversine formula on a sphere of radius
6 371 008.8 m (IUGG mean Earth radius); the error against WGS84 is below 0.5 %.

## Events

All events carry `event_id`, `occurred_at`, `aggregate_id` (the report id),
`aggregate_type` = `report`, `version` and `revision`. None carries a description,
reason, coordinate or accuracy.

| event type | when | extra payload |
|------------|------|---------------|
| `reports.report_submitted` | A report was submitted (revision 1). | `reporter_id`, `organization_id`, `source_id`, `media_count` |
| `reports.report_revised` | A new revision was submitted; `aggregate_id` is the new one. | `supersedes_id`, `reporter_id`, `organization_id`, `source_id`, `media_count` |
| `reports.report_superseded` | A revision was replaced by the next one. | `superseded_by_id` |
| `reports.report_withdrawn` | The reporter withdrew a report. | `previous_status` |
| `reports.report_triaged` | A triage result was attached. | `flag_kinds` |

## Errors

| error | family | raised when |
|-------|--------|-------------|
| `ReportNotFoundError` | not found | No report has the id. |
| `ReportAlreadySubmittedError` | conflict | `submit` on a submitted or superseded report. |
| `ReportImmutableError` | invalid transition | Revising, withdrawing, triaging or re-superseding a superseded report. |
| `ReportWithdrawnError` | invalid transition | Submitting, revising or triaging a withdrawn report. |
| `ReportNotSubmittedError` | invalid transition | Revising, superseding or triaging a draft. |
| `ReportRevisionUnchangedError` | validation | A revision's content equals the current content. |
| `ReportSupersessionMismatchError` | invariant violation | `mark_superseded` with a report that is not the next revision by the same reporter. |

## Persistence

`reports` (migration `0010_reports`) stores one row per revision. `observation`
is the reporter's exact, private WGS84 point with a GiST index for the future
distance queries duplicate suspicion and moderator maps need; it is never
stored rounded. **The rounded-point rule lives outside storage**: every row
always holds the exact point, and `AuthorisedReportQueryService`
(`shared_kernel.privacy.PublicCoordinatePolicy`) rounds it to
`public_coordinate_decimals` only when building a non-exact view for a
response, so the rounding rule can change (a different `decimals` setting, or a
different policy entirely) without a migration or a backfill. `UNIQUE
(supersedes_id)` enforces "at most one revision replaces a report" at the
database level, backing `ReportSupersessionMismatchError`. `supersedes_id` and
`superseded_by_id` both reference `reports.id` (`ON DELETE RESTRICT`; reports
are never deleted anyway). Reporter, organisation and source ids carry no
foreign key, because they belong to other modules.

## Open questions raised by this module

| # | Question | Proposed default | Blocking |
|---|----------|------------------|----------|
| Q-R1 | Distance and time window for duplicate suspicion. | 2 km and 24 hours (Phase 3 plan §6). | no |
| Q-R2 | EXIF plausibility tolerances. | Capture time within 7 days of `observed_at` and position within 5 km of the observation point (Phase 3 plan §6). | no |
| Q-R3 | Which personal-data patterns does triage look for? CNIC numbers written without dashes (13 digits) and Pakistani landlines written with a leading `0` are not matched, to avoid flagging ordinary numbers. | Mobile numbers with `0`, `+92` or `0092`; landlines with `+92` or `0092`; emails; CNIC `NNNNN-NNNNNNN-N` only. | no |
| Q-R4 | Spam signals. | Description shorter than 10 characters, more than 3 links, or one character repeated 10 or more times. | no |
| Q-R5 | How do rules compare times known only to the month, season or year? | They do not: comparisons need `exact`, `hour` or `day` precision on both sides; otherwise the rule skips the comparison. | no |
| Q-R6 | Confidence attached to each rule's flags. | EXIF medium, duplicate low, personal data medium, spam low. | no |
| Q-R7 | Most media assets per report. | 20. | no |
| Q-R8 | Who generates the id of a revision? An offline client cannot know it in advance, so a retried revision relies on the `Idempotency-Key`. | The platform's `IdGenerator`. | no |
| Q-R9 | Which reports may be withdrawn? | Drafts and current revisions. A superseded revision cannot be withdrawn on its own; the reporter withdraws the latest revision. Withdrawn reports stay on record. | no |
| Q-R10 | May `observed_at` lie after the submission time (a wrong client clock)? | Not rejected by the domain; the value is the reporter's statement. | no |
| Q-R11 | Does a new triage run replace the previous result? | Yes; the aggregate keeps the latest result, and every run stays in the outbox and audit log through `ReportTriaged`. | no |
| Q-R12 | `hazard_guess.hazard_code` and `place_hint` mirror the `hazards` and `geography` code formats because a domain layer may import only the kernel (`AGENTS.md` §2.1). Should the code formats move into `shared_kernel`? | Keep the mirrors, guarded by unit tests that compare them with the originals; the application checks existence through the facades. | no |
| Q-R13 | Does every revision keep the original's reporter, organisation and source? | Yes; a revision is a correction by the same reporter, not a new source. | no |
