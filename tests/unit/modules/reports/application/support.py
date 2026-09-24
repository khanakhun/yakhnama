"""Shared arrangements for the reports application tests.

Coordinates are synthetic test points near Gilgit, not places or boundaries.
"""

from datetime import UTC, datetime, timedelta

from tests.factories.reports import ReportTestFactory
from tests.fakes.clock import FrozenClock
from tests.fakes.identity import actor_with
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.provenance import FakeSourceReferenceMarker, FakeSourceRegistrar
from tests.fakes.reports import (
    FakeMediaOwnershipChecker,
    FakeNearbyReportsFinder,
    FakePhotoEvidenceProvider,
    InMemoryReportsUnitOfWork,
)
from tests.fakes.tasks import RecordingTaskQueue
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.modules.identity.public import OrganizationRole, Role
from yakhnama.modules.reports.application.handlers import (
    ReviseReportHandler,
    RunTriageHandler,
    SubmitReportHandler,
    WithdrawReportHandler,
)
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.value_objects import (
    GpsAccuracy,
    ObservationPoint,
    ReportContent,
)
from yakhnama.shared_kernel.privacy import PublicCoordinatePolicy
from yakhnama.shared_kernel.value_objects import (
    Coordinates,
    DatePrecision,
    DateWithPrecision,
)

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
OBSERVED = DateWithPrecision(
    value=NOW - timedelta(hours=2), precision=DatePrecision.EXACT
)
POINT = Coordinates(longitude=74.30871, latitude=35.92062)
ROUNDED_POINT = Coordinates(longitude=74.31, latitude=35.92)
ACCURACY = GpsAccuracy.metres(12.5)
PUBLIC_COORDINATES = PublicCoordinatePolicy(decimals=2)

IDS = SequentialIdGenerator(seed=501)
REPORTER_ID = IDS.new_id()
OTHER_ID = IDS.new_id()
ORGANIZATION_ID = IDS.new_id()
MEDIA_ID = IDS.new_id()
MISSING_ID = IDS.new_id()

REPORTER = actor_with(user_id=REPORTER_ID)
OTHER_CITIZEN = actor_with(user_id=OTHER_ID)
MODERATOR = actor_with({Role.MODERATOR}, user_id=OTHER_ID)
MEMBER = actor_with(
    user_id=REPORTER_ID, memberships={(ORGANIZATION_ID, OrganizationRole.MEMBER)}
)
COLLEAGUE = actor_with(
    user_id=OTHER_ID, memberships={(ORGANIZATION_ID, OrganizationRole.MEMBER)}
)


def content(**overrides: object) -> ReportContent:
    """Return report content at ``POINT`` with ``ACCURACY``, observed two hours ago."""
    return ReportContent.model_validate(
        {
            "observed_at": OBSERVED,
            "observation": ObservationPoint(coordinates=POINT, accuracy=ACCURACY),
            "description": "Muddy water rising fast in the nullah below the village.",
            "original_language": "en",
            **overrides,
        }
    )


def stored_report(**overrides: object) -> Report:
    """Return a submitted first revision by ``REPORTER_ID`` with ``content()``.

    ``overrides`` replace fields and the whole report is validated again, so an
    inconsistent override fails as it would in the domain.
    """
    base = content()
    report = ReportTestFactory.build(
        reporter_id=REPORTER_ID,
        created_at=NOW - timedelta(hours=1),
        observed_at=base.observed_at,
        observation=base.observation,
        description=base.description,
    )
    fields = {name: getattr(report, name) for name in Report.model_fields}
    return Report.model_validate({**fields, **overrides})


class Harness:
    """Fakes and handlers of one reports test, sharing one unit of work."""

    def __init__(self, *reports: Report) -> None:
        """Arrange the fakes around ``reports``."""
        self.uow = InMemoryReportsUnitOfWork(reports=reports)
        self.factory = InMemoryUnitOfWorkFactory(self.uow)
        self.registrar = FakeSourceRegistrar()
        self.marker = FakeSourceReferenceMarker(self.registrar)
        self.media = FakeMediaOwnershipChecker({MEDIA_ID: REPORTER_ID})
        self.tasks = RecordingTaskQueue()
        self.nearby = FakeNearbyReportsFinder()
        self.photos = FakePhotoEvidenceProvider()
        self.clock = FrozenClock(NOW)
        self.ids = SequentialIdGenerator(seed=502)

    def submit(self) -> SubmitReportHandler:
        """Return the submit handler."""
        return SubmitReportHandler(
            uow_factory=self.factory,
            source_registrar=self.registrar,
            source_marker=self.marker,
            media_checker=self.media,
            task_queue=self.tasks,
            clock=self.clock,
            ids=self.ids,
        )

    def revise(self) -> ReviseReportHandler:
        """Return the revise handler."""
        return ReviseReportHandler(
            uow_factory=self.factory,
            media_checker=self.media,
            task_queue=self.tasks,
            clock=self.clock,
            ids=self.ids,
        )

    def withdraw(self) -> WithdrawReportHandler:
        """Return the withdraw handler."""
        return WithdrawReportHandler(self.factory, self.clock, self.ids)

    def triage(self) -> RunTriageHandler:
        """Return the triage handler with the default chain."""
        return RunTriageHandler(
            uow_factory=self.factory,
            nearby_reports=self.nearby,
            photos=self.photos,
            clock=self.clock,
            ids=self.ids,
        )
