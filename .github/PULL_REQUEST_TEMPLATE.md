## Summary

<!-- What does this change do, and why? One or two sentences. -->

## Linked issue

Closes #

## Type of change

- [ ] `feat` — new feature
- [ ] `fix` — bug fix
- [ ] `docs` — documentation only
- [ ] `refactor` — no behaviour change
- [ ] `test` — tests only
- [ ] `chore` / `build` / `ci` — tooling or infrastructure
- [ ] `perf` — performance improvement

## Definition of Done

Copied verbatim from `AGENTS.md` §9. Check every box that applies to this change; explain
in a comment on any box that is intentionally left unchecked.

- [ ] Every class declares its pattern and the pattern matches §3.
- [ ] Every module, class and public function has a Google-style docstring; comments say why.
- [ ] `mypy --strict` is clean. Ruff is clean with no new unjustified ignores.
- [ ] `lint-imports` passes.
- [ ] Unit tests exist for every public function; integration tests for every adapter and
      repository.
- [ ] Coverage gates are met, including `diff-cover`.
- [ ] Any schema change has a reviewed, reversible migration.
- [ ] Any API change has its OpenAPI snapshot updated and is documented.
- [ ] The data dictionary in `docs/data-dictionary/` covers every new field, unit and meaning.
- [ ] `standards-reviewer` approved; `security-reviewer` approved where auth, personal data,
      media, uploads, the public API or dependencies were touched.
- [ ] A Conventional Commit exists on the phase branch (`feat(reports): ...`).
- [ ] `poetry run poe check` passes.

## Security-relevant?

- [ ] This change touches authentication, authorisation, personal data, media/uploads, the
      public API, or dependencies.

If checked, `security-reviewer` review is required before merge.

## Screenshots / notes

<!-- API responses, terminal output, or anything else a reviewer should see. Optional. -->
