# 0019. Reporting channels and assisted reporting with recorded consent

- Date: 2026-10-05
- Status: proposed
- Deciders: lead agent, maintainer
- Amended: 2026-10-05, after review: the database now also checks that a guest report
  names no organisation and that the consent method and statement version have the
  domain's values (migration `0021`); the channel index serves the non-account queues
  only; consent fields and the note are redacted from logs.

## Context and problem statement

ADR 0005 makes every actor an OIDC account, and Q10 asked how people without an
account, or who need help, put an observation on record. On 2026-10-05 the maintainer
decided both: **guest reports** (no account, ADR 0020) and **assisted reports** (someone
with an account enters a report on behalf of a person without one, with that person's
consent). Moderators must be able to tell the three apart, an assisted report must say
how consent was given without identifying the assisted person, and the provenance
record must stay honest about who stated what. The portal (its plan, phase 2, task W4)
offers "I am reporting for someone else" to trusted reporters, organisation members and
moderators.

How does a report record the way it reached the platform, who may submit on behalf of
someone else, and how does provenance record an assisted report?

## Decision drivers

- Correctness and provenance (`AGENTS.md` §1): every fact links to a source, and a
  source must not claim more than is known.
- Privacy (`AGENTS.md` §5): no personal data about the assisted person; the assisting
  person's note is private.
- Reports are immutable after submission; revisions inherit attribution.
- Authorisation as composable identity policies (`AGENTS.md` §3); deny by default.
- One moderators' queue per channel without a new read model.

## Considered options

1. A `channel` on every report (`account`, `assisted`, `guest`) plus an optional
   `assisted` consent record; the person who entered the report is its reporter and
   owns its source, whose title says "assisted" (proposed)
2. Make the assisted person a pseudonymous principal of their own (a placeholder user
   per assisted person)
3. A boolean `is_assisted` and free-text consent in the description

## Decision outcome

Proposed option: **1**, because it records what is actually known (who entered the
report, that they say the observer consented, how, and to which statement) without
inventing an identity for the observer, and because it keeps the existing reporter
rules (revise and withdraw by the reporter only) meaningful.

- **Channel.** `ReportChannel` is `account`, `assisted` or `guest`, stored on every
  revision (`reports.channel`, default `account` for older rows) and carried by
  `ReportSubmitted` and `ReportRevised`. It is part of `ReportAttribution`, so every
  revision inherits it. `GET /reports?channel=...` filters by it, served for the
  moderators' guest and assisted queues by a partial index over the non-account
  channels. A guest report never names an organisation (database check).
- **Assisted record.** `AssistedSubmission`: `consent_method` (`verbal`, `written`),
  `consent_statement_version` (`^[a-z0-9][a-z0-9._-]{0,31}$`; the statement text is
  versioned by the client, the portal's message catalogues), and an optional private
  `note` (safe text, at most 500 characters, stored as plain text, never logged). It is
  set exactly for the `assisted` channel (a domain invariant and a database check, which
  also checks the method and the version's format). A revision keeps it unchanged;
  `ReviseReportRequest` does not accept it.
- **Who may assist.** `CanReportOnBehalf(organization_id)` in the identity module:
  `HasRole(trusted_reporter) | CanModerate()`, widened with
  `HasRole(org_member) & IsMemberOf(organization_id)` when the report is submitted for
  an organisation. It is checked in addition to the ordinary submit policy. A citizen
  gets `403 permission-denied`; a missing consent field is `422`.
- **Provenance.** The report's reporter is the person who entered it. Its source is
  registered by that person as for any report (`citizen`, or `organisation` with
  `organization_id`) but titled "Assisted community report" / "Assisted organisation
  report" (citation "Yakhnama assisted ..."), so whoever reads the provenance sees that
  the observation came through an intermediary. The source names nobody.
- **Visibility.** `ReportDetail.assisted` follows the exact position: shown to the
  person who entered the report and to moderators, `null` for everyone else (for
  example colleagues in the organisation, who see the rounded view).
- **Demo data.** The development realm has `demo-trusted-reporter` and
  `demo-org-member`; the seed creates a demo organisation with a fixed id and the
  membership, in the `development` and `test` environments only (an allow-list, so a
  future environment never gets them; `yakhnama.seed.demo`).

**What is needed to move this ADR to `accepted`:**

1. The maintainer confirms the set of roles that may assist (Q218).
2. A native-speaker-reviewed consent statement exists in the portal, and its first
   version string is agreed (Q219, portal Q-W36).
3. The maintainer confirms that the assisting person, not the observer, is the
   report's reporter and source owner (this ADR).

### Consequences

- Good, because moderators can queue, filter and weigh reports by channel.
- Good, because consent is recorded in a structured, auditable way that never
  identifies the observer.
- Good, because the existing reporter rules, revisions and triage work unchanged.
- Bad, because an assisted observer cannot later correct or withdraw "their" report
  themselves; only the person who entered it can (Q220).
- Bad, because consent is a claim by the assisting person; the platform cannot verify
  it, only record it.

## Pros and cons of the options

### Option 1, channel plus consent record

- Good, because it states only what is known.
- Bad, because the observer has no handle on the report.

### Option 2, a placeholder principal per assisted person

- Good, because the observer could be given access later.
- Bad, because it creates identities nobody can authenticate as, and invites storing
  personal data to tell them apart.

### Option 3, a flag and free text

- Good, because it is the smallest change.
- Bad, because consent would be unstructured, unqueryable and mixed into the public
  description.

## More information

- Q10 (decided 2026-10-05), Q218–Q221 in `docs/open-questions.md`.
- ADR 0020 (guest reports), ADR 0005 (OIDC-only authentication).
- Data dictionary: `docs/data-dictionary/reports.md`, `identity.md`.
