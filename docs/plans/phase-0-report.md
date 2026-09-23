# Phase 0 report — Foundation and agent infrastructure

## Summary

The repository now exists as a Poetry 2.x project on Python 3.13 (verified on 3.14) with every
quality tool configured and enforced: Ruff, mypy strict with the Pydantic plugin, import-linter,
pytest with branch coverage, diff-cover, gitleaks and pip-audit, all wired into `poetry run poe
check`, pre-commit and a GitHub Actions matrix. A FastAPI app factory serves `/health/live`
behind typed settings and structlog logging with a personal-data redaction processor. The agent
infrastructure is complete: `AGENTS.md`, `CLAUDE.md`, ten subagents, twelve skills and two
hooks with unit and integration tests. The local stack (PostGIS 16, MinIO, Keycloak 26, Redis 7)
starts healthy with `poetry run poe up`. Community documents, issue and PR templates, Dependabot,
a MkDocs site and ADRs 0001–0011 are in place. No domain code was written, as planned.

## Delivered

| Task | Subagent | Files | Tests added | Status |
|------|----------|-------|-------------|--------|
| T0 Toolchain (Python 3.13.12 / 3.14.3, Poetry 2.5.1, pre-commit, commitizen, gitleaks 8.30.1) | lead | machine only | n/a | done |
| T1 Git baseline (`main` root commit, `phase/0-foundation`, ignore/editor/attributes) | lead | `.gitignore`, `.editorconfig`, `.gitattributes` | n/a | done |
| T2 `pyproject.toml` + `poetry.lock` (all tool config, 4 dependency groups, Poe tasks) | lead | `pyproject.toml`, `poetry.lock` | n/a | done |
| T3 App factory, settings, logging with redaction, `/health/live`, compose, `.env.example` | architect (Opus) | `src/yakhnama/**`, `docker-compose.yml`, `docker/`, `.env.example`, `tests/conftest.py`, `tests/unit/platform/`, `tests/api/`, `tests/architecture/` | 134 | done |
| T4 `AGENTS.md`, `CLAUDE.md`, ten agent definitions | lead | `AGENTS.md`, `CLAUDE.md`, `.claude/agents/` | n/a | done |
| T5 Hooks with tests | architect (Opus) | `.claude/settings.json`, `.claude/hooks/`, `tests/unit/hooks/`, `tests/integration/hooks/` | 129 unit + 20 integration | done |
| T6 Twelve skills | docs-writer role on Opus | `.claude/skills/` | 67 templates lint-checked | done |
| T7 pre-commit and CI workflow | architect (Opus) | `.pre-commit-config.yaml`, `.github/workflows/ci.yml` | validated with actionlint | done |
| T8 docker-compose and `.env.example` (folded into T3; images pinned by digest, host ports configurable) | architect (Opus) + lead glue | `docker-compose.yml`, `.env.example` | compose gate run | done |
| T9 Community docs, templates, Dependabot, CHANGELOG, MkDocs | docs-writer (Sonnet) | root `*.md`, `.github/`, `mkdocs.yml`, `docs/index.md` | `mkdocs build --strict` | done |
| T10 ADRs 0001–0010 (+0011 from review) | architect (Opus) | `docs/adr/` | n/a | done |
| T11 Open questions, architecture and data-dictionary pages | docs-writer (Sonnet) | `docs/` | n/a | done |
| T12 Hook test hardening | folded into T5 review cycle (property tests, fail-closed tests) | `tests/unit/hooks/` | included above | done |
| T13 Reviews | standards-reviewer, security-reviewer (Opus) | none | n/a | see Quality gate |
| T14 Gate, commits, report | lead | commits, this file | n/a | done |

## Quality gate

- `poetry run poe check`: **PASS**
  ```
  ruff format --check / ruff check      All checks passed
  mypy --strict                          Success: no issues found in 28 source files
  lint-imports                           Contracts: 2 kept, 0 broken
  pytest unit+api+architecture           263 passed, coverage 98.67 % (branch on, gate 90 %)
  pytest tests/integration               20 passed
  diff-cover vs main                     98 % on changed lines (gate 90 %)
  gitleaks git                           no leaks found
  pip-audit --strict                     No known vulnerabilities found
  ```
