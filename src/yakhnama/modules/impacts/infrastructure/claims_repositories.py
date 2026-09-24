"""SQLAlchemy adapters of the claim, asset and damage record repository ports.

The ports live in ``application/claims_ports.py``. Writes go straight to the unit of
work's transaction; inserts run inside a savepoint so a unique violation leaves the
transaction usable, and rows are expunged as soon as they are read or written.

A unique violation on a claim insert is a taken id or a second correction of the
same claim (``supersedes_id`` is unique); on an asset insert a taken id or an
OpenStreetMap element already registered (``osm_id`` is unique). All are conflicts.

**Optimistic concurrency.** Claims and damage records change only by retraction,
but two moderators retracting (or correcting) the same claim at once must not both
win. Each repository remembers the version of every aggregate it loaded or stored in
this unit of work and updates the row only ``WHERE version = <remembered version>``
(``version - 1`` for one not loaded here); a lost race raises ``ConflictError``, a
missing row the module's not-found error.

There is no delete: claims and damage records are append-only (``AGENTS.md`` §5).

Patterns: Repository (adapter side).
"""

from collections.abc import Sequence
from typing import Final

from sqlalchemy import Select, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.impacts.domain.assets import InfrastructureAsset
from yakhnama.modules.impacts.domain.claims import ImpactClaim
from yakhnama.modules.impacts.domain.damage import DamageRecord
from yakhnama.modules.impacts.domain.errors import (
    DamageRecordNotFoundError,
    ImpactClaimNotFoundError,
)
from yakhnama.modules.impacts.infrastructure.claims_mappers import (
    asset_to_row,
    claim_to_row,
    claim_to_values,
    damage_to_row,
    damage_to_values,
    row_to_asset,
    row_to_claim,
    row_to_damage,
)
from yakhnama.modules.impacts.infrastructure.claims_orm import (
    DamageRecordRow,
    ImpactClaimRow,
    InfrastructureAssetRow,
)
from yakhnama.platform.db import Base, is_unique_violation
from yakhnama.shared_kernel.errors import ConflictError
from yakhnama.shared_kernel.ids import EntityId

_CLAIM: Final = "impact claim"
_DAMAGE_RECORD: Final = "damage record"


async def _insert(session: AsyncSession, row: Base, message: str) -> None:
    """Insert ``row`` in a savepoint, turning a unique violation into a conflict.

    Raises:
        ConflictError: On a unique violation, with ``message``.
        sqlalchemy.exc.IntegrityError: On any other integrity error.
    """
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as error:
        if not is_unique_violation(error):
            raise
        raise ConflictError(message) from error
    session.expunge(row)


def _stale(
    kind: str, aggregate_id: EntityId, expected: int, stored: int
) -> ConflictError:
    message = (
        f"{kind} {aggregate_id} was changed concurrently (expected version {expected})"
    )
    return ConflictError(
        message,
        details={
            "id": str(aggregate_id),
            "expected_version": expected,
            "stored_version": stored,
        },
    )


