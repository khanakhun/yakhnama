@AGENTS.md

# Claude Code notes

Everything binding is in `AGENTS.md`. This file only adds how the Claude Code harness is
wired for this repository.

## Roles

- The **lead** (main session) plans, delegates, reviews, integrates and gates. It writes
  code only for the Phase 0 bootstrap and small integration glue.
- **Subagents** in `.claude/agents/` do the work. Each one owns specific paths and returns
  a structured report. Implementers write their own tests; `test-engineer` raises quality.
- `standards-reviewer` and `security-reviewer` are read-only and return `APPROVE` or a
  numbered list of required changes.

## Delegation brief

Every subagent task is briefed with: Goal (one sentence), Read first (`AGENTS.md`, the
skill, specific files), You own (exact paths), You may read, Acceptance criteria, Must pass
(exact commands), Return (files changed, tests added, command output summaries, open
questions).

## Skills

`.claude/skills/<name>/SKILL.md` holds the repeatable recipes listed in `AGENTS.md` §8. Read
the matching skill before adding a module, entity, command, query, endpoint, migration,
hazard type, impact metric, source adapter, exchange format, ADR or phase report.

## Hooks (`.claude/settings.json`)

- `PreToolUse` on `Edit|Write|MultiEdit`: `.claude/hooks/guard_protected_paths.py` blocks
  writes to `.env*`, `poetry.lock`, committed migrations, `LICENSE`, `.github/CODEOWNERS`,
  and to `AGENTS.md` from any subagent (the hook input carries `agent_id` only inside a
  subagent). It exits 2 with the reason on stderr.
- `PostToolUse` on the same tools: `.claude/hooks/format_and_check_file.py` runs
  `ruff format`, `ruff check --fix` and `mypy` on the edited Python file and exits 2 with the
  remaining errors so they are fixed immediately.

Both hooks are tested under `tests/unit/hooks/` and follow every rule in `AGENTS.md`.

## Phases

Work proceeds in phases. `docs/plans/phase-N.md` is written and approved before any code of
that phase. At the end of a phase the lead runs `poetry run poe check`, then writes
`docs/plans/phase-N-report.md` and waits for the maintainer's "approved".
