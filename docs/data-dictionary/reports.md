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
- An assisted report's `assisted.note` is the assisting person's private note: shown
  only with the exact view (the person who entered the report and moderators), never
  in a listing, an event or a log (log redaction drops keys ending in `note` and
  containing `consent`). It is stored as **plain text** in `reports.assisted_note`, not
  encrypted: it is protected by the database's access control like the exact position,
  so it must not hold more than the assisting person needs (Q219). The consent record
  never identifies the assisted person (ADR 0019).
- A guest report's reporter is the guest submission; it is never shown (`reporter_id`
  is `null` in every API view). The guest's capability is stored only as its SHA-256
  digest (ADR 0020).
- A moderator's review reason or note (`report_review_marks.reason`) may quote the
  report; it is shown to moderators only, never in an event (`ReportReviewMarked`
  carries `has_reason` only) or a log (ADR 0022).

## Report (aggregate root)

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `id` | `UUID` (v7) | — | Identity of this revision. For revision 1 it is the **client-generated** id (`ClientReportId`), so a retried submission carries the same id and `SubmitReport` is idempotent on it. The timestamp inside the id is the client's and is never used as a time. A revision gets a new id from the platform. | Reporting client (revision 1); platform `IdGenerator` (later revisions, Q-R8). | Phase 3 |
| `reporter_id` | `UUID` (v7) | — | The principal accountable for the report: the user who entered it (`account`, `assisted`) or the guest submission that carried it (`guest`). Every revision keeps it. Shown as `null` for a guest report. | Authenticated actor, or the guest submission (ADR 0020). | Phase 3 (guest: 2026-10) |
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
| `channel` | `ReportChannel` | — | How the report reached the platform: `account`, `assisted` or `guest`. Every revision keeps it. `account` for reports stored before channels existed. | Platform, from the route and the `assisted` block. | 2026-10 (ADR 0019) |
| `assisted.consent_method` | `ConsentMethod`, nullable | — | How the assisted person consented: `verbal` or `written`. Set exactly for `assisted`. | Assisting person. | 2026-10 |
| `assisted.consent_statement_version` | `str`, `^[a-z0-9][a-z0-9._-]{0,31}$`, nullable | — | Version of the consent statement read to or by the assisted person; the statement's text lives with the client (portal message catalogues). | Assisting person's client. | 2026-10 |
| `assisted.note` | `str`, safe text 1–500 with line breaks, nullable | — | Private note by the assisting person. **Personal data**: exact view only; stored as plain text (not encrypted). | Assisting person. | 2026-10 |
| `lineage_id` (column only) | `UUID` (v7) | — | The id of the lineage's revision 1: equal to `id` for revision 1, inherited by every later revision. Not a field of the aggregate; set by the repository on insert and read by the review marks. | Platform. | 2026-10 (ADR 0022) |

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
| `reports.report_submitted` | A report was submitted (revision 1). | `reporter_id`, `organization_id`, `source_id`, `media_count`, `channel` |
| `reports.report_revised` | A new revision was submitted; `aggregate_id` is the new one. | `supersedes_id`, `reporter_id`, `organization_id`, `source_id`, `media_count`, `channel` |
| `reports.report_superseded` | A revision was replaced by the next one. | `superseded_by_id` |
| `reports.report_withdrawn` | The reporter withdrew a report. | `previous_status` |
| `reports.report_triaged` | A triage result was attached. | `flag_kinds` |
| `reports.report_review_marked` | A moderator marked a lineage; `aggregate_type` is `report_review` and `aggregate_id` the lineage id; the report itself is unchanged. | `state`, `previous_state`, `report_id`, `revision`, `actor_id`, `has_reason` (never the reason) |

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
| `GuestChallengeInvalidError` | validation (`guest-challenge-invalid`) | The challenge is malformed or not signed with the platform's key. |
| `GuestChallengeExpiredError` | validation (`guest-challenge-expired`) | The challenge was redeemed after `expires_at`. |
| `GuestProofInvalidError` | validation (`guest-proof-invalid`) | The nonce does not give the required leading zero bits. |
| `GuestChallengeSpentError` | conflict (`guest-challenge-spent`) | The challenge already opened a submission. |
| `GuestCapabilityInvalidError` | permission denied (`guest-capability-invalid`) | No capability, a wrong one, or an unknown submission. |
| `GuestCapabilityExpiredError` | permission denied (`guest-capability-expired`) | The capability has expired. |
| `GuestMediaLimitError` | conflict (`guest-media-limit`) | A fourth photo upload was requested. |
| `GuestSubmissionClosedError` | conflict (`guest-submission-closed`) | An upload or a different report after the report was reserved. |
| `GuestMediaNotFoundError` | not found | Completing an asset not granted to the submission. |
| `GuestSubmissionLimitError` | 429 (`rate-limited`) | An hourly cap is reached: submissions opened, or guest reports submitted; carries `retry_after_seconds` (the `Retry-After` header). |
| `ReviewReasonRequiredError` | validation | A review mark that needs a reason has none (archive, back to `new`, out of `archived`); `details.reason` is `review_reason_required`. |
| `ReportFilterForbiddenError` | permission denied | A caller who is not a moderator filters reports by `review_state`. |

