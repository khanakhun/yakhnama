# 0020. Guest submissions with a proof-of-work challenge and a capability

- Date: 2026-10-05
- Status: proposed
- Deciders: lead agent, maintainer
- Amended: 2026-10-05, after the security and code review of the guest flow: two
  hourly caps instead of one, counted under a database lock; an adaptive difficulty
  with an 18-bit production floor and a 22-bit ceiling; photo and report slots
  reserved before any asset or source exists; receipt replay for 24 hours after
  expiry; a purge task with a grace margin on the database's clock; uploads bound to
  their exact size; a shorter guest upload URL. The sections below describe the
  amended decision.

## Context and problem statement

On 2026-10-05 the maintainer decided that people without an account may report
(**guest reports**), with up to three photos, and that no third-party CAPTCHA may be
used, because no third party may learn who reports. The web portal calls the API from
its own server (a backend-for-frontend), so every anonymous request reaches the API from
the portal's address: the per-IP anonymous rate limit of ADR 0017 sees all guests as one
client. Every other report has a user as its reporter and its media's owner, and the
reports, media and provenance modules assume an actor.

How does an anonymous person get permission to submit exactly one report with up to
three photos, how is abuse kept down without a CAPTCHA or the client's address, and how
are the guest report's reporter, media owner and source represented without weakening
the reports module's invariants?

## Decision drivers

- Privacy: no third party in the path; no personal data about the guest; secrets never
  in URLs or logs.
- Abuse resistance that does not rely on the IP address (portal Q-W9).
- Retry safety on weak mobile connections.
- Honest domain invariants: no fake users, no nullable fields that every policy must
  remember to check.
- Stateless where cheap; the database as the only shared state (several API
  processes).

## Considered options

1. A signed SHA-256 proof-of-work challenge, redeemed once for a short-lived
   **capability** bound to one **guest submission**; the submission is the report's
   reporter and the photos' owner; platform-owned sources; a global hourly cap
   and global hourly caps (proposed)
2. A nullable `reporter_id` on reports and media, plus a guest flag
3. A shared system "guest" user that reports for every guest
4. A third-party CAPTCHA or proof-of-humanity service

## Decision outcome

Proposed option: **1**.

- **Challenge.** `POST /guest-submissions/challenges` returns a challenge signed with
  HMAC-SHA256 under `guest_challenge_secret`:
  `v1.<salt>.<difficulty_bits>.<expires_at>.<signature>`, with a 128-bit random salt,
  a difficulty and an expiry (`guest_challenge_ttl_seconds`, default 600). Nothing is
  stored. The client finds a decimal `nonce` with `SHA-256(salt + nonce)` starting with
  `difficulty_bits` zero bits; a phone needs seconds, a bot farm pays per submission.
  In production the secret must be URL-safe base64 of at least 32 random bytes (at
  least 16 distinct byte values) and differ from every other configured secret.
- **Adaptive difficulty.** The difficulty is `base + opened // step`, capped at `max`:
  the base `guest_pow_difficulty_bits` (default 18, at least 18 in production;
  development and tests may go lower, end-to-end tests use 8), one more bit for every
  `guest_pow_difficulty_step` (default 200) submissions opened in the last hour, never
  more than `guest_pow_difficulty_max_bits` (default and ceiling 22). It is signed into
  the challenge, so a client cannot lower it. The portal computes SHA-256 with WebCrypto
  in a worker, about 30 000 to 50 000 hashes a second on a Moto G4; the expected solve
  time on that phone is:

  | Bits | Expected hashes | Moto G4, average |
  |------|-----------------|------------------|
  | 18 | 262 144 | 5–9 s |
  | 19 | 524 288 | 10–17 s |
  | 20 | 1 048 576 | 21–35 s |
  | 21 | 2 097 152 | 42–70 s |
  | 22 | 4 194 304 | 85–140 s |

  The number of hashes is a geometric draw, so about one guest in twenty needs three
  times the average. A quiet hour costs an honest guest only the base; a flood makes
  every further submission more expensive for everyone, the flood included.
