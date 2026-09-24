"""Alembic environment: runs migrations over the async asyncpg engine.

The database URL comes from ``yakhnama.platform.settings.Settings`` (the
``YAKHNAMA_DATABASE_URL`` environment variable or ``.env``) unless the caller passes
``-x database_url=postgresql+asyncpg://...``. Either way it goes through the same
``Settings`` validation, so only the asyncpg driver is accepted.

``target_metadata`` is ``yakhnama.platform.db.Base.metadata``. A table is only part of
that metadata once the module defining its model has been imported, so every ORM
module is listed in ``MODEL_MODULES`` and imported below. **Each module's
``infrastructure/orm.py`` must be added to ``MODEL_MODULES`` when it lands**, otherwise
autogenerate proposes dropping its tables and ``alembic check`` fails.

Patterns: none (Alembic entry-point script; it defines no classes).
"""

import asyncio
import importlib
from logging.config import fileConfig
from typing import Final, Literal

from alembic import context
from geoalchemy2 import alembic_helpers
from pydantic import PostgresDsn
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.sql.schema import SchemaItem

from yakhnama.platform.db import Base
from yakhnama.platform.settings import Settings

# Every module that registers tables on Base.metadata. Append each module's
# ``yakhnama.modules.<m>.infrastructure.orm`` here as it is written.
MODEL_MODULES: Final = (
    "yakhnama.platform.outbox.models",
    "yakhnama.platform.idempotency.models",
    "yakhnama.modules.geography.infrastructure.orm",
    "yakhnama.modules.hazards.infrastructure.orm",
    "yakhnama.modules.impacts.infrastructure.orm",
    "yakhnama.modules.identity.infrastructure.orm",
    "yakhnama.modules.provenance.infrastructure.orm",
    "yakhnama.modules.audit.infrastructure.orm",
    "yakhnama.modules.reports.infrastructure.orm",
    "yakhnama.modules.media.infrastructure.orm",
    "yakhnama.modules.events.infrastructure.orm",
    "yakhnama.modules.verification.infrastructure.orm",
    "yakhnama.modules.impacts.infrastructure.claims_orm",
    "yakhnama.modules.ingestion.infrastructure.orm",
    "yakhnama.modules.exchange.infrastructure.orm",
)

# Tables that PostGIS itself creates in the public schema. They are not ours, so
# autogenerate must neither drop nor compare them.
# The raster catalog views of postgis_raster are named exactly rather than matched by
# a "raster_" prefix: a prefix would also hide the application's own raster_assets
# table (migration 0016) from autogenerate and let alembic check miss its drift.
POSTGIS_TABLES: Final = frozenset(
    {
        "spatial_ref_sys",
        "geography_columns",
        "geometry_columns",
        "raster_columns",
        "raster_overviews",
    }
)

VERSION_TABLE: Final = "alembic_version"
DATABASE_URL_ARGUMENT: Final = "database_url"

# Every migration session runs in UTC, like the application's sessions
# (platform/db.py), so server-side now() and timestamp rendering agree.
# search_path is pinned to public because the official PostGIS image installs
# postgis_tiger_geocoder and postgis_topology and adds their schemas to the database's
# search_path; PostgreSQL reflection of the default schema lists every visible table,
# so autogenerate would otherwise propose dropping the tiger and topology tables.
SERVER_SETTINGS: Final = {"timezone": "UTC", "search_path": "public"}

for module_name in MODEL_MODULES:
    importlib.import_module(module_name)

config = context.config

# Tests run Alembic in-process and set "configure_logger" to False: fileConfig would
# replace the root logger's handlers, including pytest's capture handler.
if config.config_file_name is not None and config.attributes.get(
    "configure_logger", True
):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def resolve_database_url() -> str:
    """Return the database URL for this run.

    Returns:
        The ``-x database_url=...`` value if given, otherwise the configured
        ``Settings().database_url``, validated either way.

    Raises:
        pydantic.ValidationError: If the URL is not a ``postgresql+asyncpg`` DSN.
            The error never contains the URL itself (``hide_input_in_errors``).
    """
    arguments = context.get_x_argument(as_dictionary=True)
    override = arguments.get(DATABASE_URL_ARGUMENT)
    if override is None:
        settings = Settings()
    else:
        # _env_file=None: an explicit URL must not be mixed with a local .env.
        settings = Settings(_env_file=None, database_url=PostgresDsn(override))
    return settings.database_url.unicode_string()


def include_object(
    item: SchemaItem,
    name: str | None,
    type_: Literal[
        "schema",
        "table",
        "column",
        "index",
        "unique_constraint",
        "foreign_key_constraint",
        "check_constraint",
    ],
    reflected: bool,  # noqa: FBT001  # reason: positional signature fixed by Alembic
    compare_to: SchemaItem | None,
) -> bool:
    """Tell autogenerate whether to consider a database object.

    Args:
        item: The table, column, index or constraint being compared.
        name: Its name.
        type_: What kind of object it is.
        reflected: Whether it came from the database rather than the metadata.
        compare_to: The object it is compared with, if any.

    Returns:
        ``False`` for PostGIS-managed tables and Alembic's version table, ``True``
        for everything else.
    """
    # GeoAlchemy2's own include_object is not used: it targets SpatiaLite and
    # GeoPackage and would also hide any real table whose name starts with "idx_".
    del item, reflected, compare_to
    if type_ != "table" or name is None:
        return True
    return not (name == VERSION_TABLE or name in POSTGIS_TABLES)


def _configure_context(connection: Connection | None, url: str | None) -> None:
    """Configure the migration context with the project's comparison options."""
    context.configure(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        version_table=VERSION_TABLE,
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
        # Batch mode exists for SQLite's limited ALTER TABLE; PostgreSQL needs none.
        render_as_batch=False,
        # Render geometry types with their geoalchemy2 import. GeoAlchemy2's
        # "writer" is not registered: it rewrites create_table into
        # create_geospatial_table, while the project declares GiST indexes explicitly
        # and uses plain op.create_table (write-migration skill).
        render_item=alembic_helpers.render_item,
        literal_binds=connection is None,
        dialect_opts={"paramstyle": "named"} if connection is None else {},
    )


def run_migrations_offline() -> None:
    """Emit the migration SQL to stdout without connecting (``alembic --sql``)."""
    _configure_context(connection=None, url=resolve_database_url())
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations_on(connection: Connection) -> None:
    """Run the migrations on a synchronous connection handed over by ``run_sync``."""
    _configure_context(connection=connection, url=None)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Connect through asyncpg and run the migrations in one transaction."""
    # NullPool: a migration run opens exactly one connection and must not keep it.
    engine = create_async_engine(
        resolve_database_url(),
        poolclass=pool.NullPool,
        hide_parameters=True,
        connect_args={"server_settings": dict(SERVER_SETTINGS)},
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations_on)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