## Review marks (ADR 0022)

Report moderation is non-blocking and reversible (maintainer, 2026-10-07): nothing a
reporter sends is refused, rejected or deleted, and a mark never changes a report's
status, content, `version` or its reporter's rights to revise and withdraw. Moderators
mark a report **lineage** (a report and all its revisions) `new`, `reviewed` or
`archived`; marks are shown to moderators only (Q240).

### ReportReview (aggregate root)

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `id` | `UUID` (v7) | — | The lineage id: the id of the lineage's revision 1. | Platform. | 2026-10 |
| `state` | `ReviewState` | — | `new` (never marked, or a mark undone), `reviewed` (a moderator looked at it), `archived` (set aside with a reason; still on record). A lineage without a review is `new`. | Moderator. | 2026-10 |
| `last_mark` | `ReviewMark` | — | The mark that set `state`. | Moderator. | 2026-10 |
| `version` | `int`, 1–2³¹−1 | count | Optimistic-concurrency version; the API's review `ETag` is `"<lineage_id>:<version>"`, version 0 while unmarked. | Platform. | 2026-10 |
| `created_at` | `datetime` (UTC) | UTC | When the lineage was first marked. | Platform `Clock`. | 2026-10 |
| `updated_at` | `datetime` (UTC) | UTC | When it was last marked; equals `last_mark.marked_at`. | Platform `Clock`. | 2026-10 |

### ReviewMark (one row of the history, never changed)

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `id` | `UUID` (v7) | — | The mark. | Platform. | 2026-10 |
| `state` | `ReviewState` | — | The state the mark set. | Moderator. | 2026-10 |
| `reason` | `str`, safe text 1–500 with line breaks, nullable | — | Why, or a note. Required to archive, to go back to `new` and to leave `archived`; optional when marking `reviewed` from `new` or `reviewed` (Q242). Moderators only. | Moderator. | 2026-10 |
| `report_id` | `UUID` (v7) | — | The revision the moderator looked at. | Moderator's request. | 2026-10 |
| `revision` | `int`, 1–1000 | count | That revision's number. | Platform. | 2026-10 |
| `actor_id` | `UUID` (v7) | — | The moderator (a user id). | Authenticated actor. | 2026-10 |
| `marked_at` | `datetime` (UTC) | UTC | When. | Platform `Clock`. | 2026-10 |

A mark repeating the last one (same state, revision and reason) changes nothing.

### What reports carry for moderators

`ReportSummary.review` and `ReportDetail.review` are
`{state, updated_at, updated_by, reviewed_revision, revised_since}`, `null` for anyone
who is not a moderator. `revised_since` is true while the lineage has a newer revision
than the one a `reviewed` or `archived` mark was made on. `ReportDetail.linked_events`
(moderators only) lists `{event_id, report_id, role}` for every event any revision of
the lineage is linked to, read from the events module's `event_report_links`
projection.

## Guest submissions (ADR 0020)

### GuestSubmission (aggregate root)

A submission is **open** (photos may be reserved), then **reserved** (the report's
fingerprint, source id and submission time are recorded before the source or the report
exists), then **filed** (report id and reference). Every change is a version-checked
save, so of two concurrent requests exactly one wins and the other reloads.

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `id` | `UUID` (v7) | — | The submission; the guest report's reporter and the owner of its photos. | Platform `IdGenerator`. | 2026-10 |
| `capability_digest` | `str`, 64 hex | — | SHA-256 of the capability handed to the guest once. The capability itself (256 random bits) is never stored. | Platform (`secrets`). | 2026-10 |
| `expires_at` | `datetime` (UTC) | UTC | When the capability stops working: opening time + `guest_capability_ttl_seconds` (default 1800, **proposed**). A retry of the submitted report is still answered for `guest_receipt_grace_seconds` (default 86 400) after it. | Platform `Clock`. | 2026-10 |
| `media_ids` | list of `UUID` (v7), 0–3, unique | — | Photo slots reserved for the submission, each naming the asset the media module then creates with that id (maintainer decision: at most 3). A slot whose grant failed on the server is given back. | Platform `IdGenerator`. | 2026-10 |
| `content_fingerprint` | `str`, 64 hex, nullable | — | SHA-256 of the submitted content's canonical JSON, to answer a retry with the same receipt. Set by the reservation. | Platform. | 2026-10 |
| `source_id` | `UUID` (v7), nullable | — | The platform source the report cites, chosen by the reservation before the source is registered (idempotently, under this id). | Platform `IdGenerator`. | 2026-10 (migration `0021`) |
| `submitted_at` | `datetime` (UTC), nullable | UTC | When the report was submitted (reserved); counted by the hourly reports cap. `content_fingerprint`, `source_id` and `submitted_at` are set together. | Platform `Clock`. | 2026-10 |
| `report_id` | `UUID` (v7), nullable, unique | — | The guest report, once filed. | Platform. | 2026-10 |
| `reference` | `str`, `YK-XXXX-XXXX`, nullable, unique | — | Receipt code the guest can quote: two groups of four from `ABCDEFGHJKMNPQRSTVWXYZ23456789` (no look-alikes), about 39 bits. Set with `report_id`, only after the reservation. | Platform (`secrets`). | 2026-10 |
| `version` | `int`, ≥ 1 | count | Optimistic-concurrency version. | Platform. | 2026-10 |
| `created_at`, `updated_at` | `datetime` (UTC) | UTC | Opening and last change; `created_at` is what the hourly opened cap counts. | Platform `Clock`. | 2026-10 |