- **Redemption.** `POST /guest-submissions` checks the signature, the expiry and the
  answer, takes the opened cap's lock, checks both caps, records the salt in
  `guest_challenges` (primary key, so a replay or a race is `409 guest-challenge-spent`)
  and opens a `GuestSubmission` in the same transaction. The insert itself refuses a
  challenge that expired by the **database's** clock, the clock the purge uses, so a
  challenge can never be redeemed again after its record was purged, whatever the API
  servers' clocks say. The periodic task `reports.purge_guest_records` deletes spent
  salts five minutes after they expired (the margin covers redemptions still running).
- **Capability.** 256 random bits, returned once, stored only as SHA-256, compared in
  constant time, valid for `guest_capability_ttl_seconds` (default 1800; the setting
  replaces `guest_capability_ttl_minutes`). The client sends it in the
  `Guest-Capability` request header; log redaction treats any key containing
  `capability`, `challenge` or `nonce` as secret, and no access log records request
  headers. A wrong or missing capability and an
  unknown submission are one error (`403 guest-capability-invalid`); an expired one has
  its own (`403 guest-capability-expired`). 403, not 401: no bearer scheme is involved,
  and the portal must not take the answer for an expired session.
- **One report, three photos, reserved first.** The submission grants at most three
  image uploads (`409 guest-media-limit` for a fourth) and exactly one report. Each
  grant first **reserves a photo slot** for a new asset id (a version-checked save of
  the submission, retried when parallel uploads of the same guest collide), and only
  then does the media module create exactly that asset, its platform source and the
  presigned URL; a fourth request creates nothing, and a grant that fails on the server
  gives its slot back. The report is **reserved** the same way before its source exists:
  under the reports cap's lock the submission records the content's SHA-256
  fingerprint, the id its platform source will have and the submission time; then the
  source is registered under that id (idempotently), and the report is stored and the
  submission filed (report id, receipt reference) in one reports unit of work. A request
  that loses either race reloads and returns the winner's receipt, so concurrent
  identical submits store one report and one source. A retry with the same content
  returns the same receipt, also for `guest_receipt_grace_seconds` (default 24 hours)
  after the capability expired: replay wins over expiry. Different content, or an upload
  after the reservation, is `409 guest-submission-closed`. A photo granted before the
  report may still be completed until the capability expires; it is not added to the
  report (Q228). The receipt is a reference `YK-XXXX-XXXX` (about 39 bits from an
  alphabet without look-alike characters, unique).
- **Representation.** The **guest submission is the report's reporter** and the
  **owner of its photos**. Its id comes from the same UUIDv7 generator as user ids, so
  it never equals a user's: "the reporter" policies (`IsSelf`) never match a guest
  report (nobody can revise or withdraw it) and the media ownership checks and triage
  photo lookup work unchanged. The report's `channel` is `guest` (ADR 0019), it names no
  organisation, and `ReportDetail.reporter_id` is `null` for it in every view. The
  submission and the report are written in one reports unit of work.
- **Provenance.** A guest has no user to own a source, so the provenance module gains
  `RegisterPlatformSource`: a `citizen` or `organisation` source owned by the system
  (`SYSTEM_OWNER`), titled "Guest community report" (and "Guest media upload" for
  photos), optionally under an id the caller reserved (then idempotent). It cannot mint
  a higher-ranked source type. `MarkPlatformSourceReferenced` freezes it only after the
  report or asset citing it is committed; a replayed receipt marks it again, which
  repairs a filing whose request died between its commit and the mark.
- **Media.** The media module gains `RequestGuestUpload` (images only, with the reserved
  asset id) and `CompleteGuestUpload`; completion checks that the submission owns the
  asset, as an account upload checks the user. Every upload grant, guest or account,
  takes the file's exact `byte_size` (1 to 50 MiB, `422` above) and signs it into the
  presigned `PUT` as `Content-Length`, so storage refuses a body of any other length
  (checked against MinIO); a guest's URL lives `guest_upload_presign_ttl_seconds`
  (default 300). The periodic task `media.sweep_stale_uploads` fails uploads still not
  completed two hours after their grant and deletes their upload objects, and the
  private bucket expires `media/upload/` after a day.
