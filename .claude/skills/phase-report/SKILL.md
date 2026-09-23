---
name: phase-report
description: Write docs/plans/phase-N-report.md at the end of a phase using exactly the fixed template - summary, delivered tasks, quality gate evidence, ADRs, deviations, open questions, risks with issue references and the proposed next phase.
---

# phase-report

## When to use

- At the end of every phase, after `poetry run poe check` has been run on the phase
  branch, and before asking the maintainer for "approved".

## Preconditions

- Every task of `docs/plans/phase-N.md` is either accepted (reviews approved,
  Conventional Commit on `phase/N-...`) or explicitly listed as not done.
- `poetry run poe check` has just been run on the head of the phase branch; you have its
  output. A report is never written from memory or from an earlier run.
- Coverage numbers come from that run (`coverage.xml` / terminal summary) and
  `poetry run poe diff-cover`.
- Every risk or piece of debt has an issue; create the issue first, then reference it.

## Owning subagent

The **lead** (main session) writes the report; it is not delegated to a subagent in
`.claude/agents/`. `docs-writer` may fix formatting afterwards, never content.

## Files

| Path | Create/modify | Purpose |
|------|---------------|---------|
| `docs/plans/phase-N-report.md` | create | The report |

## Steps

1. Run the gate and keep the output:

   ```bash
   poetry run poe check 2>&1 | tee /tmp/phase-N-check.txt
   poetry run poe diff-cover
   poetry run poe arch
   ```

2. Collect per-package coverage (overall, `domain`, `application`) from the coverage
   report, and diff coverage from `diff-cover`.
3. Collect import-linter contracts kept/broken from `poetry run poe arch`.
4. For each task: the subagent, files, tests added, final status, and the number of
   `standards-reviewer` / `security-reviewer` cycles until APPROVE.
5. List ADRs added in the phase (number, title, status).
6. List every deviation from the phase plan with its reason; "none" only if truly none.
7. List open questions, blocking first, as question / why it matters / proposed default
   / blocking yes-no.
8. List risks and debt, each with an issue reference (`#NN`).
9. Propose the next phase in a few bullets.
10. Fill the template **exactly**: same headings, same order, same table columns. Do not
    add or rename sections.

## Templates

### `docs/plans/phase-N-report.md`

````markdown
# Phase N report — <title>

## Summary

<3–5 sentences: what the phase set out to do, what was delivered, the gate result, and
what the maintainer must decide.>

## Delivered

| Task | Subagent | Files | Tests added | Status |
|------|----------|-------|-------------|--------|
| TN — <task> | <subagent or lead> | `<path>`, `<path>` | `<test path>` (<n> tests) | accepted / partial / not done |

## Quality gate

- `poetry run poe check`: PASS | FAIL

  ```text
  <pasted summary lines of each step, in the order poe check runs them: lint,
  typecheck, arch, cov, test-integration, diff-cover, security>
  ```

- Coverage: overall <n> %, domain <n> %, application <n> %, diff <n> %.
- import-linter: <k> contracts kept, <b> broken.
- Reviews: standards-reviewer <n> tasks approved (cycles: TN=<c>, ...);
  security-reviewer <n> tasks approved (cycles: TN=<c>, ...).

## ADRs added

| # | Title | Status |
|---|-------|--------|
| [NNNN](../adr/NNNN-slug.md) | <title> | proposed / accepted |

## Deviations from the plan (and why)

- <deviation> — <why> — <impact>.

## Open questions (blocking first)

| # | Question | Why it matters | Proposed default | Blocking |
|---|----------|----------------|------------------|----------|
| Qn | <question> | <why> | <default> | yes / no |

## Risks and technical debt (each with an issue reference)

| Risk or debt | Likelihood / impact | Mitigation | Issue |
|--------------|---------------------|------------|-------|
| <item> | low / medium / high | <mitigation> | #NN |

## Proposed next phase (brief)

- <goal>
- <main tasks>
- <what must be approved first>
````

## Required tests

None as code. The report is valid only if:

- the gate output pasted is from the head commit of the phase branch;
- every task in `docs/plans/phase-N.md` has a row under *Delivered*;
- every risk row has an issue reference;
- headings match the template exactly (a reviewer can diff them).

## Checks

```bash
poetry run poe check
poetry run poe diff-cover
poetry run poe arch
grep -c '^## ' docs/plans/phase-N-report.md   # 8 sections, as in the template
```

## Definition of Done

- [ ] Report at `docs/plans/phase-N-report.md` with the exact template headings.
- [ ] Gate result and coverage figures come from a fresh run on the branch head.
- [ ] Every planned task accounted for; review cycle counts given.
- [ ] Deviations, open questions (blocking first) and risks (with issues) listed.
- [ ] Committed as `docs(plans): add phase N report`.
- [ ] Maintainer asked for "approved"; no next-phase work starts before it.

## Pitfalls

- **Stale numbers.** Re-run the gate after the last merge; a report from an older commit
  misleads.
- **"Done" with a red gate.** If `poe check` fails, the status line says FAIL and the
  failing step is pasted; the phase is not complete.
- **Hiding partial work.** A task that is partial says so in *Status* and in
  *Deviations*.
- **Risks without issues.** Create the issue first; "TBD" is not a reference.