- Coverage: overall 98.67 %, domain n/a (no module yet), application n/a, diff 98 %. The 7 uncovered
  lines are the real git and subprocess adapters in the hooks, exercised only by the integration tier.
- import-linter: 2 contracts kept, 0 broken (shared_kernel framework-free; platform never imports main).
- `pre-commit run --all-files`: all 8 hooks pass. `mkdocs build --strict`: pass.
- `poetry run poe up`: PostGIS, MinIO, Keycloak and Redis healthy in about 30 s; three MinIO buckets created.
- Hook demonstration (via the exact `settings.json` command): `poetry.lock` → exit 2; `AGENTS.md` from a
  subagent → 2; `AGENTS.md` from the lead → 0; `.env.example` → 0; forged `cwd` with absolute
  `poetry.lock` → 2; NotebookEdit on `LICENSE` → 2; `CLAUDE_PROJECT_DIR` pointing at an empty dir → 2.
- Reviews: security **APPROVE after 3 cycles** (11 findings in cycle 1, 4 in cycle 2, all
  fixed and verified with crafted inputs in cycle 3). Standards: 3 cycles (21 findings in cycle
  1, 16 skill findings in cycle 2, 3 minor skill findings in cycle 3); the cycle-3 items were
  fixed by the lead and verified by re-running the gate, the structural pattern check and Ruff
  over the skill templates. No fourth standards cycle was run, which is stated here rather than
  claimed as a formal APPROVE.

## ADRs added

0001 modular monolith with hexagonal layers; 0002 PostgreSQL with PostGIS; 0003 FastAPI with app
factory; 0004 async SQLAlchemy and Alembic; 0005 OIDC-only authentication; 0006 UUIDv7 identifiers;
0007 transactional outbox; 0008 Taskiq behind a TaskQueue port; 0009 object storage for media and
rasters (all accepted); 0010 licensing (**proposed**: Apache-2.0 code, CC BY 4.0 data); 0011 catalog
entries for Settings and API Schema and 0012 catalog entries for Command, Query/DTO, Domain
Error plus `PreconditionFailedError` (both **proposed**, need your approval because they touch
`AGENTS.md` §2.3 and §3).

## Deviations from the plan (and why)

1. T3 and T5 were delegated to Opus 5.5 agents in the architect role instead of written by the lead,
   to honour the model-routing preference. The lead wrote `pyproject.toml`, `AGENTS.md`, `CLAUDE.md`,
   the agent files and integration glue.
2. `.env.example` is exempt from the `.env*` guard rule; it is documentation and must stay editable.
3. The hook command is `cd "$CLAUDE_PROJECT_DIR" && poetry run python ... || exit 2` rather than the
   bare command in the specification: Claude Code blocks only on exit 2, so any other failure would
   silently allow the write (security finding, high).
4. `poe check` runs `cov` (unit + api + architecture in one coverage run) plus `test-integration`
   instead of separate `test-unit`/`test-api` steps; CI keeps all jobs. The `migrations` and `contract`
   jobs are declared and gated on the files that make them real; `openapi-snapshot` and `migrate`
   Poe tasks are added in the phase that creates their targets rather than as broken placeholders.
5. `tests/integration/` already exists (hook adapter tests that run real git, ruff and mypy) so the
   integration job is active from Phase 0.
6. A `permissions.deny` list in `.claude/settings.json` refuses reads and file-tool writes of `.env*`,
   `poetry.lock`, `LICENSE` and `CODEOWNERS` outright, in addition to the hook.
7. MinIO images moved to quay.io (Docker Hub denies the `minio/mc` pull); bucket creation sits behind
   a compose profile because `docker compose up --wait` treats any exited container as failure.
8. Host ports are configurable in `.env` because this machine already runs PostgreSQL, Redis and a
   service on 8080.