- **Rate limiting.** The guest routes stay under the anonymous per-client limit of
  ADR 0017. Behind the portal that limit is one bucket for every guest, so it is not
  the protection. Two **global caps**, counted in the database and shared by all
  processes, answer `429 rate-limited` with `Retry-After` (documented on every 429 of
  the guest routes): guest **reports filed** in any rolling hour
  (`guest_reports_per_hour`, default 200), which protects the moderators' queue, and
  submissions **opened** (`guest_submissions_per_hour`, default 2 000, much higher, so
  submissions opened and abandoned by a flood do not lock honest guests out of
  reporting). Each cap is checked under its own transaction-scoped advisory lock
  (`pg_advisory_xact_lock`, one constant key per cap), so concurrent requests are
  counted one after the other and never overshoot it. Opening a submission is also
  refused while the reports cap is reached. Per-client limiting is **not** built here:
  it needs the portal's trusted-proxy work (portal Q-W9) and, in production, a
  **per-client limit at the reverse proxy** in front of the portal; a portal-signed
  client key was considered and deferred (Q223). A flood can still delay honest guests
  for up to an hour.

**What is needed to move this ADR to `accepted`:**

1. The maintainer confirms the difficulty curve, lifetimes and caps (Q222, Q223) and
   the retention (Q226).
2. Production has a per-client limit at the reverse proxy in front of the portal
   (portal Q-W9).
3. The maintainer accepts that guest photos are granted, not completed, against the
   limit of three (Q224).

### Consequences

- Good, because no third party sees a guest, and no IP address or other personal data
  is stored about one.
- Good, because a challenge is single-use and short-lived, and a capability is useless
  without the submission id and expires in half an hour.
- Good, because the reports, media and verification code paths stay the same for guest
  reports; no nullable reporter leaks into policies.
- Good, because a lost response is safe to retry at every step except the upload grant,
  and a retried report gets its receipt for a day after the capability expired.
- Good, because concurrent requests can neither overshoot a cap nor leave orphan
  assets or sources: slots and reports are reserved before anything is created.
- Bad, because proof of work only raises the cost of abuse; a determined attacker with
  hardware can still fill the hourly caps, and then honest guests wait (Q223). The
  adaptive difficulty makes that flood dearer but also makes honest guests in it wait
  up to a few minutes on a low-end phone.
- Bad, because the guest cannot revise or withdraw the report, and can only quote a
  reference no route looks up yet (Q225).
- Bad, because `reporter_id` and `owner_id` now hold either a user id or a submission
  id; code must not assume they name a user (documented on the fields).

## Pros and cons of the options

### Option 1, proof of work and a capability

- Good, because it needs no new infrastructure and no third party.
- Bad, because it costs a low-end phone a few seconds of computation.

### Option 2, nullable reporter

- Good, because it reads naturally.
- Bad, because every policy, query and adapter that reads `reporter_id` or `owner_id`
  must handle `None`, and media ownership would need a second key anyway.

### Option 3, a shared guest user

- Good, because nothing changes in the model.
- Bad, because every guest report would share one reporter, so ownership checks would
  let one guest attach another guest's photos, and "the reporter" would be a lie.

### Option 4, a CAPTCHA service

- Good, because it is well understood.
- Bad, because the provider learns who reports (maintainer decision: excluded).

## More information

- Q10 (decided), Q76, Q221–Q226, Q228 in `docs/open-questions.md`; portal ADR 0013 (how the
  portal keeps the capability in a sealed cookie) and Q-W9.
- ADR 0017 (rate limiting), ADR 0019 (channels), ADR 0009 (object storage).
- `docs/architecture/api.md`, "Guest submissions"; `docs/data-dictionary/reports.md`.
