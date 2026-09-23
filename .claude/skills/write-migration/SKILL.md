---
name: write-migration
description: Create one Alembic migration - autogenerate with a Conventional Commit message, hand-review it, name it NNNN_<slug>.py with a linear down_revision, keep schema and data migrations separate, follow the PostGIS index conventions, and prove upgrade/downgrade and alembic check.
---

# write-migration

## When to use

- Any change to the database schema (table, column, index, constraint, extension) or any
  change to stored data that must happen once per database.
- Never to fix a migration that is already committed: write a new one.

## Preconditions

- You are `persistence-engineer` and no other migration is in progress on the branch
  (migrations are single-writer; `migrations/` is one writer at a time).
- The Alembic environment exists (Phase 1): `alembic.ini`, `migrations/env.py`,
  `migrations/script.py.mako`. `alembic.ini` sets
  `file_template = %%(rev)s_%%(slug)s`, `truncate_slug_length = 60` and
  `script_location = %(here)s/migrations` (so tests that load `alembic.ini` from
  `Path(__file__).resolve().parents[n]` work from any working directory); `env.py`
  imports every module's `infrastructure/orm.py`, uses `yakhnama.platform.db.Base.metadata`
  as `target_metadata`, and registers GeoAlchemy2's Alembic helpers
  (`geoalchemy2.alembic_helpers`) so geometry columns and spatial indexes render.
- `Base.metadata` has the naming convention below (architect, `platform/db.py`), so
  `op.f("...")` names are deterministic:

  | Object | Convention | Example |
  |--------|------------|---------|
  | primary key | `pk_%(table_name)s` | `pk_places` |
  | foreign key | `fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s` | `fk_places_parent_id_places` |
  | index | `ix_%(column_0_label)s` | `ix_places_parent_id` |
  | unique | `uq_%(table_name)s_%(column_0_name)s` | `uq_place_names_value` |
  | check | `ck_%(table_name)s_%(constraint_name)s` | `ck_impact_claims_value_non_negative` |
  | GiST (explicit) | `ix_<table>_<column>_gist` | `ix_places_geometry_gist` |

- The ORM change (`add-entity`) is written and the local database is at `head`:
  `poetry run poe up` then `poetry run poe migrate` (both from Phase 1).
- The `migrate` Poe task is added in Phase 1 (`test-integration` already exists and runs
  `pytest tests/integration -m integration`); if `migrate` is missing, stop and report
  rather than running ad-hoc commands in CI.

## Owning subagent

`persistence-engineer` only, one migration at a time. The guard hook blocks edits to
committed migrations.

## Files

| Path | Create/modify | Purpose |
|------|---------------|---------|
| `migrations/versions/NNNN_<slug>.py` | create | The migration (next free four-digit number) |
| `tests/integration/migrations/test_migrations.py` | create once | Upgrade/downgrade round trip and `alembic check` |
| `tests/architecture/test_migration_layout.py` | create once | Naming, linear history, contiguous numbers |
| `docs/data-dictionary/<m>.md` | modify | New columns and their meaning |

## Steps

1. Find the next number: `ls migrations/versions` and take the highest `NNNN` plus one.
   The current head is its `down_revision`: `poetry run alembic heads` must print exactly
   one revision.
2. Generate with the Conventional Commit message as the revision message and the number
   as the revision id:

   ```bash
   poetry run alembic revision --autogenerate --rev-id 0002 -m "feat(places): create places table"
   ```

   This writes `migrations/versions/0002_feat_places_create_places_table.py`.
3. **Hand-review** the generated file against this checklist and fix it by hand:
   - [ ] `revision` equals the file number, `down_revision` is the previous head
         (linear; no merge revisions, no `branch_labels`);
   - [ ] module docstring: the commit message, `Revision ID`, `Revises`, and one line
         saying whether this is a schema or a data migration;
   - [ ] only the intended objects appear; autogenerate noise (spatial tables of
         PostGIS itself, `tiger`/`topology` schemas, spurious type changes) is removed;
   - [ ] every name comes from the convention (`op.f(...)` or the explicit GiST name);
   - [ ] geometry columns are `Geometry(..., srid=4326, spatial_index=False)` with an
         explicit `postgresql_using="gist"` index;
   - [ ] timestamps are `DateTime(timezone=True)`;
   - [ ] `NOT NULL` on a populated table goes through expand → backfill (separate data
         migration) → contract;
   - [ ] large indexes on populated tables are created with
         `postgresql_concurrently=True` inside `op.get_context().autocommit_block()`;
   - [ ] `downgrade()` reverses `upgrade()` exactly, in reverse order;
   - [ ] no data changes in a schema migration, no DDL in a data migration;
   - [ ] no destructive downgrade of verified data without an ADR (downgrade of a data
         migration may be a documented no-op, never a `DELETE` of verified rows).