9. `LICENSE` holds the Apache-2.0 text rather than a placeholder sentence, per your answer to Q6; ADR
   0010 stays proposed until you confirm.
10. `tests/` is a package (`__init__.py` everywhere) so `from tests.fakes import ...` works and test
    basenames may repeat across tiers.

## Open questions (blocking first)

| # | Question | Why it matters | Proposed default | Blocking |
|---|----------|----------------|------------------|----------|
| 1 | Confirm Apache-2.0 for code and CC BY 4.0 for data (ADR 0010) | Everything published carries it | As proposed | yes, before the first public release; no for Phase 1 |
| 2 | Approve the catalog rows in ADR 0011 (Settings, API Schema) and ADR 0012 (Command, Query/DTO, Domain Error, `PreconditionFailedError`) | `AGENTS.md` §2.3 and §3 are maintainer-protected | Approve | no |
| 3 | Licensing of third-party data mixed into exports (OSM is ODbL) | Cannot relicense as CC BY | Per-source licence per record/layer, listed in the sidecar | yes for accepting 0010 |
| 4 | DCO sign-off and NOTICE copyright line | Apache attribution | DCO, "Yakhnama contributors" | yes for accepting 0010 |
| 5 | Source for "13,000 glaciers" and lake counts in the README | Credibility | Cite the ICIMOD inventory | no |
| 6 | Public coordinate rounding (`public_coordinate_decimals`, default 2 ≈ 1.1 km) | Reporter safety vs usefulness | 2 | no |
| 7 | Boundary data source, taxonomy labels, IdP and hosting (carried from the specification) | Phase 1–2 inputs | OCHA COD-AB; Keycloak | no for Phase 1 |
| 8 | jev MCP for reviewer second opinions | Optional | Not now | no |

Full list with status: `docs/open-questions.md`.

## Risks and technical debt (each with an issue reference)

The GitHub repository does not exist yet, so issue numbers cannot be assigned. Each item below should
become an issue on first push; the placeholder ids are stable labels for this report.

| Ref | Item |
|-----|------|
| TD-1 | Bash-based writes bypass the path guard (only file tools are hooked); backstops are pre-commit gitleaks, CI and CODEOWNERS. |
| TD-2 | PostToolUse hook costs about 1 s warm, 3.8 s cold (mypy); measure again as `src/` grows. |
| TD-3 | Redaction is key-based and over-redacts (`allocation`, `membership`); values inside message strings are not caught. Phase 2 adds request logging without IP or query string. |
| TD-4 | Hook modules duplicate `HookInput`/root-finding code; extract when a third hook appears. |
| TD-5 | Per-layer 95 % coverage gate for `domain`/`application` is not yet enforced by tooling; add with the first module. |
| TD-6 | Subagents cannot repair hooks or `settings.json` (lead-only); accepted. |
| TD-7 | Action SHAs pinned from `git ls-remote`; Dependabot keeps them current. |
| TD-8 | `types-shapely`, `pyyaml` and `types-pyyaml` are needed by Phase 1 templates; add via `poetry add` then. |
| TD-9 | Subagents cannot write under `.claude/` (skills, agents, hooks); the lead installs their output, as happened with the skill fixes. |
| TD-10 | The skill templates assume shared-kernel names (`Clock.now`, `record_event`, cursor helpers) that Phase 1 defines; each skill states the real kernel wins. |

## Proposed next phase (brief)

Phase 1, shared kernel and reference data, as specified: `IdGenerator` (UUIDv7) and `Clock` ports,
the error hierarchy, base value objects, Specification combinators, the domain event base, Unit of
Work protocol and cursor pagination in `shared_kernel`; async engine, SQLAlchemy Unit of Work, outbox
skeleton, OpenTelemetry and the container in `platform`; the `geography`, `hazards` and `impacts`
(registry only) modules with versioned YAML seed data and an idempotent loader; Alembic with the
`migrate` task, `/health/ready`, testcontainers for PostGIS and MinIO, and the per-layer coverage gate.
The plan will be written to `docs/plans/phase-1.md` for approval.
