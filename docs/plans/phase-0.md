# Phase 0 plan — Foundation and agent infrastructure

Status: **awaiting maintainer approval**
Branch: `phase/0-foundation`
Lead: Fable 5.1 (orchestration only). Subagents: Opus 5.5 (`model: opus` in every agent file).

## 1. Goal

Stand up a repository in which every later change is gated by tooling, not goodwill: a
Poetry 2.x project on Python ≥ 3.13 with the full Ruff / mypy / import-linter / pytest
configuration, an app factory serving `/health/live`, the complete agent infrastructure
(`AGENTS.md`, `CLAUDE.md`, ten subagents, twelve skills, two hooks with tests), pre-commit,
CI, docker-compose, community documents and ADRs 0001–0010. Nothing domain-specific is
built in this phase.

## 2. Environment findings (pre-plan audit)

| Item | Found | Needed |
|------|-------|--------|
| OS | Ubuntu 24.04 (WSL2), 22 cores, 15 GB RAM | ok |
| Python | 3.12.3 only | 3.13 (min) and 3.14 (CI matrix) |
| Poetry | absent | 2.x |
| gitleaks, pre-commit | absent | required by hooks and CI |
| Docker / Compose | 29.6 / v5.3 | ok |
| git | 2.43, repo **not** initialised | ok |
| `uv` | present at `~/.local/bin/uv` | not used for the project (§2 rule 1) |

Toolchain installation is T0 below. It needs your approval because it changes the machine,
and because the choice of installer is yours: I propose `pipx install poetry` (or the
official installer) and `uv python install 3.13 3.14` **only as an interpreter downloader**,
never as a package manager for this project. If you prefer the deadsnakes PPA or pyenv,
say so.

## 3. Tasks

Owner "lead" means I write it myself, which §0 allows for the Phase 0 bootstrap. Subagents
cannot be used before their definition files exist (T4), so tasks after T4 are delegated.
Every delegated task receives the §15 brief and goes through `standards-reviewer`; the
ones marked (S) also go through `security-reviewer`.

