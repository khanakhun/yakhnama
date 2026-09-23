# Contributing to Yakhnama

Thank you for helping build the open record of high-mountain hazards in Gilgit-Baltistan.
This document covers how to set up, how we branch and commit, and what a change needs
before it merges. `AGENTS.md` is the binding contract behind all of this; where the two
disagree, `AGENTS.md` wins.

## Setup

Requirements: Python ≥ 3.13, [Poetry](https://python-poetry.org/) 2.x, Docker with
Compose v2. Get a 3.13 interpreter with `uv python install 3.13` (uv only downloads the
interpreter here; it never manages this project's dependencies), or with pyenv or the
deadsnakes PPA.

```bash
git clone https://github.com/khanakhun/yakhnama.git
cd yakhnama

poetry install
cp .env.example .env

poetry run poe up            # PostGIS, MinIO, Keycloak, Redis
poetry run pre-commit install

poetry run poe check         # everything CI runs — run this before you open a PR
```

## Branch model

- `main` is protected. Nobody commits to it directly, including the maintainer.
- Phased work happens on `phase/N-<slug>` branches (for example `phase/0-foundation`),
  planned in `docs/plans/phase-N.md` and approved by the maintainer before any code of
  that phase is written.
- Contributor changes outside a phase branch use `feat/<slug>`, `fix/<slug>` or
  `docs/<slug>` branches.
- Everything merges into `main` by pull request.

## Commits

We use [Conventional Commits](https://www.conventionalcommits.org/). `commitizen` is
configured as a `commit-msg` hook and also maintains `CHANGELOG.md`.

```
<type>(<scope>): <description>

feat(reports): add report triage chain of responsibility
fix(verification): correct state-machine transition guard
docs(readme): document the quick start
```

- `type` is one of `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `build`, `ci`,
  `perf` (standard Conventional Commits types).
- `scope` is a module name (`reports`, `events`, `hazards`, `verification`, `identity`,
  `ingestion`, `exchange`, …), or `shared_kernel`, `platform`, `docs`, `ci` for
  cross-cutting changes.
- Dependencies change only via `poetry add` / `poetry remove` (`AGENTS.md` §4), with one
  accepted exception: Dependabot's `pip`-ecosystem updates (see `.github/dependabot.yml`)
  modify `poetry.lock` through Poetry's own dependency resolver, not by hand.

## Pre-commit hooks

Installed once with `poetry run pre-commit install`, then run automatically on every
commit: `ruff format`, `ruff check`, `mypy`, `lint-imports`, `gitleaks`,
`poetry check --lock`, `commitizen` (on the commit message), and the end-of-file and
trailing-whitespace fixers. Run them on demand with:

```bash
poetry run pre-commit run --all-files
```

Never skip a hook (`--no-verify`) to get a commit through. If a hook is wrong, fix the
hook, don't bypass it.

## Definition of Done

Every task must meet the Definition of Done in `AGENTS.md` §9 before it is considered
complete: pattern declarations, docstrings, a clean `mypy --strict` and `lint-imports`,
tests for every public function and every adapter, coverage gates including `diff-cover`,
reviewed and reversible migrations for schema changes, an updated OpenAPI snapshot for API
changes, an updated data dictionary entry for new fields, `standards-reviewer` approval
(and `security-reviewer` approval where auth, personal data, media, uploads, the public
API or dependencies were touched), a Conventional Commit, and a passing
`poetry run poe check`.

## Running the test tiers

| Tier | Command | Notes |
|------|---------|-------|
| Unit | `poetry run poe test-unit` | `tests/unit/`, no I/O, uses fakes from `tests/fakes/` (created with the first module in Phase 1) |
| API | `poetry run poe test-api` | `tests/api/` and `tests/architecture/`, `httpx.AsyncClient` against the app factory |
| Coverage gate | `poetry run poe cov` | All non-integration tests, fails under 90 %; the 95 % gate for `domain` and `application` is added with the first module in Phase 1 |
| Diff coverage | `poetry run poe diff-cover` | ≥ 90 % coverage on lines changed against `main` |
| Security | `poetry run poe security` | `gitleaks` secret scan, `pip-audit` dependency audit |
| Integration | `poetry run poe test-integration` | Tests against real tools and services (git, ruff and mypy for the hooks now; PostGIS and MinIO via testcontainers from Phase 1); never mock the database |
| Everything | `poetry run poe check` | The full gate CI runs, in order |

## How to add things

Each kind of change has a repeatable recipe as a skill in `.claude/skills/<name>/SKILL.md`
(exact files, templates, required tests and checks), summarised from `AGENTS.md` §8:

| Want to add | Skill | Owner |
|-------------|-------|-------|
| A bounded context | `new-module` | architect scaffolds; layer owners fill |
| An entity or value object | `add-entity` | domain-modeler → persistence-engineer |
| A command or query | `add-command`, `add-query` | application-engineer |
| An endpoint | `add-api-endpoint` | api-engineer |
| A migration | `write-migration` | persistence-engineer only, one at a time |
| A hazard type | `add-hazard-type` | domain-modeler |
| An impact metric | `add-impact-metric` | domain-modeler |
| An external data source | `add-source-adapter` | integration-engineer |
| An import/export format | `add-exchange-format` | integration-engineer |
| An architecture decision | `write-adr` | architect |
| A phase report | `phase-report` | lead |

If you are unsure which recipe applies, ask in the pull request description rather than
improvising a new pattern; new patterns go through an ADR first (`AGENTS.md` §3).

## Domain uncertainty

Never invent a domain fact — a hazard definition, a metric's semantics, an administrative
boundary, a local place name, a threshold. If your change depends on one that isn't
settled, add it to `docs/open-questions.md` in the existing format (question, why it
matters, proposed default, blocking yes/no) instead of guessing.

## Review process

Every pull request is reviewed by `standards-reviewer` (read-only; conformance to
`AGENTS.md`) and, where the change touches authentication, personal data, media uploads,
the public API or dependencies, by `security-reviewer` as well. Both return either
`APPROVE` or a numbered list of required changes. CODEOWNERS review is also required (see
branch protection below). Address every required change; don't merge around a reviewer.

## AI-assisted contributions

AI-assisted contributions are welcome. They go through exactly the same gate as any other
change: the full Definition of Done, the same reviewers, the same CI checks. Disclose in
the PR description that the change is AI-assisted; that does not change what is required
of it.

## Branch protection for `main`

`main` is configured with, at minimum:

- Require a pull request before merging; no direct pushes.
- Require these status checks to pass before merging: `lint`, `test-unit`, `test-api`, `cov`,
  `test-integration`, `diff-cover`, `security` (and `migrations`, `contract` once they are
  active for the current phase, per `ci.yml`). These names are job ids; each job runs once
  per Python-version matrix entry, so the status check actually shown on a PR carries the
  job's `name:` and the matrix value, for example `test-unit` appears as
  "Unit tests (py3.13)" and "Unit tests (py3.14)".
- Require a linear history.
- No force pushes.
- No branch deletions.
- Require review from Code Owners (`.github/CODEOWNERS`).
- Dismiss stale pull request approvals when new commits are pushed.

## Getting help

Open a discussion or an issue using the templates in `.github/ISSUE_TEMPLATE/`. For
security issues, do not open a public issue — see `SECURITY.md`.
