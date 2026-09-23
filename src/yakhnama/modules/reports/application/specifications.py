"""Search specifications over report records.

Evaluated in memory by fakes and compiled to SQL by the infrastructure query
service, which must return exactly what ``is_satisfied_by`` accepts.

**Bounding boxes test the rounded position.** ``ReportInBoundingBoxSpecification``
rounds each report's position with the same ``PublicCoordinatePolicy`` the summaries
use before testing it, so the SQL form must round the stored point first (for
example ``ST_MakePoint(round(x, d), round(y, d))``). Testing the exact point would
let a caller narrow ever smaller boxes around one report and recover the reporter's
private position from which boxes still return it.

Patterns: Specification.
"""

from datetime import datetime

from yakhnama.modules.reports.application.dto import ReportRecord
from yakhnama.modules.reports.domain.value_objects import ReportStatus
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.privacy import PublicCoordinatePolicy
from yakhnama.shared_kernel.specification import Specification
from yakhnama.shared_kernel.value_objects import BoundingBox


class ReportStatusSpecification(Specification[ReportRecord]):
    """Matches reports in one lifecycle status.

    Implements: Specification.

    Attributes:
        status: The status to match.
    """

    def __init__(self, status: ReportStatus) -> None:
        """Create the specification.

        Args:
            status: The status to match.
        """
        self._status = status

    @property
    def status(self) -> ReportStatus:
        """Return the status to match."""
        return self._status

    def is_satisfied_by(self, candidate: ReportRecord) -> bool:
        """Tell whether ``candidate`` has the status.

        Args:
            candidate: The report to test.

        Returns:
            ``True`` if the statuses are equal.
        """
        return candidate.status is self._status


class ReportHazardCodeSpecification(Specification[ReportRecord]):
    """Matches reports whose reporter guessed one hazard type.

    Implements: Specification.

    Attributes:
        hazard_code: The guessed code to match.
    """

    def __init__(self, hazard_code: str) -> None:
        """Create the specification.

        Args:
            hazard_code: The guessed code to match, compared exactly.
        """
        self._hazard_code = hazard_code

    @property
    def hazard_code(self) -> str:
        """Return the guessed code to match."""
        return self._hazard_code

    def is_satisfied_by(self, candidate: ReportRecord) -> bool:
        """Tell whether ``candidate``'s guess is the code.

        Args:
            candidate: The report to test.

        Returns:
            ``True`` if it has a guess with that code; never without a guess.
        """
        guess = candidate.hazard_guess
        return guess is not None and guess.hazard_code == self._hazard_code


class ReportInBoundingBoxSpecification(Specification[ReportRecord]):
    """Matches reports whose **rounded** position lies in a box (see module docs).

    Implements: Specification.

    Attributes:
        bbox: The box, edges included.
        public_coordinates: The rounding applied before the test.
    """

    def __init__(
        self, bbox: BoundingBox, public_coordinates: PublicCoordinatePolicy
    ) -> None:
        """Create the specification.

        Args:
            bbox: The box, edges included.
            public_coordinates: The rounding the summaries use.
        """
        self._bbox = bbox
        self._public_coordinates = public_coordinates

    @property
    def bbox(self) -> BoundingBox:
        """Return the box."""
        return self._bbox

    @property
    def public_coordinates(self) -> PublicCoordinatePolicy:
        """Return the rounding applied before the test."""
        return self._public_coordinates

    def is_satisfied_by(self, candidate: ReportRecord) -> bool:
        """Tell whether ``candidate``'s rounded position lies in the box.

        Args:
            candidate: The report to test.

        Returns:
            ``True`` if the rounded point is inside or on an edge.
        """
        rounded = self._public_coordinates.apply(candidate.observation.coordinates)
        return self._bbox.contains(rounded)


class ReportObservedFromSpecification(Specification[ReportRecord]):
    """Matches reports observed at or after an instant.

    Compares the stored ``observed_at`` value (the start of its precision period)
    regardless of its precision.

    Implements: Specification.

    Attributes:
        instant: The earliest observation time, inclusive.
    """

    def __init__(self, instant: datetime) -> None:
        """Create the specification.

        Args:
            instant: The earliest observation time, inclusive; timezone-aware.
        """
        self._instant = instant

    @property
    def instant(self) -> datetime:
        """Return the earliest observation time."""
        return self._instant

    def is_satisfied_by(self, candidate: ReportRecord) -> bool:
        """Tell whether ``candidate`` was observed at or after the instant.

        Args:
            candidate: The report to test.

        Returns:
            ``True`` if ``observed_at.value >= instant``.
        """
        return candidate.observed_at.value >= self._instant


class ReportObservedToSpecification(Specification[ReportRecord]):
    """Matches reports observed at or before an instant.

    Implements: Specification.

    Attributes:
        instant: The latest observation time, inclusive.
    """

    def __init__(self, instant: datetime) -> None:
        """Create the specification.

        Args:
            instant: The latest observation time, inclusive; timezone-aware.
        """
        self._instant = instant

    @property
    def instant(self) -> datetime:
        """Return the latest observation time."""
        return self._instant

    def is_satisfied_by(self, candidate: ReportRecord) -> bool:
        """Tell whether ``candidate`` was observed at or before the instant.

        Args:
            candidate: The report to test.

        Returns:
            ``True`` if ``observed_at.value <= instant``.
        """
        return candidate.observed_at.value <= self._instant


class ReportReporterSpecification(Specification[ReportRecord]):
    """Matches reports by one reporter; scopes listings for non-moderators.

    Implements: Specification.

    Attributes:
        reporter_id: The reporter.
    """

    def __init__(self, reporter_id: EntityId) -> None:
        """Create the specification.

        Args:
            reporter_id: The reporter.
        """
        self._reporter_id = reporter_id

    @property
    def reporter_id(self) -> EntityId:
        """Return the reporter."""
        return self._reporter_id

    def is_satisfied_by(self, candidate: ReportRecord) -> bool:
        """Tell whether ``candidate`` is by the reporter.

        Args:
            candidate: The report to test.

        Returns:
            ``True`` if the reporter ids are equal.
        """
        return candidate.reporter_id == self._reporter_id
