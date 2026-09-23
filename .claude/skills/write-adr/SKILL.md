---
name: write-adr
description: Record an architecture decision as a MADR file docs/adr/NNNN-title.md with the next free number, the proposed → accepted → deprecated/superseded lifecycle, and an index row in docs/adr/README.md.
---

# write-adr

## When to use

- A choice that is hard to reverse or that constrains other work: a new pattern outside
  `AGENTS.md` §3, a new dependency with architectural weight, a storage or API
  convention, a module boundary, a deviation from `AGENTS.md`.
- Superseding or deprecating an earlier decision.
- Not for choices already fixed by `AGENTS.md` §2–§5 (those change only with maintainer
  approval, and then an ADR records it).

## Preconditions

- The problem, the drivers and at least two real options are known. If a domain fact is
  unknown, it goes to `docs/open-questions.md`, not into the ADR as an assumption.
- You know the next free number: `ls docs/adr/ | sort | tail -n 3`.

## Owning subagent

`architect` writes and owns ADRs. `docs-writer` may fix formatting only. The maintainer
(or the lead with the maintainer's approval) moves an ADR to `accepted`.

## Files

| Path | Create/modify | Purpose |
|------|---------------|---------|
| `docs/adr/NNNN-<kebab-case-title>.md` | create | The decision record |
| `docs/adr/README.md` | modify | Index row |
| `docs/adr/MMMM-<older>.md` | modify (status line only) | When superseding or deprecating |

## Steps

1. Take the next free four-digit number `NNNN`; never reuse or renumber.
2. Name the file `NNNN-<title-in-kebab-case>.md`, for example
   `0013-keep-migrations-out-of-the-phase-0-gate.md`.
3. Fill the template below. Status starts as `proposed`. `Date` is today in ISO format.
4. Keep it short and factual: context as it is, drivers as a list, options with honest
   pros and cons, one decision, consequences both good and bad.
5. Add the row and the reference link to the index in `docs/adr/README.md`.
6. Lifecycle:
   - `proposed` → `accepted` when the maintainer approves (edit the status line only);
   - `accepted` → `deprecated` when no longer relevant, with one sentence why under
     *More information*;
   - `accepted` → `superseded by [PPPP](PPPP-slug.md)` when a new ADR replaces it; the
     new ADR links back under *More information*.
   After acceptance, the body is never rewritten; a changed decision is a new ADR.
7. Link the ADR from the code or doc it governs where helpful (for example a comment
   `# see ADR 0013` next to the rule it explains).

## Templates

### `docs/adr/NNNN-<title>.md` (MADR)

```markdown
# NNNN. <Title in the imperative or as a noun phrase>

- Date: YYYY-MM-DD
- Status: proposed | accepted | deprecated | superseded by [NNNN](NNNN-slug.md)
- Deciders: lead agent, maintainer

## Context and problem statement
## Decision drivers
## Considered options
## Decision outcome
### Consequences
- Good, because ...
- Bad, because ...
## Pros and cons of the options
### <Option 1>
## More information
```

Guidance per section:

- **Context and problem statement**: two to five sentences; end with the question being
  decided.
- **Decision drivers**: bullets, each a force (a rule in `AGENTS.md`, a quality goal, a
  constraint).
- **Considered options**: a numbered list, at least two. Mark the selected option with
  `(chosen)` in an accepted ADR, or `(proposed)` in a proposed one, for example
  `1. Modular monolith with hexagonal layers per bounded context (chosen)`.
- **Decision outcome**: for an accepted ADR, `Chosen option: **<option>**, because
  <reason tied to drivers>.` For a proposed ADR, `Proposed option: **<option>**, because
  <reason>.` followed by a bold line `**What is needed to move this ADR to `accepted`:**`
  and a numbered list of the confirmations still missing (see ADR 0010).
- **Consequences**: good and bad, both required.
- **Pros and cons of the options**: one `###` per option with `- Good, because ...` /
  `- Bad, because ...` bullets.
- **More information**: links to issues, plans, related ADRs, and supersession notes.

### `docs/adr/README.md` — index

The index already exists; copy its structure, never replace it. Its preamble explains
what an ADR is, then `## Numbering`, `## Status rules` (with the status table), and
`## Index`. Add one table row and one reference-style link at the bottom, in number
order:

```markdown
## Index

| ADR | Title | Status |
|-----|-------|--------|
| [0001] | Modular monolith with hexagonal layers | accepted |
| [0013] | <Title> | proposed |

[0001]: 0001-modular-monolith-with-hexagonal-layers.md
[0013]: 0013-<slug>.md
```

On a status change, edit the row's `Status` cell in the same commit as the ADR's status
line.

## Required tests

None as code. Structural expectations checked by the reviewer (and by a
`tests/architecture` check if the architect adds one):

- file name matches `^\d{4}-[a-z0-9-]+\.md$` and the number is the next free one;
- the heading number equals the file number;
- the status is one of the four allowed values;
- every file has an index row and a reference link, and every link points at an
  existing file.

## Checks

```bash
ls docs/adr/
poetry run poe docs    # serves the site locally (Ctrl+C to stop) once mkdocs.yml exists
poetry run poe check   # unchanged code still passes; required before the commit
```

## Definition of Done

- [ ] Next free number; kebab-case file name; heading matches.
- [ ] All MADR sections present, in the template's order; at least two options.
- [ ] Status `proposed` (or updated through the lifecycle only).
- [ ] Index row and reference link added; superseded ADR's status line and index row
      updated.
- [ ] No invented domain facts; open questions referenced instead.
- [ ] `standards-reviewer` approved.
- [ ] Conventional Commit, for example `docs(adr): record 0013 migrations gate`.

## Pitfalls

- **Rewriting history.** Accepted ADRs are not edited except for the status line.
- **One-option ADRs.** If there was no real alternative, it is not a decision worth an
  ADR, or the alternatives were not explored.
- **Decisions hidden in plans.** A decision in `docs/plans/phase-N.md` that outlives the
  phase needs an ADR.
- **Numbering races.** Two branches taking the same number: the later merge renumbers
  before merging, never after acceptance.