class SqlAlchemyImpactClaimRepository:
    """PostgreSQL-backed implementation of ``ImpactClaimRepository``.

    The session belongs to the unit of work; this class never commits.

    Implements: Repository (port ``ImpactClaimRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session
        self._versions: dict[EntityId, int] = {}

    async def get(self, claim_id: EntityId) -> ImpactClaim | None:
        """Return one claim, active or retracted.

        Args:
            claim_id: The claim.

        Returns:
            The aggregate, or ``None``.
        """
        row = await self._session.scalar(
            select(ImpactClaimRow).where(ImpactClaimRow.id == claim_id)
        )
        if row is None:
            return None
        self._session.expunge(row)
        return self._track(row_to_claim(row))

    async def list_for_event(self, event_id: EntityId) -> Sequence[ImpactClaim]:
        """Return every claim of an event, active and retracted.

        Args:
            event_id: The hazard event.

        Returns:
            The claims ordered by ``created_at``, then id.
        """
        rows = (
            await self._session.scalars(
                select(ImpactClaimRow)
                .where(ImpactClaimRow.event_id == event_id)
                .order_by(ImpactClaimRow.created_at, ImpactClaimRow.id)
            )
        ).all()
        for row in rows:
            self._session.expunge(row)
        return [self._track(row_to_claim(row)) for row in rows]

    async def add(self, claim: ImpactClaim) -> None:
        """Insert a new claim or correction.

        Args:
            claim: The new aggregate.

        Raises:
            ConflictError: If the id is taken, or another correction already
                supersedes the same claim.
            sqlalchemy.exc.IntegrityError: If the metric, asset or superseded claim
                is not stored, which the application layer rules out.
        """
        await _insert(
            self._session,
            claim_to_row(claim),
            f"impact claim {claim.id} or a correction of the same claim exists",
        )
        self._track(claim)

    async def save(self, claim: ImpactClaim) -> None:
        """Update a stored claim (a retraction), checking optimistic concurrency.

        Args:
            claim: The new state.

        Raises:
            ImpactClaimNotFoundError: If the claim is not stored.
            ConflictError: If it changed since it was loaded in this unit of work.
        """
        expected = self._versions.get(claim.id, claim.version - 1)
        statement = (
            update(ImpactClaimRow)
            .where(ImpactClaimRow.id == claim.id, ImpactClaimRow.version == expected)
            .values(claim_to_values(claim))
            .returning(ImpactClaimRow.id)
            .execution_options(synchronize_session=False)
        )
        if claim.version > expected and (
            (await self._session.execute(statement)).scalar_one_or_none() is not None
        ):
            self._track(claim)
            return
        stored = await self._session.scalar(
            select(ImpactClaimRow.version).where(ImpactClaimRow.id == claim.id)
        )
        if stored is None:
            message = f"impact claim {claim.id} does not exist"
            raise ImpactClaimNotFoundError(message, details={"claim_id": str(claim.id)})
        raise _stale(_CLAIM, claim.id, expected, stored)

    def _track(self, claim: ImpactClaim) -> ImpactClaim:
        self._versions[claim.id] = claim.version
        return claim


class SqlAlchemyInfrastructureAssetRepository:
    """PostgreSQL-backed implementation of ``InfrastructureAssetRepository``.

    The session belongs to the unit of work; this class never commits.

    Implements: Repository (port ``InfrastructureAssetRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session

    async def get(self, asset_id: EntityId) -> InfrastructureAsset | None:
        """Return one asset.

        Args:
            asset_id: The asset.

        Returns:
            The aggregate, or ``None``.
        """
        return await self._one(
            select(InfrastructureAssetRow).where(InfrastructureAssetRow.id == asset_id)
        )

    async def get_by_osm_id(self, osm_id: str) -> InfrastructureAsset | None:
        """Return the asset registered for an OpenStreetMap element.

        Args:
            osm_id: The element, for example ``way/123456``.

        Returns:
            The aggregate, or ``None``.
        """
        return await self._one(
            select(InfrastructureAssetRow).where(
                InfrastructureAssetRow.osm_id == osm_id
            )
        )

    async def _one(
        self, statement: Select[tuple[InfrastructureAssetRow]]
    ) -> InfrastructureAsset | None:
        row = await self._session.scalar(statement)
        if row is None:
            return None
        self._session.expunge(row)
        return row_to_asset(row)

    async def add(self, asset: InfrastructureAsset) -> None:
        """Insert a new asset.

        Args:
            asset: The new aggregate.

        Raises:
            ConflictError: If the id or the OpenStreetMap element is taken.
        """
        await _insert(
            self._session,
            asset_to_row(asset),
            f"asset {asset.id} or OpenStreetMap element {asset.osm_id} exists",
        )


class SqlAlchemyDamageRecordRepository:
    """PostgreSQL-backed implementation of ``DamageRecordRepository``.

    The session belongs to the unit of work; this class never commits.

    Implements: Repository (port ``DamageRecordRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session
        self._versions: dict[EntityId, int] = {}

    async def get(self, damage_id: EntityId) -> DamageRecord | None:
        """Return one damage record, active or retracted.

        Args:
            damage_id: The record.

        Returns:
            The aggregate, or ``None``.
        """
        row = await self._session.scalar(
            select(DamageRecordRow).where(DamageRecordRow.id == damage_id)
        )
        if row is None:
            return None
        self._session.expunge(row)
        record = row_to_damage(row)
        self._versions[record.id] = record.version
        return record

    async def add(self, record: DamageRecord) -> None:
        """Insert a new damage record.

        Args:
            record: The new aggregate.

        Raises:
            ConflictError: If the id is taken.
            sqlalchemy.exc.IntegrityError: If the asset is not stored, which the
                application layer rules out.
        """
        await _insert(
            self._session, damage_to_row(record), f"damage record {record.id} exists"
        )
        self._versions[record.id] = record.version

    async def save(self, record: DamageRecord) -> None:
        """Update a stored record (a retraction), checking optimistic concurrency.

        Args:
            record: The new state.

        Raises:
            DamageRecordNotFoundError: If the record is not stored.
            ConflictError: If it changed since it was loaded in this unit of work.
        """
        expected = self._versions.get(record.id, record.version - 1)
        statement = (
            update(DamageRecordRow)
            .where(DamageRecordRow.id == record.id, DamageRecordRow.version == expected)
            .values(damage_to_values(record))
            .returning(DamageRecordRow.id)
            .execution_options(synchronize_session=False)
        )
        if record.version > expected and (
            (await self._session.execute(statement)).scalar_one_or_none() is not None
        ):
            self._versions[record.id] = record.version
            return
        stored = await self._session.scalar(
            select(DamageRecordRow.version).where(DamageRecordRow.id == record.id)
        )
        if stored is None:
            message = f"damage record {record.id} does not exist"
            raise DamageRecordNotFoundError(
                message, details={"damage_id": str(record.id)}
            )
        raise _stale(_DAMAGE_RECORD, record.id, expected, stored)
