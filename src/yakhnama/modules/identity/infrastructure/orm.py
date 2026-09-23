"""SQLAlchemy row models of the ``identity`` bounded context.

Rows are persistence shapes only (ADR 0004): ``mappers.py`` translates them to and
from the frozen ``User``, ``Organization`` and ``Membership`` aggregates, and nothing
outside this package sees them.

A user row mirrors an OpenID Connect identity keyed by ``(issuer, subject)``; the only
personal data it holds is the optional ``display_name``. ``roles`` is a JSONB array of
role strings, written sorted so the stored value of a role set is deterministic.

Patterns: Adapter (ORM row models behind the repository and query service adapters).
"""

from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from yakhnama.platform.db import Base

USERS_TABLE: Final = "users"
ORGANIZATIONS_TABLE: Final = "organizations"
MEMBERSHIPS_TABLE: Final = "memberships"


class UserRow(Base):
    """Row model of the ``users`` table: one row per ``User`` mirror.

    Implements: Adapter (ORM row model of ``SqlAlchemyUserRepository``).

    Attributes:
        id: Primary key, the aggregate id (UUIDv7).
        issuer: The OIDC ``iss`` the identity comes from, compared exactly.
        subject: The OIDC ``sub`` within that issuer, compared exactly.
        display_name: Optional display name, the only personal data stored.
        roles: Platform roles held explicitly, as a sorted JSON array of strings.
        status: ``active`` or ``suspended``.
        status_reason: Why the user was suspended.
        version: Optimistic-concurrency version, compared on every update.
        created_at: First mirrored, UTC.
        updated_at: Last changed, UTC.
        last_seen_at: Last authenticated request, UTC; never moves backwards.
    """

    __tablename__ = USERS_TABLE
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_users_issuer_subject"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    # Lengths follow the domain bounds: ISSUER_MAX_LENGTH and the OIDC 255-character
    # limit on "sub" (OIDC Core 1.0 §2).
    issuer: Mapped[str] = mapped_column(String(512))
    subject: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(120))
    roles: Mapped[list[str]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16))
    status_reason: Mapped[str | None] = mapped_column(String(500))
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]


class OrganizationRow(Base):
    """Row model of the ``organizations`` table: one row per ``Organization``.

    Implements: Adapter (ORM row model of ``SqlAlchemyOrganizationRepository``).

    Attributes:
        id: Primary key, the aggregate id (UUIDv7).
        slug: URL-safe handle, unique.
        name: Display name.
        organization_type: ``OrganizationType`` value.
        status: ``active``, ``suspended`` or ``retired``.
        status_reason: Why it was suspended or retired.
        version: Optimistic-concurrency version, compared on every update.
        created_at: First recorded, UTC.
        updated_at: Last changed, UTC.
    """

    __tablename__ = ORGANIZATIONS_TABLE

    id: Mapped[UUID] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    organization_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    status_reason: Mapped[str | None] = mapped_column(String(500))
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class MembershipRow(Base):
    """Row model of the ``memberships`` table: one user in one organisation.

    Rows go with their organisation or user (``ON DELETE CASCADE``): a membership is a
    relation, not verified data, and neither users nor organisations are ever
    hard-deleted by the application (they are suspended or retired).

    Implements: Adapter (ORM row model of ``SqlAlchemyMembershipRepository``).

    Attributes:
        id: Primary key, the membership id (UUIDv7).
        organization_id: The organisation.
        user_id: The member.
        role: ``member`` or ``admin``.
        version: Optimistic-concurrency version, compared on every update.
        created_at: When the user joined, UTC.
        updated_at: Last changed, UTC.
    """

    __tablename__ = MEMBERSHIPS_TABLE
    __table_args__ = (
        # Leads with organization_id, so it also serves every per-organisation
        # lookup; user_id gets its own index for "my memberships".
        UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_memberships_organization_id_user_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
