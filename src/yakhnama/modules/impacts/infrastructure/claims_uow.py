"""The SQLAlchemy unit of work for impact claims, assets and damage records.

It composes the existing metric repository with the three claims repositories on
one session, so a claim is validated against its metric and stored in the same
transaction.

Patterns: Unit of Work.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.impacts.application.claims_ports import (
    DamageRecordRepository,
    ImpactClaimRepository,
    InfrastructureAssetRepository,
)
from yakhnama.modules.impacts.application.ports import ImpactMetricRepository
from yakhnama.modules.impacts.infrastructure.claims_repositories import (
    SqlAlchemyDamageRecordRepository,
    SqlAlchemyImpactClaimRepository,
    SqlAlchemyInfrastructureAssetRepository,
)
from yakhnama.modules.impacts.infrastructure.repositories import (
    SqlAlchemyImpactMetricRepository,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWork


class SqlAlchemyImpactClaimsUnitOfWork(SqlAlchemyUnitOfWork):
    """``ImpactClaimsUnitOfWork`` over one ``AsyncSession``, with the outbox on commit.

    Implements: Unit of Work (port ``ImpactClaimsUnitOfWork``).
    """

    def _open_repositories(self, session: AsyncSession) -> None:
        """Build the four repositories on the session this unit of work opened.

        Args:
            session: The session of this unit of work.
        """
        self._impact_metrics = SqlAlchemyImpactMetricRepository(session)
        self._impact_claims = SqlAlchemyImpactClaimRepository(session)
        self._infrastructure_assets = SqlAlchemyInfrastructureAssetRepository(session)
        self._damage_records = SqlAlchemyDamageRecordRepository(session)

    @property
    def impact_metrics(self) -> ImpactMetricRepository:
        """Return the metric repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        # Reading the session first raises the unit of work's own error when it is
        # not active, instead of an AttributeError on a missing repository.
        _ = self.session
        return self._impact_metrics

    @property
    def impact_claims(self) -> ImpactClaimRepository:
        """Return the claim repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        _ = self.session
        return self._impact_claims

    @property
    def infrastructure_assets(self) -> InfrastructureAssetRepository:
        """Return the asset repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        _ = self.session
        return self._infrastructure_assets

    @property
    def damage_records(self) -> DamageRecordRepository:
        """Return the damage record repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        _ = self.session
        return self._damage_records