4. Data migrations use `op.get_bind()` with `sa.text(...)` and bound parameters, work in
   bounded batches, are idempotent, and never import ORM models (models change; the
   migration must not).
5. Apply and prove reversibility locally, then run the checks:

   ```bash
   poetry run poe migrate
   poetry run alembic downgrade -1
   poetry run poe migrate
   poetry run alembic check
   ```

6. Update the data dictionary for every new column.
7. Commit the migration alone or with its ORM change; from then on it is immutable.

## Templates

### `migrations/versions/0002_feat_places_create_places_table.py` (schema migration)
```python
"""feat(places): create places table.

Revision ID: 0002
Revises: 0001
Create Date: 2025-01-01T00:00:00+00:00

Schema migration only; no data is written here.
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``places`` with its foreign key, b-tree and GiST indexes."""
    op.create_table(
        "places",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("level", sa.String(length=32), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column(
            "geometry",
            geoalchemy2.types.Geometry(
                geometry_type="GEOMETRY",
                srid=4326,
                spatial_index=False,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revised_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["places.id"],
            name=op.f("fk_places_parent_id_places"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_places")),
    )
    op.create_index(op.f("ix_places_parent_id"), "places", ["parent_id"])
    op.create_index(
        "ix_places_geometry_gist",
        "places",
        ["geometry"],
        postgresql_using="gist",
    )


def downgrade() -> None:
    """Drop everything ``upgrade`` created, in reverse order."""
    op.drop_index("ix_places_geometry_gist", table_name="places")
    op.drop_index(op.f("ix_places_parent_id"), table_name="places")
    op.drop_table("places")
```

### `migrations/versions/0005_chore_places_backfill_revised_at.py` (data migration)
```python
"""chore(places): backfill places.revised_at from created_at.

Revision ID: 0005
Revises: 0004
Create Date: 2025-01-01T00:00:00+00:00

Data migration only. It runs after 0004 added ``revised_at`` as nullable and before
0006 makes it ``NOT NULL`` (expand, backfill, contract).

Downgrade is a deliberate no-op: the rows cannot tell which values were backfilled,
and downgrading 0004 drops the column anyway. Upgrade is safe to re-run.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BATCH_SIZE = 5_000

_BACKFILL = sa.text(
    """
    UPDATE places
    SET revised_at = created_at
    WHERE id IN (
        SELECT id FROM places WHERE revised_at IS NULL LIMIT :batch_size
    )
    """,
)


def upgrade() -> None:
    """Copy ``created_at`` into ``revised_at`` in bounded batches."""
    connection = op.get_bind()
    # Batches keep each UPDATE short so the table is never locked for long.
    while connection.execute(_BACKFILL, {"batch_size": BATCH_SIZE}).rowcount:
        pass


def downgrade() -> None:
    """Leave data unchanged; see the module docstring for why."""
```

`migrations/script.py.mako` should render the same shape (typed module-level
`revision`/`down_revision`/`branch_labels`/`depends_on`, docstrings on `upgrade` and
`downgrade`) so generated files pass Ruff and mypy after review. `pyproject.toml`
excludes `migrations/versions` from Ruff and does not list `migrations` in mypy's
`files`, so review is the only gate; both templates above were nevertheless checked with
Ruff `--select ALL` and mypy strict, and generated files should reach the same bar.

### `tests/integration/migrations/test_migrations.py` (create once)
```python
"""Migration round-trip tests against an empty PostGIS database."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

pytestmark = pytest.mark.integration

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    """Return an Alembic config pointed at the empty test database."""
    config = Config(REPOSITORY_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


# Sync tests on purpose: env.py drives its own event loop with asyncio.run, which
# fails inside a running pytest-asyncio loop.
def test_migrations_upgrade_head_then_downgrade_base_succeeds(
    alembic_config: Config,
) -> None:
    command.upgrade(alembic_config, "head")

    command.downgrade(alembic_config, "base")

    command.upgrade(alembic_config, "head")


def test_migrations_at_head_match_orm_models_with_no_pending_changes(
    alembic_config: Config,
) -> None:
    command.upgrade(alembic_config, "head")

    command.check(alembic_config)
```

