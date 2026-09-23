"""Database engine, session factory and the declarative base for every ORM model.

Every ``modules/<m>/infrastructure/orm.py`` model derives from ``Base`` so that one
``Base.metadata`` describes the whole schema; Alembic's ``migrations/env.py`` uses it as
``target_metadata``. The metadata carries a naming convention so that every constraint
and index gets a deterministic name and ``op.f("...")`` in a migration always matches
what autogenerate produces (``write-migration`` skill):

==============  ============================================================
Object          Convention
==============  ============================================================
primary key     ``pk_%(table_name)s``
foreign key     ``fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s``
index           ``ix_%(column_0_label)s``
unique          ``uq_%(table_name)s_%(column_0_name)s``
check           ``ck_%(table_name)s_%(constraint_name)s``
==============  ============================================================

A check constraint therefore needs an explicit short ``name`` (for example
``CheckConstraint("attempts >= 0", name="attempts_non_negative")`` becomes
``ck_outbox_messages_attempts_non_negative``). Spatial columns come from GeoAlchemy2
in the module's ``orm.py`` with ``spatial_index=False``, and their GiST index is
declared explicitly as ``ix_<table>_<column>_gist`` so that it is visible in review.

Annotated columns map ``datetime`` to ``TIMESTAMP WITH TIME ZONE`` and ``uuid.UUID``
to the native ``uuid`` type, so a model cannot declare a naive timestamp by accident
(``AGENTS.md`` §4, hard rules).

Patterns: Adapter (SQLAlchemy is the adapter behind the repository and unit-of-work
ports).
"""

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Any, ClassVar, Final
from uuid import UUID

from sqlalchemy import MetaData, Uuid
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeEngine

from yakhnama.platform.settings import Settings

NAMING_CONVENTION: Final = MappingProxyType(
    {
        "pk": "pk_%(table_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
    }
)

# One shared instance for explicit ``mapped_column(UtcDateTime)`` declarations, the same
# type the annotation map uses for ``Mapped[datetime]``.
UtcDateTime: Final = TIMESTAMP(timezone=True)

# Every session starts in UTC so that server-side functions such as ``now()`` and any
# text rendering of timestamps agree with the application (AGENTS.md §4).
_SERVER_SETTINGS: Final = MappingProxyType({"timezone": "UTC"})


class Base(DeclarativeBase):
    """Declarative base of every ORM model, carrying the shared naming convention.

    ORM models are persistence shapes only; mappers in each module translate them to
    and from frozen domain entities (ADR 0004).

    Implements: Adapter (ORM mapping base).

    Attributes:
        metadata: The schema of every model, with ``NAMING_CONVENTION`` applied.
        type_annotation_map: ``datetime`` to ``TIMESTAMP WITH TIME ZONE`` and
            ``uuid.UUID`` to the native ``uuid`` type.
    """

    metadata = MetaData(naming_convention=dict(NAMING_CONVENTION))
    # Any mirrors SQLAlchemy's own ``_TypeAnnotationMapType``: keys are arbitrary Python
    # types and each TypeEngine is generic over a different value type.
    type_annotation_map: ClassVar[Mapping[Any, TypeEngine[Any]]] = {
        datetime: UtcDateTime,
        UUID: Uuid(as_uuid=True),
    }


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the application's async engine; no connection is opened yet.

    Bound parameters are always hidden from SQLAlchemy's logging and error messages,
    even with ``database_echo`` on, because they can hold personal data from reports
    (``AGENTS.md`` §5). ``pool_pre_ping`` replaces connections the server dropped
    (a restart, an idle timeout) instead of failing the next request.

    Args:
        settings: Supplies ``database_url``, ``database_pool_size`` and
            ``database_echo``.

    Returns:
        An ``AsyncEngine`` using the asyncpg driver with every session in UTC.
    """
    return create_async_engine(
        settings.database_url.unicode_string(),
        pool_size=settings.database_pool_size,
        pool_pre_ping=True,
        echo=settings.database_echo,
        hide_parameters=True,
        connect_args={"server_settings": dict(_SERVER_SETTINGS)},
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create the factory that opens one ``AsyncSession`` per unit of work.

    ``expire_on_commit=False`` because async sessions cannot lazy-load expired
    attributes: reading a mapped attribute after commit would otherwise raise.

    Args:
        engine: The engine sessions connect through.

    Returns:
        A session factory bound to ``engine``.
    """
    return async_sessionmaker(engine, expire_on_commit=False)
