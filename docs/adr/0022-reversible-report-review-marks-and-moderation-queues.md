# 0022. Reversible report review marks, the media queue and the moderator directory

- Date: 2026-10-07
- Status: proposed
- Amended: 2026-10-07, after review of `cb04e54`: no-op and stale-revision marks,
  bulk `error` outcome and lineage collapse, linked-events cap, the media queue's
  report lookup and indexes (migration `0026`), triage flag kinds on listings,
  `If-Match` on photo decisions, the case-by-target filter and the idempotency
  answers in the contract
- Deciders: lead agent, maintainer

## Context and problem statement

The web portal's moderation console (portal plan, phase 3, task B3) needs to work
through what reporters send. At `4e1a024` the backend gave moderators nothing for
that: a report has a lifecycle status that belongs to its reporter (`submitted`,
`superseded`, `withdrawn`), no moderator state, and no route a moderator can act
on. Nothing opens a verification case for a report either (Q133 only describes the
initial state). The console also needs a queue of photos awaiting a decision, the
names of the people a case can be assigned to, a way to list a moderator's own
reports, and response headers (`ETag` above all) declared in the contract so a
generated client knows to send them back.

On 2026-10-07 the maintainer decided that **report moderation is non-blocking and
reversible**: nothing a reporter sends is refused, rejected or deleted; moderators
mark reports *reviewed* or *archived*, with a reason to archive, and can undo
either; reporters keep their rights to revise and withdraw. Self-verification of
events is allowed, and sensitive photos stay never public (Q109).

How do moderators keep track of reports without touching them, and what do the
queue, directory and header gaps become in the contract?

## Decision drivers

- Reports are never edited after submission (`AGENTS.md` §5); a moderator's view of
  a report must not change the report, its `ETag` or what its reporter may do.
- Every mark is auditable: who, when, why; nothing about it is ever lost.
- A correction is a new revision of the same observation, so it should not undo a
  moderator's work, but the moderator must see that it changed.
- Privacy: the reporter's position and the moderators' notes stay inside the
  moderation boundary; events and logs carry no free text.
- No new pattern: aggregates, repositories, query services and policies as they are.
- The console works over a slow connection: one read gives a report with its mark.

## Considered options

1. A reversible **review mark per report lineage** (`new`, `reviewed`, `archived`)
   in its own aggregate `ReportReview`, with an append-only history of marks, the
   revision each mark was made on and a derived `revised_since` (proposed)
2. A review mark per report revision
3. A `VerificationCase` per report, moved through the verification state machine
4. A moderator status on the `Report` aggregate itself

## Decision outcome

Proposed option: **1**.

- **Lineage.** `reports.lineage_id` names the id of a report's revision 1 (a revision
  inherits it; migration `0025` backfills every row and adds the check
  `lineage_root_is_first_revision`). The repository sets it on insert; the `Report`
  aggregate does not carry it, because nothing in the domain depends on it.
- **`ReportReview` aggregate** (`reports/domain/reviews.py`), keyed by the lineage
  id, holds the current `state`, the `last_mark` and a version. A lineage without a
  review is `new`. `mark` refuses a missing reason (`ReviewReasonRequiredError`,
  422): archiving, moving back to `new` and leaving `archived` need one; marking
  `reviewed` from `new` or `reviewed` takes an optional note. A mark repeating the
  last one (state, revision and reason) changes nothing, and so does marking a
  never-marked lineage `new`, with or without a reason (amended: it used to answer
  `reason_required`). Each mark records the revision the moderator looked at;
  `revised_since` is true while the lineage has a newer revision than the one a
  `reviewed` or `archived` mark was made on. A mark on a revision older than the one
  the lineage was last marked on is refused (`ReviewRevisionSupersededError`, 409,
  `details.reason = "review_revision_superseded"`; amended, Q254): it would move the
  mark back in time.
- **History.** Every mark is a row in `report_review_marks`, inserted in the same
  transaction as the review and never updated or deleted. `ReportReviewMarked`
  (event `reports.report_review_marked`) carries the states, the revision, the
  moderator and whether there was a reason, never the reason itself.
- **Routes** (all `CanModerate`, under `/api/v1/moderation`):
  `GET /reports/{report_id}/review` returns the lineage's mark and up to 200 marks,
  newest first (`ReportReviewDetail`, with `ETag: "<lineage_id>:<version>"`,
  version 0 while unmarked); `POST /reports/{report_id}/review` with
  `{state, reason?}` marks and returns the same body, its `ETag` naming the version
  the mark produced, from the command's result (amended). `If-Match` is optional, as
  on every moderation route (Q66), so the route declares `412` but not `428`
  (amended), but when sent it is compared with the review's version inside the unit
  of work (unlike Q155), so two moderators cannot both win.
  `POST /reports/review` with `{report_ids (1–100, distinct), state, reason?}`
  marks each lineage in its own unit of work and returns one outcome per report id:
  `marked`, `unchanged`, `not_found`, `reason_required`, `conflict` or `error`
  (amended: any other refusal by the domain; a failure outside it, such as the
  database going away, still aborts the request with the reports before it marked).
  Revisions of one lineage are collapsed (amended, Q255): the lineage is marked
  once, on the newest revision the request names, and every id of it gets that
  result. It is not atomic on purpose: one archived report in a spam wave must not
  block the rest.