`database_url` is an integration fixture (test-engineer, Phase 1) pointing at an empty
PostGIS database.

### `tests/architecture/test_migration_layout.py` (create once)
```python
"""Structural checks on the Alembic revision files (no database needed)."""

import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VERSIONS = REPOSITORY_ROOT / "migrations" / "versions"
FILE_NAME = re.compile(r"^(?P<number>\d{4})_[a-z0-9_]+\.py$")


def _script_directory() -> ScriptDirectory:
    """Load the revision graph from ``alembic.ini``."""
    return ScriptDirectory.from_config(Config(REPOSITORY_ROOT / "alembic.ini"))


def test_migration_files_are_named_nnnn_slug() -> None:
    names = sorted(path.name for path in VERSIONS.glob("*.py"))

    mismatched = [name for name in names if FILE_NAME.match(name) is None]

    assert mismatched == []


def test_migration_history_is_linear_with_single_head() -> None:
    script = _script_directory()

    heads = script.get_heads()

    assert len(heads) == 1


def test_migration_revision_ids_match_file_numbers() -> None:
    script = _script_directory()

    mismatched = [
        revision.revision
        for revision in script.walk_revisions()
        if revision.path is None
        or not Path(revision.path).name.startswith(f"{revision.revision}_")
    ]

    assert mismatched == []


def test_migration_numbers_are_contiguous_from_0001() -> None:
    numbers = sorted(
        int(match["number"])
        for path in VERSIONS.glob("*.py")
        if (match := FILE_NAME.match(path.name)) is not None
    )

    expected = list(range(1, len(numbers) + 1))

    assert numbers == expected
```

## Required tests

- `test_migrations_upgrade_head_then_downgrade_base_succeeds` — empty database, `upgrade
  head`, `downgrade base`, `upgrade head` again.
- `test_migrations_at_head_match_orm_models_with_no_pending_changes` — `alembic check`.
- `test_migration_files_are_named_nnnn_slug`,
  `test_migration_history_is_linear_with_single_head`,
  `test_migration_revision_ids_match_file_numbers`,
  `test_migration_numbers_are_contiguous_from_0001`.
- For a data migration: an integration test that seeds rows at the previous revision,
  upgrades, and asserts the transformed values (and that re-running is harmless).

## Checks

```bash
poetry run poe lint
poetry run poe typecheck
poetry run poe up
poetry run poe migrate              # task added in Phase 1
poetry run alembic check
poetry run poe test-integration     # PostGIS/MinIO fixtures from Phase 1
poetry run poe test-api             # runs tests/architecture
poetry run poe check
```

## Definition of Done

- [ ] File named `NNNN_<slug>.py`, `revision == "NNNN"`, linear `down_revision`, single
      head.
- [ ] Hand-review checklist completed; names follow the convention; GiST on geometry.
- [ ] Schema and data changes are in separate migrations.
- [ ] `downgrade()` reverses `upgrade()`; upgrade → downgrade base → upgrade passes on an
      empty database; `alembic check` is clean.
- [ ] Data dictionary updated.
- [ ] `standards-reviewer` approved (`security-reviewer` if personal data columns change).
- [ ] Conventional Commit matching the migration message.
- [ ] `poetry run poe check` passes.

## Pitfalls

- **Editing a committed migration.** Blocked by the guard hook and forbidden; write a new
  one.
- **Async `env.py` inside async tests.** `env.py` calls `asyncio.run`; migration tests
  are plain `def` tests.
- **Autogenerate misses things.** It does not detect renamed columns (it drops and adds),
  changed server defaults reliably, or enum value changes. Write those by hand.
- **Two heads.** Parallel branches that both add `0007` produce two heads; renumber the
  unmerged one before merging, never add a merge revision.
- **PostGIS extension.** `CREATE EXTENSION postgis` belongs to the first migration only;
  never drop it in a downgrade other than that one.
- **Slug length.** Long commit messages are truncated by `truncate_slug_length`; keep the
  message short enough to stay readable.