**Retention** (Q226, **proposed**): a submission that never filed a report is deleted by
`reports.purge_guest_records` once its capability expired more than
`guest_receipt_grace_seconds` ago; a filed submission is kept with its report.

### Proof-of-work challenge (not stored until redeemed)

| field | type | meaning |
|-------|------|---------|
| `salt` | `str`, URL-safe base64, 16–64 characters | 128 random bits per challenge. |
| `difficulty_bits` | `int`, 1–22 issued (1–32 accepted) | Leading zero bits `SHA-256(salt + nonce)` needs: `guest_pow_difficulty_bits` (default 18, at least 18 in production) plus one per `guest_pow_difficulty_step` (default 200) submissions opened in the last hour, at most `guest_pow_difficulty_max_bits` (22). **Proposed** (Q222). |
| `expires_at` | `datetime` (UTC), whole seconds | Issue time + `guest_challenge_ttl_seconds` (default 600, **proposed**). |
| `challenge` (token) | `v1.<salt>.<bits>.<expiry>.<HMAC-SHA256>` | Signed with `guest_challenge_secret`; opaque to clients. |

`guest_challenges` keeps a redeemed challenge's `salt` (primary key) and `expires_at`,
so a challenge opens at most one submission. The insert refuses a challenge that has
expired by the database's clock, and `reports.purge_guest_records` deletes rows five
minutes after they expired by the same clock.

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

Migration `0019_reporting_channels` adds `channel` (`NOT NULL`, server default
`account`), the three `assisted_*` columns with the checks `channel_known` and
`assisted_matches_channel`, and the tables `guest_submissions` (`report_id` references
`reports.id`, `ON DELETE RESTRICT`; `reference` and `report_id` unique; `created_at`
indexed for the hourly cap) and `guest_challenges` (`expires_at` indexed for the
purge). **Its downgrade loses data**: guest and assisted reports keep their rows but
lose their channel and consent record (the earlier schema cannot hold them), and the
guest submissions with their receipt references are dropped.

Migration `0021_guest_reservations_and_report_checks` adds the checks
`guest_without_organisation` (a guest report names no organisation),
`assisted_consent_method_known` (`verbal` or `written`) and
`assisted_statement_version_format`, replaces the plain `channel` index with the partial
index `ix_reports_channel_created_at_id_not_account` on `(channel, created_at, id)`
over the non-account channels (the moderators' guest and assisted queues), and expands
`guest_submissions`: `source_id`, indexes on `submitted_at` (reports cap) and
`expires_at` (retention), and the checks `filed_after_reserved`,
`media_ids_at_most_three`, `version_positive`, `times_ordered` and
`capability_digest_format` in place of `closed_fields_together`. `0022` backfills
`source_id` of filed submissions from their report; `0023` adds
`reserved_fields_together`. Downgrading `0021` fails while a submission is reserved but
not yet filed (seconds per report in flight).

Migration `0025_report_review_marks_and_moderation_queues` adds `reports.lineage_id`
(backfilled along each revision chain, then `NOT NULL`, referencing `reports.id`,
indexed), the check `lineage_root_is_first_revision` (`(revision = 1) = (lineage_id =
id)`), the GIN index `ix_reports_media_ids_gin` (`jsonb_path_ops`) for the media
queue's report lookup, and two tables: `report_reviews` (primary key `lineage_id`
referencing `reports.id`; `state`, indexed and checked; `last_mark_id`;
`reviewed_report_id` referencing `reports.id`; `reviewed_revision`, `updated_by`,
`version`, `created_at`, `updated_at`) and `report_review_marks` (insert-only; `id`,
`lineage_id` referencing `report_reviews`, `state`, `reason`, `report_id` referencing
`reports.id`, `revision`, `actor_id`, `marked_at`; indexed on `(lineage_id, marked_at,
id)`). All references are `ON DELETE RESTRICT`. **Its downgrade loses every review
mark.**

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

The reporting channels and guest submissions added their questions to the central
log, `docs/open-questions.md` Q218–Q228; the review marks Q240–Q246 and Q251–Q253.
