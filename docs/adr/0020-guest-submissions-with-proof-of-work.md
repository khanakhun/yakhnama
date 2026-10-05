# 0020. Guest submissions with a proof-of-work challenge and a capability

- Date: 2026-10-05
- Status: proposed
- Deciders: lead agent, maintainer

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
   (proposed)
2. A nullable `reporter_id` on reports and media, plus a guest flag
3. A shared system "guest" user that reports for every guest
4. A third-party CAPTCHA or proof-of-humanity service

## Decision outcome

Proposed option: **1**.

- **Challenge.** `POST /guest-submissions/challenges` returns a challenge signed with
  HMAC-SHA256 under `guest_challenge_secret`:
  `v1.<salt>.<difficulty_bits>.<expires_at>.<signature>`, with a 128-bit random salt,
  the configured difficulty (`guest_pow_difficulty_bits`, default 16, at least 12 in
  production) and an expiry (`guest_challenge_ttl_seconds`, default 600). Nothing is
  stored. The client finds a decimal `nonce` with `SHA-256(salt + nonce)` starting with
  `difficulty_bits` zero bits; a phone needs seconds, a bot farm pays per submission.
- **Redemption.** `POST /guest-submissions` checks the signature, the expiry and the
  answer, checks the global cap, records the salt in `guest_challenges` (primary key, so
  a replay or a race is `409 guest-challenge-spent`) and opens a `GuestSubmission` in the
  same transaction. Spent salts are kept only until the challenge expires.
- **Capability.** 256 random bits, returned once, stored only as SHA-256, compared in
  constant time, valid for `guest_capability_ttl_minutes` (default 30). The client sends
  it in the `Guest-Capability` request header. A wrong or missing capability and an
  unknown submission are one error (`403 guest-capability-invalid`); an expired one has
  its own (`403 guest-capability-expired`). 403, not 401: no bearer scheme is involved,
  and the portal must not take the answer for an expired session.
- **One report, three photos.** The submission grants at most three image uploads
  (`409 guest-media-limit` for a fourth) and exactly one report. A retry with the same
  content (same SHA-256 fingerprint) returns the same receipt; different content, or an
  upload after the report, is `409 guest-submission-closed`. The receipt is a reference
  `YK-XXXX-XXXX` (about 39 bits from an alphabet without look-alike characters, unique).
- **Representation.** The **guest submission is the report's reporter** and the
  **owner of its photos**. Its id comes from the same UUIDv7 generator as user ids, so
  it never equals a user's: "the reporter" policies (`IsSelf`) never match a guest
  report (nobody can revise or withdraw it) and the media ownership checks and triage
  photo lookup work unchanged. The report's `channel` is `guest` (ADR 0019), it names no
  organisation, and `ReportDetail.reporter_id` is `null` for it in every view. The
  submission and the report are written in one reports unit of work.
- **Provenance.** A guest has no user to own a source, so the provenance module gains
  `RegisterPlatformSource`: a `citizen` or `organisation` source owned by the system
  (`SYSTEM_OWNER`), marked referenced at once, titled "Guest community report" (and
  "Guest media upload" for photos). It cannot mint a higher-ranked source type.
- **Media.** The media module gains `RequestGuestUpload` (images only) and
  `CompleteGuestUpload`; completion checks that the submission owns the asset, as an
  account upload checks the user.
- **Rate limiting.** The guest routes stay under the anonymous per-client limit of
  ADR 0017. Behind the portal that limit is one bucket for every guest, so it is not
  the protection: a **global cap** on submissions opened in any rolling hour
  (`guest_submissions_per_hour`, default 200, counted in the database, so shared by all
  processes) answers `429 rate-limited` with `Retry-After`, and in production a
  **per-client limit at the reverse proxy** in front of the portal is required (portal
  Q-W9). The cap protects the moderators' queue, not individual guests; a flood can
  delay honest guests for up to an hour (Q223).

**What is needed to move this ADR to `accepted`:**

1. The maintainer confirms the difficulty, lifetimes and cap (Q222, Q223).
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
- Good, because a lost response is safe to retry at every step except the upload grant.
- Bad, because proof of work only raises the cost of abuse; a determined attacker with
  hardware can still fill the hourly cap, and then honest guests wait (Q223).
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

- Q10 (decided), Q76, Q221–Q226 in `docs/open-questions.md`; portal ADR 0013 (how the
  portal keeps the capability in a sealed cookie) and Q-W9.
- ADR 0017 (rate limiting), ADR 0019 (channels), ADR 0009 (object storage).
- `docs/architecture/api.md`, "Guest submissions"; `docs/data-dictionary/reports.md`.
