"""Factories for the ``reports`` domain: report content and reports.

``ReportTestFactory`` is suffixed ``TestFactory`` because the domain already has a
``ReportFactory`` (``yakhnama.modules.reports.domain.factories``); this one builds a
``Report`` directly for arranging state. By default it builds the first revision of a
submitted report, observed one hour before it was submitted, at a random point in the
test region. Descriptions are placeholders (``"Test report <n>: ..."``), never real
observations, and the region is test data, not a boundary fact.

Patterns: Factory.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta

from polyfactory import PostGenerated, Use

from tests.factories.base import (
    FACTORY_IDS,
    YakhnamaModelFactory,
    random_instant,
    sequence,
)
from tests.factories.shared_kernel import CoordinatesFactory
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.value_objects import (
    ObservationPoint,
    ReportContent,
    ReportStatus,
)
from yakhnama.shared_kernel.value_objects import DatePrecision, DateWithPrecision

OBSERVATION_LEAD = timedelta(hours=1)
"""How long before ``created_at`` factory reports were observed."""


def _observation_point() -> ObservationPoint:
    return ObservationPoint(coordinates=CoordinatesFactory.build())


def _observed_before_creation(_name: str, values: Mapping[str, object]) -> object:
    created_at = values["created_at"]
    assert isinstance(created_at, datetime)
    return DateWithPrecision(
        value=created_at - OBSERVATION_LEAD, precision=DatePrecision.EXACT
    )


def _same_as_created_at(_name: str, values: Mapping[str, object]) -> object:
    # A record at version 1 has not changed since it was created.
    return values["created_at"]


class ReportContentFactory(YakhnamaModelFactory[ReportContent]):
    """Builds English content with no guess, hint or media, observed in 2026.

    Implements: Factory.
    """

    __model__ = ReportContent

    observed_at = Use(
        lambda: DateWithPrecision(value=random_instant(), precision=DatePrecision.EXACT)
    )
    observation = Use(_observation_point)
    description = sequence("Test report {}: a placeholder description.")
    original_language = "en"
    hazard_guess = None
    place_hint = None
    media_ids = ()


class ReportTestFactory(YakhnamaModelFactory[Report]):
    """Builds submitted first revisions at version 1.

    Pass ``status=ReportStatus.DRAFT, submitted_at=None`` for a draft.

    Implements: Factory.
    """

    __model__ = Report

    id = Use(FACTORY_IDS.new_id)
    reporter_id = Use(FACTORY_IDS.new_id)
    organization_id = None
    source_id = Use(FACTORY_IDS.new_id)
    created_at = Use(random_instant)
    observed_at = PostGenerated(_observed_before_creation)
    observation = Use(_observation_point)
    description = sequence("Test report {}: a placeholder description.")
    original_language = "en"
    hazard_guess = None
    place_hint = None
    media_ids = ()
    status = ReportStatus.SUBMITTED
    revision = 1
    supersedes_id = None
    superseded_by_id = None
    withdrawal_reason = None
    triage = None
    submitted_at = PostGenerated(_same_as_created_at)
    version = 1
    updated_at = PostGenerated(_same_as_created_at)