| # | Task | Owner | Owns (writes to) | Depends on | Acceptance criteria |
|---|------|-------|------------------|------------|---------------------|
| T0 | Toolchain: Python 3.13 + 3.14, Poetry 2.x, pipx, pre-commit, gitleaks | lead (with maintainer OK) | machine only, no repo files | approval | `poetry --version` is 2.x; `poetry env use 3.13` works; `gitleaks version`, `pre-commit --version` succeed |
| T1 | Git init; default branch `main` with no commits of work; create `phase/0-foundation`; `.gitignore`, `.editorconfig`, `.gitattributes` | lead | those files | T0 | `git branch --show-current` = `phase/0-foundation`; `main` never receives direct commits |
| T2 | `pyproject.toml` (PEP 621 `[project]`, groups `dev`/`test`/`docs`, full Ruff config from §12.1, mypy strict + pydantic plugin, import-linter contracts for the initial layers, pytest/coverage gates from §11, commitizen, all Poe tasks) and `poetry.lock` | lead | `pyproject.toml`, `poetry.lock` | T1 | `poetry check --lock` passes; `poetry install` succeeds on 3.13 and 3.14; every Poe task listed in §13.5 exists |
| T3 | `src/yakhnama/main.py` app factory; `platform/settings.py` (`pydantic-settings`, `.env` driven); `platform/logging.py` (structlog JSON, redaction processor stub with tests); `/health/live`; `shared_kernel/__init__.py` and `platform/__init__.py` with module docstrings; `tests/unit`, `tests/api` for the above; `tests/architecture/test_structure.py` (every package has a docstring; `main.py` is the only place that builds the app) | lead | `src/yakhnama/`, `tests/` | T2 | `poe test-unit`, `poe test-api` green; coverage ≥ 90 % overall; `mypy --strict` clean; `lint-imports` green |
| T4 | `AGENTS.md` (≤ 400 lines, all §13.1 sections incl. glossary and Definition of Done); `CLAUDE.md` (`@AGENTS.md` + Claude notes); ten subagent files in `.claude/agents/` with `name`, `description`, `tools`, `model: opus` | lead | `AGENTS.md`, `CLAUDE.md`, `.claude/agents/` | T2 | Each agent lists its owned paths and return format exactly as §13.2; read-only agents have no Write/Edit; `AGENTS.md` line count ≤ 400 |
| T5 | Hooks: `.claude/settings.json`, `.claude/hooks/guard_protected_paths.py`, `.claude/hooks/format_and_check_file.py`, `tests/unit/hooks/` (S) | lead | `.claude/settings.json`, `.claude/hooks/`, `tests/unit/hooks/` | T2, T3 | Guard exits 2 with stderr message for every protected path in §13.4 and 0 otherwise; format hook runs ruff format, ruff check --fix, mypy on the edited file and exits 2 with remaining errors; both scripts pass the full gate themselves; unit tests cover every branch |
| T6 | Twelve skills in `.claude/skills/<name>/SKILL.md` (preconditions, files, templates, required tests, checks) | docs-writer drafts, lead reviews and edits | `.claude/skills/` | T4 | Each skill names exact paths and commands; templates match §12.4 canonical style; `add-*` skills reference the owning subagent |
| T7 | `.pre-commit-config.yaml` (order of §13.5) and `.github/workflows/ci.yml` (jobs of §13.5 on 3.13 / 3.14 matrix) (S) | lead | those files | T2, T5 | `pre-commit run --all-files` green locally; `ci.yml` validated with `actionlint` if available, otherwise by review; jobs that cannot run yet (see §5) are declared but gated on a path filter, never no-op'd |
| T8 | `docker-compose.yml` (PostGIS 16, MinIO, Keycloak, Redis with healthchecks and named volumes), `.env.example` (S) | lead | those files | T1 | `docker compose config` valid; `docker compose up -d` reaches healthy on all four services; `.env.example` has every setting `platform/settings.py` reads, with no real secrets |
| T9 | Community docs: `README.md`, `CONTRIBUTING.md` (incl. branch-protection documentation for `main`), `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), `SECURITY.md`, `.github/PULL_REQUEST_TEMPLATE.md`, `.github/ISSUE_TEMPLATE/` (bug, feature, data-correction), `.github/CODEOWNERS` placeholder, `LICENSE` placeholder | docs-writer | those files | T4 | README quick start actually works when followed; `LICENSE` and `CODEOWNERS` are clearly marked placeholders per §18 |
| T10 | ADRs 0001–0010 (MADR) using the `write-adr` skill | architect | `docs/adr/` | T4, T6 | Ten files numbered `0001`–`0010`, titles as §14, status `accepted` except 0010 `proposed` (licence pending) |
| T11 | `docs/open-questions.md`, `docs/architecture/README.md` (module map, Mermaid), `docs/data-dictionary/README.md` (conventions only) | docs-writer | `docs/` | T4 | Open questions use the §15.7 format (question, why it matters, proposed default, blocking yes/no) |
| T12 | Hook and test-infrastructure audit: property tests for the guard's path matcher, coverage gap fill | test-engineer | `tests/` | T5 | Coverage on `.claude/hooks/` ≥ 95 %; no mocks of our own code |
| T13 | Reviews: `standards-reviewer` on every task; `security-reviewer` on T5, T7, T8 | reviewers (read-only) | none | each task | APPROVE, or findings fixed within ≤ 3 cycles |
| T14 | Integration and gate: `poe check`, `docker compose up`, hook block demonstration, Conventional Commits per accepted task, phase report (§16) | lead | commits, `docs/plans/phase-0-report.md` | all | see §6 |

## 4. Parallelism

- **Serial spine:** T0 → T1 → T2 → T3 → T4 → T5. These touch `pyproject.toml`, `src/`, and the
  agent definitions, all of which are single-writer.
- **Parallel wave A (after T4, disjoint paths):** T6 (`.claude/skills/`), T9 (root docs and
  `.github/` templates), T11 (`docs/`), T8 (`docker-compose.yml`, `.env.example`).
- **Parallel wave B (after T5 and T6):** T7 (pre-commit, CI), T10 (`docs/adr/`), T12 (`tests/`).
- T13 reviews run as each task lands. T14 is last.

## 5. What `poe check` covers in Phase 0 (deviation, stated up front)

§13.5 lists `migrations`, `contract` and `test-integration` jobs. In Phase 0 there is no
Alembic environment, no OpenAPI snapshot (first snapshot is Phase 2 per §14) and no
repository to integration-test. I will **not** add placeholder tasks that pass trivially,
because a no-op check is a weakened check (§2). Instead `check` in Phase 0 runs
`format --check → lint → typecheck → arch → test-unit → test-api → cov → diff-cover → security`,
and the three missing tasks are added in the phase that creates the thing they test
(`migrations` in Phase 1, `contract` in Phase 2, `test-integration` in Phase 1). The CI
workflow declares all jobs from day one; the not-yet-applicable ones are skipped by an
explicit `if:` on the existence of `migrations/env.py` or `tests/contract/openapi.json`,
so they switch on automatically. This will be recorded in ADR 0011 if you disagree with
the approach; otherwise it is noted in the phase report as a deviation.

## 6. Gate (must all be true before the phase report)

- `poetry run poe check` passes on Python 3.13 (3.14 verified in CI or locally if installed).
- `docker compose up -d` brings PostGIS, MinIO, Keycloak and Redis to healthy.
- A recorded hook run shows `guard_protected_paths.py` blocking a write to `poetry.lock`
  and to `.env`, with the stderr message.
- `pre-commit run --all-files` is green.
- Every accepted task has a Conventional Commit on `phase/0-foundation`.
- `docs/plans/phase-0-report.md` follows §16.

## 7. Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Python 3.14 wheels missing for `asyncpg`, `shapely`, `geoalchemy2` or `psycopg` at lock time | low–medium | Lock on 3.13 first; if a 3.14 install fails, report it and keep 3.14 as a non-required CI job until fixed. Never pin down to 3.12. |
| Hook latency: `mypy` on every edit can take seconds | medium | Run mypy with its incremental cache in `.mypy_cache`; scope to the edited file plus followed imports; measure and report the p50 in the phase report |
| Hook cannot tell the lead from a subagent for the `AGENTS.md` rule | high | Proposed default in open question Q3 |
| Keycloak image start time (30–60 s) makes "compose up works" slow | low | Healthcheck with a generous `start_period`; document in README |
| Coverage ≥ 90 % is trivially met on a tiny codebase and may hide weak tests | medium | `test-engineer` audit (T12) and reviewer sign-off on test quality, not just the number |
| WSL2 file-system performance for testcontainers later | low | Not a Phase 0 concern; noted for Phase 1 |

## 8. Open questions for the maintainer

Blocking first.

| # | Question | Why it matters | Proposed default | Blocking |
|---|----------|----------------|------------------|----------|
| Q1 | May I install Python 3.13/3.14, Poetry 2.x, pipx, pre-commit and gitleaks on this machine, and by which method (pipx + `uv python install` as interpreter source, deadsnakes PPA, or pyenv)? | Nothing can be locked or tested without them | pipx for Poetry/pre-commit; `uv python install 3.13 3.14` for interpreters; gitleaks from its GitHub release binary | **yes** |
| Q2 | You wrote "you can also use jev if thats need i can share api key or access". I do not recognise "jev". Which tool or service did you mean? | I will not guess at an external integration or ask for credentials I may not need | Proceed without it; nothing in Phase 0 requires an external service | no |
| Q3 | How should `guard_protected_paths.py` identify "the lead" for the `AGENTS.md` rule, given hook input carries no actor identity? | The hook must be deterministic and testable | Allow the write only when the environment variable `YAKHNAMA_ACTOR=lead` is set; subagent briefs never set it, and the hook logs the decision | no |
| Q4 | Model routing: all ten subagents on Opus 5.5 as you asked. Do you want the two read-only reviewers and `docs-writer` on Opus too, or a cheaper model? | Cost versus review quality | All on Opus 5.5 | no |
| Q5 | `pyproject.toml` needs `authors` and a project URL. What name and email should appear, and is there a GitHub org/repo for the README and CODEOWNERS placeholder? | Public metadata | `authors = [{name = "Yakhnama maintainers"}]`, URL placeholder `https://github.com/<org>/yakhnama`, no email | no |
| Q6 | Initial licence placeholder text: a `LICENSE` file that literally states "Licence pending maintainer decision (AGPL-3.0 or Apache-2.0)"? | §18 reserves the decision; the file must exist for the guard rule | Yes, that text, plus a matching note in README | no |

Reply "approved" (optionally with answers to Q1–Q6) and I will execute the serial spine,
then fan out the parallel waves.
