# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) via
[PEP 440](https://peps.python.org/pep-0440/). `commitizen` maintains this file from
Conventional Commits going forward (`cz bump` updates it on release); entries below Phase 0
were written by hand because there is no release yet.

## [Unreleased]

### Added

- Phase 0 foundation: Poetry 2.x project on Python ≥ 3.13 with the full Ruff, mypy strict,
  import-linter and pytest/coverage configuration.
- App factory (`yakhnama.main:create_app`) with structured logging and a `/health/live`
  liveness endpoint.
- Agent infrastructure: `AGENTS.md`, `CLAUDE.md`, subagent definitions and skills.
- Pre-commit hooks and the CI workflow (`ci.yml`).
- docker-compose services for local development: PostGIS, MinIO, Keycloak, Redis.
- Community documents: `README.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`,
  `SECURITY.md`, issue and pull request templates, Dependabot configuration.
- Initial architecture decision records in `docs/adr/`.
- Documentation site scaffold (`mkdocs.yml`, `docs/`), including the architecture overview,
  data dictionary conventions and open questions log.