- **Reads.** `ReportSummary.review` and `ReportDetail.review`
  (`{state, updated_at, updated_by, reviewed_revision, revised_since}`) and
  `ReportDetail.linked_events` (`[{event_id, report_id, role}]`, from the events
  module's `event_report_links` projection, across the lineage, oldest first, at
  most 200, with `ReportDetail.is_linked_events_truncated`; amended, Q259) are set
  for moderators only and `null` for everyone else, the reporter included (Q240).
  `ReportSummary.triage_flags` (amended, Q256) lists, for moderators only, the
  distinct kinds of flag the report's latest triage raised, without their detail.
  `GET /reports?review_state=` filters on the mark (`new` includes unmarked
  lineages) and answers 403 to anyone who is not a moderator, rather than ignoring
  the filter or applying it to their own reports, which would leak the mark
  (Q243); `GET /reports?triage_flag=` (amended) is moderators-only the same way and
  is served by the GIN index `ix_reports_triage_gin` (migration `0026`).
  `GET /reports?reporter=me` narrows any caller's listing to their own reports.
- **Media queue.** `GET /moderation/media?moderation_status=&scan_status=` lists
  completed uploads oldest first, with no presigned links (only the single
  `GET /media/{id}` presigns). An asset uploaded before its report keeps
  `media_assets.report_id` empty, so the queue resolves `report_id` from the
  newest report that lists the asset (`reports.media_ids`, GIN `jsonb_path_ops`
  index `ix_reports_media_ids_gin`); `ix_media_assets_moderation_queue` serves the
  order when filtered by status and `ix_media_assets_completed_created_at_id`
  (migration `0026`, amended) when not (Q247). Amended: the lookup is an aggregate,
  `(array_agg(id ORDER BY created_at DESC, id DESC))[1]`, not `ORDER BY ... LIMIT
  1`, which made PostgreSQL walk `ix_reports_created_at_id` backwards (about 0.7 s
  per page at 200 000 reports, against about 16 ms); an integration test checks the
  plan. Setting `media_assets.report_id` when a report is submitted or revised was
  considered and not done: it is a cross-module write on every submission, would
  change the asset's version under a moderator's decision and needs a backfill.
  Amended: `POST /moderation/media/{asset_id}/decision` takes an optional
  `If-Match`, compared inside the unit of work, so a stale approval cannot
  overwrite a rejection (Q258).
- **Verification cases by target** (amended, Q257). `GET
  /moderation/verification-cases` takes `target_id`, so the console finds an
  event's case in one call (`target_kind=event&target_id=<event id>`).
- **Moderator directory.** `GET /moderation/moderators` lists active users whose
  stored roles include `moderator` or `admin`, as `{id, display_name}` only, at most
  500 (Q248–Q250).
- **Headers in OpenAPI.** Routes declare what they set (`ETag`, `Location`, `Link`)
  with `platform/openapi_headers.header_responses`, on every moderation route, on
  `GET /reports/{id}` and on `GET /events/{id}`. What the middlewares set is added
  to the finished document once (`declare_middleware_headers`, installed in
  `main.py`): `Retry-After` on every `429`, and on an authenticated `POST`
  `Idempotent-Replayed` on success and `Retry-After` on `409`, described as sent only
  with `idempotency-key-in-use` (amended). Amended: the middleware's own answers
  (`400` `invalid-idempotency-key`, `409` `idempotency-key-reused` and
  `idempotency-key-in-use`) are added as Problem Details responses to every
  authenticated `POST` under `/api/v1` that does not declare that status itself.
- **Data checks** (amended). Migration `0026` adds
  `first_revision_supersedes_nothing`, `(revision = 1) = (supersedes_id IS NULL)`,
  after a pre-check that lists up to 50 offending report ids and stops; `0025` is
  committed and cannot list them, so on such data its own check fails instead.

**What is needed to move this ADR to `accepted`:** the maintainer confirms that marks
are internal (Q240), the reason rules (Q242), the non-atomic bulk mark (Q244) and the
amendments' defaults (Q254–Q259).

### Consequences

- Good, because nothing a reporter sends is ever refused or changed; a mark is a
  moderator's note about it, with a full, append-only history.
- Good, because a correction keeps the lineage's mark and says so
  (`revised_since`), so a moderator does not review the same observation twice
  from scratch and does not miss a change.
- Good, because one read returns a report with its mark and links; the mark has its
  own `ETag` and never changes the report's.
- Bad, because reports now carry a derived column (`lineage_id`) that the repository
  must keep right; the database check and the backfill guard it.
- Bad, because there are now two moderator-facing notions next to each other,
  marks on reports and verification cases on events and claims; the console must
  keep them apart (Q246).

## Pros and cons of the options

### Option 1, one review per lineage with history

- Good, because the unit a moderator reviews is the observation, not a revision.
- Bad, because it needs the lineage id.

### Option 2, one mark per revision

- Good, because no lineage is needed.
- Bad, because every correction would fall back to `new` and lose the moderator's
  work, or the handler would have to copy marks between revisions.

### Option 3, a verification case per report

- Good, because the state machine and history exist.
- Bad, because its states (`rejected` is terminal, Q134) contradict the maintainer's
  decision that nothing is rejected, and a report is evidence, not a claim to verify.

### Option 4, a status on the report

- Bad, because it edits a submitted report, changes its `ETag` under its reporter
  and mixes the reporter's lifecycle with the moderators' work.

## More information

- Q66, Q109, Q133, Q134, Q155; Q240–Q259 in `docs/open-questions.md`.
- ADR 0016 (idempotency keys), ADR 0017 (rate limiting), ADR 0019 (channels),
  ADR 0020 (guest submissions).
- Data dictionary: `docs/data-dictionary/reports.md`, `media.md`, `identity.md`;
  routes in `docs/architecture/api.md`.
