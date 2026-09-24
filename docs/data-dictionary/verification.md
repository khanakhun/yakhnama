# Data dictionary — verification

The `verification` module owns the `VerificationCase` of each report, event and impact
claim: its state, its append-only transition history and its reviewer. Conventions are
in [README.md](README.md).

**Provenance legend.** `proposed` means the rule is a default chosen by the domain
modeller, not a confirmed domain fact; each is listed under "Open questions" below.

Code: `src/yakhnama/modules/verification/domain/`.

## Verification case (`VerificationCase`, aggregate root)

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `id` | UUID (v7) | — | Identifier of the case. | generated (`IdGenerator`) | Phase 3 |
| `target.kind` | enum `report \| event \| claim` | — | What the case verifies. | application | Phase 3 |
| `target.target_id` | UUID (v7) | — | The report, event or impact claim. One case per target for life. | application | Phase 3 |
| `initial_state` | enum, `draft` or `submitted` | — | The state the case was opened in (see "Initial states"). | computed | Phase 3 |
| `state` | `VerificationState` | — | The current state; always the `to_state` of the last transition, or `initial_state` without history. | computed | Phase 3 |
| `history[]` | `Transition` | — | Every move since opening, oldest first; append-only and re-checked against the table on every load. | computed | Phase 3 |
| `assigned_to` | UUID (v7) or null | — | Reviewer currently responsible. Cannot be set on a `rejected` or `retracted` case. | moderator | Phase 3 |
| `opened_by` | UUID (v7) | — | Who opened the case. | computed | Phase 3 |
| `version` | int ≥ 1 | — | Starts at 1, +1 per transition or assignment. | computed | Phase 3 |
| `created_at`, `updated_at` | datetime (UTC) | — | Opening and last change; exact instants; `updated_at` is never before the last transition. | computed (`Clock`) | Phase 3 |

## Transition (`Transition`, value object)

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `from_state`, `to_state` | `VerificationState` | — | The move; must be in the transition table. | moderator or automated check | Phase 3 |
| `actor_id` | UUID (v7) | — | Who moved the case: a user, or the service account of an automated process. Always required. | computed | Phase 3 |
| `reason` | safe text, 1–1000, line breaks allowed, or null | — | Why; required for every target state except `submitted`. Blank text counts as missing. Never published in domain events. | moderator | Phase 3 |
| `occurred_at` | datetime (UTC) | — | When; transitions are in chronological order. | computed (`Clock`) | Phase 3 |
| `is_human` | bool | — | Whether a person made the move; `verified` requires `true`. | application | Phase 3 |

## States and the transition table (`AGENTS.md` §6.3)

`VerificationState`: `draft | submitted | under_review | verified | rejected |
needs_information | disputed | retracted`.

| from | allowed to |
|------|------------|
| `draft` | `submitted` |
| `submitted` | `under_review` |
| `under_review` | `verified`, `rejected`, `needs_information` |
| `needs_information` | `submitted` |
| `verified` | `disputed`, `retracted` |
| `disputed` | `under_review` |
| `rejected` | — (terminal) |
| `retracted` | — (terminal) |

Checks on `VerificationCase.transition`, in order: the move is in the table
(`InvalidTransitionError`), a move to `verified` is made by a human
(`HumanRequiredError`), a reason is present unless the target is `submitted`
(`ReasonRequiredError`).

## Initial states (proposed)

| target kind | opens in | why |
|-------------|----------|-----|
| `report` | `submitted` | a report exists only once submitted |
| `event` | `draft` | a moderator drafts an event before putting it up for review |
| `claim` | `submitted` | a claim is recorded with its source in one step |

## Domain events

Every event has `aggregate_type = "verification_case"`, `target_kind` and `target_id`.
Reason text is never in a payload.

| `event_type` | payload beyond the base fields |
|--------------|--------------------------------|
| `verification.verification_case_opened` | `initial_state`, `opened_by` |
| `verification.verification_transitioned` | `from_state`, `to_state`, `actor_id`, `is_human`, `has_reason` |
| `verification.verification_assigned` | `reviewer_id`, `previous_reviewer_id`, `assigned_by` |

## Persistence

`verification_cases` (migration `0013_verification`) stores `initial_state`
as its own `NOT NULL` column, separate from `state`: `state` is the case's
current, moving position, while `initial_state` is fixed at row creation and
never updated, so the state the case was opened in (see "Initial states"
above) stays recoverable even after `history` has grown past it. `history` is
stored as one JSONB array per row (replayed against the transition table on
every read, as the domain requires) rather than a separate table, since a
case's history is always read and written as a whole with the case. `UNIQUE
(target_kind, target_id)` enforces "one case per target for life" at the
database level; it also serves the events read model's join on
`target_kind = 'event'`. `target_id` carries no foreign key, because reports,
events and claims belong to other modules. Indexes on `state`, `assigned_to`
and `(created_at, id)` serve the moderator listings.

## Open questions

- **Initial states.** Reports and claims open in `submitted`, events in `draft`.
  Default: as proposed.
- **Terminal states.** The §6.3 table lists no move out of `rejected` or `retracted`,
  so both are terminal; a correction is a new report revision or a new claim.
  Default: terminal.
- **Dispute rules.** Who may move a `verified` case to `disputed` (any moderator, the
  original reviewer, an external party through a moderator), and whether an automated
  check may dispute. Default: any actor with a reason; authorisation in the application.
- **Automated rejection.** Only `verified` requires a human, so an automated check may
  move a case to `rejected` or `needs_information`. Default: allowed, per the
  table's literal reading; the application may restrict it.
- **Assignment on closed cases.** Rejected and retracted cases cannot be assigned.
  Default: as proposed.
