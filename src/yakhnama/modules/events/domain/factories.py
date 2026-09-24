"""Creating events from reports (``AGENTS.md`` §3: Factory).

A moderator selects one or more reports that describe the same occurrence; the factory
derives what the reports already say and leaves the rest to the moderator:

- **period**: from the earliest to the latest ``observed_at``, both at the *coarsest*
  precision among the reports (a **proposed** rule: the combined record is no more
  precise than its least precise report). A single observed instant gives no end.
- **centroid**: the mean of the reports' public points (see ``ReportForEvent``); no
  geometry is set, since a mean of points is not a mapped extent.
- **report links**: every report linked as ``primary`` by the creating moderator.
- **sources**: every report's source, once each, in report order.
- **attributes**: optional; the moderator's, checked against the hazard type.

Patterns: Factory.
"""

from collections.abc import Sequence
from typing import Final
from uuid import UUID

from yakhnama.modules.events.domain.entities import (
    Event,
    require_matching_attributes,
)
from yakhnama.modules.events.domain.events import EventCreated
from yakhnama.modules.events.domain.value_objects import (
    EventPeriod,
    ReportForEvent,
    ReportLink,
    mean_coordinates,
)
from yakhnama.modules.hazards.public import HazardAttributesUnion, HazardTypeRef
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import IdGenerator
from yakhnama.shared_kernel.value_objects import DatePrecision, DateWithPrecision

__all__ = ["MAX_REPORTS_ON_CREATION", "EventFactory", "ReportForEvent", "coarsest"]

MAX_REPORTS_ON_CREATION: Final = 100
"""Technical cap, not a domain fact: keeps the ``EventCreated`` payload bounded.

More reports are linked afterwards with ``Event.link_report``.
"""

# DatePrecision declares its members from the finest to the coarsest.
_PRECISION_ORDER: Final = tuple(DatePrecision)


def coarsest(precisions: Sequence[DatePrecision]) -> DatePrecision:
    """Return the least precise of ``precisions``.

    Args:
        precisions: At least one precision.

    Returns:
        The coarsest one, for example ``month`` among ``day`` and ``month``.

    Raises:
        ValidationError: If ``precisions`` is empty.
    """
    if not precisions:
        message = "coarsest needs at least one precision"
        raise ValidationError(message)
    return max(precisions, key=_PRECISION_ORDER.index)


class EventFactory:
    """Builds a new draft ``Event`` from the reports it rests on.

    Implements: Factory.
    """

    @staticmethod
    def from_reports(  # noqa: PLR0913  # reason: keyword-only inputs named by the brief
        reports: Sequence[ReportForEvent],
        *,
        hazard_type: HazardTypeRef,
        title: str,
        created_by: UUID,
        clock: Clock,
        ids: IdGenerator,
        attributes: HazardAttributesUnion | None = None,
    ) -> AggregateChange[Event]:
        """Create a draft event from ``reports``.

        Args:
            reports: 1 to ``MAX_REPORTS_ON_CREATION`` distinct reports.
            hazard_type: The hazard type the moderator classifies the event as.
            title: Short name, 3 to 200 characters of safe text.
            created_by: The moderator.
            clock: Source of the timestamps.
            ids: Source of the event id and the domain event id.
            attributes: Optional hazard-specific attributes; their
                ``hazard_type`` must be ``hazard_type.code``.

        Returns:
            The new event at version 1 and one ``EventCreated`` event.

        Raises:
            ValidationError: If ``reports`` is empty, too long or repeats a report.
            AttributesMismatchError: If ``attributes`` belong to another hazard type.
            pydantic.ValidationError: If ``title`` is out of bounds or unsafe.
        """
        _check_reports(reports)
        require_matching_attributes(hazard_type, attributes)
        now = clock.now()
        instants = [report.observed_at.value for report in reports]
        precision = coarsest([report.observed_at.precision for report in reports])
        started, ended = min(instants), max(instants)
        period = EventPeriod(
            started_at=DateWithPrecision(value=started, precision=precision),
            ended_at=(
                None
                if started == ended
                else DateWithPrecision(value=ended, precision=precision)
            ),
        )
        source_ids = tuple(dict.fromkeys(report.source_id for report in reports))
        event = Event.model_validate(
            {
                "id": ids.new_id(),
                "hazard_type": hazard_type,
                "title": title,
                "period": period,
                "attributes": attributes,
                "centroid": mean_coordinates(report.coordinates for report in reports),
                "source_ids": source_ids,
                "report_links": tuple(
                    ReportLink(
                        report_id=report.id,
                        linked_by=created_by,
                        linked_at=now,
                        role="primary",
                    )
                    for report in reports
                ),
                "created_by": created_by,
                "created_at": now,
                "updated_at": now,
            }
        )
        created = EventCreated(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=event.id,
            actor_id=created_by,
            hazard_code=hazard_type.code,
            report_ids=event.report_ids,
            source_ids=source_ids,
        )
        return AggregateChange[Event](state=event, events=(created,))


def _check_reports(reports: Sequence[ReportForEvent]) -> None:
    if not reports:
        message = "an event needs at least one report"
        raise ValidationError(message)
    if len(reports) > MAX_REPORTS_ON_CREATION:
        message = (
            f"at most {MAX_REPORTS_ON_CREATION} reports can create an event; "
            "link the rest afterwards"
        )
        raise ValidationError(message, details={"count": len(reports)})
    if len({report.id for report in reports}) != len(reports):
        message = "each report can be used only once"
        raise ValidationError(message)
