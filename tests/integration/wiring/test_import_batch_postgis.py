"""``SessionBatchEventWriter`` against PostGIS: one import batch, one transaction.

A committed batch stores the row's source, the event, its verification case, its
claim and their outbox rows; a batch left without ``commit`` after a refused row
stores none of them, although every module handler "committed" its own unit of
work inside the batch.
"""

from datetime import UTC, datetime
from typing import Final

import pytest
from sqlalchemy import func, select

from tests.fakes.identity import actor_with
from yakhnama.modules.events.public import EventPeriod
from yakhnama.modules.exchange.domain.backfill import (
    ImportedClaimDraft,
    ImportedSourceDraft,
)
from yakhnama.modules.exchange.public import ImportedEventDraft
from yakhnama.modules.identity.public import Role
from yakhnama.modules.impacts.public import CountValue
from yakhnama.modules.provenance.public import RegisterSource, SourceDetails, SourceType
from yakhnama.modules.verification.public import TargetKind, VerificationTarget
from yakhnama.platform.container import Container, build_seed_handler
from yakhnama.platform.outbox.models import OutboxMessage
from yakhnama.platform.wiring.events import ReportFactsAdapter
from yakhnama.platform.wiring.exchange import (
    BatchReferencePorts,
    SessionBatchEventWriter,
)
from yakhnama.platform.wiring.verification import ReportOwnerAdapter
from yakhnama.seed.application import SeedReferenceData
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.value_objects import (
    Confidence,
    DatePrecision,
    DateWithPrecision,
)

pytestmark = pytest.mark.integration

MODERATOR: Final = actor_with({Role.MODERATOR})
KNOWN_PLACE: Final = "pk.gb"
UNKNOWN_PLACE: Final = "pk.gb.nowhere"


def _day(year: int, month: int, day: int) -> DateWithPrecision:
    return DateWithPrecision(
        value=datetime(year, month, day, tzinfo=UTC), precision=DatePrecision.DAY
    )


def _draft(place_code: str) -> ImportedEventDraft:
    return ImportedEventDraft(
        title="Outburst flood of 1974",
        hazard_type="glof",
        period=EventPeriod(started_at=_day(1974, 7, 12)),
        place_codes=(place_code,),
        source=ImportedSourceDraft(citation="District archive, flood register 1974"),
        claims=(
            ImportedClaimDraft(
                metric_code="deaths",
                value=CountValue(count=3),
                confidence=Confidence.MEDIUM,
                claimed_at=_day(1974, 7, 20),
            ),
        ),
    )


def _writer(container: Container) -> SessionBatchEventWriter:
    return SessionBatchEventWriter(
        container.engine,
        BatchReferencePorts(
            outbox_writer=container.outbox_writer,
            clock=container.clock,
            ids=container.id_generator,
            coordinates=container.public_coordinates,
            hazard_types=container.hazard_type_query_service,
            geography=container.geography_uow_factory,
            identity=container.identity_uow_factory,
            report_facts=ReportFactsAdapter(
                container.report_query_service, container.public_coordinates
            ),
            report_owners=ReportOwnerAdapter(container.report_query_service),
        ),
    )


async def _lineage(container: Container) -> EntityId:
    detail = await container.source_registrar(
        RegisterSource(
            actor=MODERATOR,
            source_type=SourceType.DATASET,
            details=SourceDetails(title="Lineage", citation="Import job, test."),
        )
    )
    return detail.id


async def _outbox_count(container: Container) -> int:
    async with container.session_factory() as session:
        count = await session.scalar(select(func.count(OutboxMessage.id)))
    return int(count or 0)


@pytest.fixture
async def seeded(container: Container) -> Container:
    await build_seed_handler(container)(
        SeedReferenceData(actor=actor_with({Role.ADMIN}))
    )
    return container


async def test_batch_committed_stores_event_case_claim_and_outbox_rows(
    seeded: Container,
) -> None:
    lineage_id = await _lineage(seeded)
    outbox_before = await _outbox_count(seeded)

    async with _writer(seeded).batch() as batch:
        event_id = await batch.create(
            _draft(KNOWN_PLACE), lineage_source_id=lineage_id, actor=MODERATOR
        )
        await batch.commit()

    event = await seeded.event_query_service.get(event_id)
    case = await seeded.verification_query_service.get_for_target(
        VerificationTarget(kind=TargetKind.EVENT, target_id=event_id)
    )
    assert event is not None
    assert lineage_id in event.source_ids
    assert case is not None
    assert await _outbox_count(seeded) > outbox_before


async def _write_then_refuse(
    writer: SessionBatchEventWriter, lineage_id: EntityId, created: list[EntityId]
) -> None:
    # The second row names a place that does not exist, so its event is refused
    # after the first row's source, event, case and claim were written.
    async with writer.batch() as batch:
        created.append(
            await batch.create(
                _draft(KNOWN_PLACE), lineage_source_id=lineage_id, actor=MODERATOR
            )
        )
        await batch.create(
            _draft(UNKNOWN_PLACE), lineage_source_id=lineage_id, actor=MODERATOR
        )


async def test_batch_left_without_commit_stores_nothing(seeded: Container) -> None:
    lineage_id = await _lineage(seeded)
    outbox_before = await _outbox_count(seeded)
    created: list[EntityId] = []

    with pytest.raises(ValidationError):
        await _write_then_refuse(_writer(seeded), lineage_id, created)

    (first_id,) = created
    assert await seeded.event_query_service.get(first_id) is None
    assert (
        await seeded.verification_query_service.get_for_target(
            VerificationTarget(kind=TargetKind.EVENT, target_id=first_id)
        )
        is None
    )
    assert await _outbox_count(seeded) == outbox_before
