"""Report triage: domain-pure rules that suggest, never decide.

Triage runs over a submitted report and returns ``TriageFlag`` suggestions for a
moderator. **It never blocks, edits, rejects or verifies a report**, and no flag
changes the report's status (Phase 3 plan §3). Each rule is a small, independent
handler; ``TriageChain`` passes the same ``TriageContext`` to every rule in order
(EXIF plausibility, duplicate suspicion, personal data, spam) and collects what they
raise. Unlike a classic chain of responsibility no rule stops the chain, because a
report can be a duplicate *and* contain a phone number.

Rules see only value objects: the application layer gathers nearby reports from the
read side and photo facts from the ``media`` facade and maps them into
``ReportSummaryForTriage`` and ``PhotoEvidence``, so this module needs no other module.

Every threshold below is a **proposed** default (Phase 3 plan §6), listed as an open
question in ``docs/data-dictionary/reports.md``. Flag details name what was found but
never repeat the matched text, the coordinates or a distance precise enough to locate
the reporter, because details are shown to moderators and may reach logs.

Distances use the haversine great-circle formula on a sphere of the IUGG mean Earth
radius. Its error against the WGS84 ellipsoid is below 0.5 %, far inside the
kilometre-scale thresholds here, and it needs no geometry library in the domain.

Patterns: Chain of Responsibility.
"""

import math
import re
from collections.abc import Sequence
from datetime import timedelta
from typing import Final, Protocol, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.value_objects import (
    MEDIA_PER_REPORT_MAX,
    TriageFlag,
    TriageResult,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.value_objects import (
    Confidence,
    Coordinates,
    DatePrecision,
    DateWithPrecision,
)

MEAN_EARTH_RADIUS_METRES: Final = 6_371_008.8
"""IUGG mean Earth radius R1 (Moritz, Geodetic Reference System 1980)."""

# --- Proposed thresholds (Phase 3 plan §6; open questions in the data dictionary) ---
EXIF_TIME_TOLERANCE: Final = timedelta(days=7)
EXIF_DISTANCE_TOLERANCE_METRES: Final = 5_000.0
DUPLICATE_TIME_WINDOW: Final = timedelta(hours=24)
DUPLICATE_DISTANCE_METRES: Final = 2_000.0
SPAM_MIN_DESCRIPTION_LENGTH: Final = 10
SPAM_MAX_URLS: Final = 3
SPAM_REPEATED_CHARACTER_RUN: Final = 10

_SECONDS_PER_HOUR = 3600
_METRES_PER_KILOMETRE = 1000
# Human-readable forms of the thresholds for flag details, derived so the text can
# never disagree with the rule.
_EXIF_DAYS: Final = EXIF_TIME_TOLERANCE.days
_EXIF_KM: Final = f"{EXIF_DISTANCE_TOLERANCE_METRES / _METRES_PER_KILOMETRE:g}"
_DUPLICATE_HOURS: Final = int(
    DUPLICATE_TIME_WINDOW.total_seconds() // _SECONDS_PER_HOUR
)
_DUPLICATE_KM: Final = f"{DUPLICATE_DISTANCE_METRES / _METRES_PER_KILOMETRE:g}"

COMPARABLE_PRECISIONS: Final = frozenset(
    {DatePrecision.EXACT, DatePrecision.HOUR, DatePrecision.DAY}
)
"""Precisions fine enough to compare against day-scale windows (**proposed**).

A time known only to the month, season or year cannot show that two moments are
within 24 hours or 7 days of each other, so rules skip such comparisons rather than
guess.
"""

NEARBY_REPORTS_MAX: Final = 200
"""Most candidate reports one triage run considers; the read side pre-filters."""

# Pakistani numbers in the forms people write them (**proposed**, Q-R3): mobile
# numbers 03XX-XXXXXXX with an optional +92 / 0092 prefix instead of the leading 0,
# and landlines written with the +92 / 0092 prefix. ``\d`` also matches Urdu
# (Extended Arabic-Indic) digits, which reporters may type.
_PHONE_PATTERNS: Final = (
    re.compile(r"(?<!\d)(?:\+92|0092|0)[\s-]?3\d{2}[\s-]?\d{7}(?!\d)"),
    re.compile(r"(?<!\d)(?:\+92|0092)[\s-]?\d{2,4}[\s-]?\d{5,8}(?!\d)"),
)
_EMAIL_PATTERN: Final = re.compile(r"[\w.%+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")
# The CNIC layout NNNNN-NNNNNNN-N (Phase 3 plan §6), unanchored so it is found inside
# a description, and bounded by non-digits so a longer number does not match.
_CNIC_PATTERN: Final = re.compile(r"(?<!\d)\d{5}-\d{7}-\d(?!\d)")
_URL_PATTERN: Final = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_REPEATED_CHARACTER_PATTERN: Final = re.compile(
    rf"(\S)\1{{{SPAM_REPEATED_CHARACTER_RUN - 1},}}"
)


def measure_distance_metres(origin: Coordinates, destination: Coordinates) -> float:
    """Return the great-circle distance between two points, by haversine.

    Args:
        origin: The first point.
        destination: The second point.

    Returns:
        The distance in metres, from 0 to half the Earth's circumference.
    """
    latitude_1 = math.radians(origin.latitude)
    latitude_2 = math.radians(destination.latitude)
    delta_latitude = latitude_2 - latitude_1
    delta_longitude = math.radians(destination.longitude - origin.longitude)
    haversine = (
        math.sin(delta_latitude / 2) ** 2
        + math.cos(latitude_1)
        * math.cos(latitude_2)
        * math.sin(delta_longitude / 2) ** 2
    )
    # Rounding can push the value a hair outside [0, 1] for antipodal points, where
    # asin would raise; clamping keeps the result exact at the bounds.
    central_angle = 2 * math.asin(math.sqrt(min(1.0, max(0.0, haversine))))
    return MEAN_EARTH_RADIUS_METRES * central_angle


def _time_apart(
    first: DateWithPrecision, second: DateWithPrecision
) -> timedelta | None:
    if (
        first.precision not in COMPARABLE_PRECISIONS
        or second.precision not in COMPARABLE_PRECISIONS
    ):
        return None
    return abs(first.value - second.value)


class ReportSummaryForTriage(BaseModel):
    """Another report the read side found near a report being triaged.

    Implements: Value Object.

    Attributes:
        report_id: The other report.
        observed_at: When it was observed.
        coordinates: Where its reporter was.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: EntityId
    observed_at: DateWithPrecision
    coordinates: Coordinates


class PhotoEvidence(BaseModel):
    """What a photo's EXIF metadata says about when and where it was taken.

    Implements: Value Object.

    Attributes:
        media_id: The media asset.
        taken_at: The EXIF capture time, or ``None`` if absent.
        location: The EXIF GPS position, or ``None`` if absent.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    media_id: EntityId
    taken_at: DateWithPrecision | None = None
    location: Coordinates | None = None


class TriageContext(BaseModel):
    """Everything the triage rules may look at for one report.

    Implements: Value Object.

    Attributes:
        report: The report being triaged.
        nearby_reports: Candidate reports near it in space and time.
        photos: EXIF facts of its attached photos.
        now: When the chain runs, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    report: Report
    nearby_reports: tuple[ReportSummaryForTriage, ...] = Field(
        default=(), max_length=NEARBY_REPORTS_MAX
    )
    photos: tuple[PhotoEvidence, ...] = Field(
        default=(), max_length=MEDIA_PER_REPORT_MAX
    )
    now: AwareDatetime


class TriageRule(Protocol):
    """One triage check; returns a flag, or ``None`` when it has nothing to say.

    Implements: Chain of Responsibility (handler).
    """

    def evaluate(self, context: TriageContext) -> TriageFlag | None:
        """Check the report in ``context``.

        Args:
            context: The report and what is known around it.

        Returns:
            A flag, or ``None``.
        """
        ...


class ExifPlausibilityRule:
    """Flag photos taken long before or after, or far from, the observation.

    A photo with no EXIF time or position is never flagged: missing metadata is
    common (messaging apps strip it) and proves nothing. Times are compared only
    when both are known to the day or better (``COMPARABLE_PRECISIONS``).

    Implements: Chain of Responsibility (handler).
    """

    def evaluate(self, context: TriageContext) -> TriageFlag | None:
        """Flag the report if any photo's EXIF time or place is implausible.

        Args:
            context: The report and its photos' EXIF facts.

        Returns:
            One ``exif_implausible`` flag covering every implausible photo, or
            ``None``.
        """
        report = context.report
        is_time_off = False
        is_place_off = False
        for photo in context.photos:
            if photo.taken_at is not None:
                apart = _time_apart(photo.taken_at, report.observed_at)
                is_time_off |= apart is not None and apart > EXIF_TIME_TOLERANCE
            if photo.location is not None:
                distance = measure_distance_metres(
                    photo.location, report.observation.coordinates
                )
                is_place_off |= distance > EXIF_DISTANCE_TOLERANCE_METRES
        if not (is_time_off or is_place_off):
            return None
        reasons = [
            reason
            for reason, applies in (
                (
                    f"taken more than {_EXIF_DAYS} days from the observation time",
                    is_time_off,
                ),
                (
                    f"taken more than {_EXIF_KM} km from the observation point",
                    is_place_off,
                ),
            )
            if applies
        ]
        return TriageFlag(
            kind="exif_implausible",
            detail="A photo's metadata says it was " + " and ".join(reasons) + ".",
            confidence=Confidence.MEDIUM,
        )


class DuplicateSuspicionRule:
    """Flag a report observed close to another report in space and time.

    The report it revises (``supersedes_id``) and the report itself are never
    candidates. Only the nearest candidate is named, so the moderator starts from
    the most likely duplicate.

    Implements: Chain of Responsibility (handler).
    """

    def evaluate(self, context: TriageContext) -> TriageFlag | None:
        """Flag the report if another report is within 2 km and 24 hours.

        Args:
            context: The report and candidate nearby reports.

        Returns:
            A ``duplicate_suspected`` flag naming the nearest match, or ``None``.
        """
        report = context.report
        excluded = {report.id, report.supersedes_id}
        matches: list[tuple[float, EntityId]] = []
        for candidate in context.nearby_reports:
            if candidate.report_id in excluded:
                continue
            apart = _time_apart(candidate.observed_at, report.observed_at)
            if apart is None or apart > DUPLICATE_TIME_WINDOW:
                continue
            distance = measure_distance_metres(
                candidate.coordinates, report.observation.coordinates
            )
            if distance <= DUPLICATE_DISTANCE_METRES:
                matches.append((distance, candidate.report_id))
        if not matches:
            return None
        _, nearest_id = min(matches)
        return TriageFlag(
            kind="duplicate_suspected",
            detail=(
                f"{len(matches)} other report(s) observed within {_DUPLICATE_KM} km "
                f"and {_DUPLICATE_HOURS} hours; "
                "the nearest is linked."
            ),
            confidence=Confidence.LOW,
            related_report_id=nearest_id,
        )


class PiiScrubRule:
    """Flag descriptions that seem to contain phone numbers, emails or CNIC numbers.

    The rule only flags: it never redacts, because the description is the
    reporter's own words and a report is never edited. A moderator decides whether
    the public copy needs redaction.

    Implements: Chain of Responsibility (handler).
    """

    def evaluate(self, context: TriageContext) -> TriageFlag | None:
        """Flag the report if its description matches a personal-data pattern.

        Args:
            context: The report.

        Returns:
            A ``pii_detected`` flag naming the kinds found (never the values), or
            ``None``.
        """
        description = context.report.description
        found = [
            kind
            for kind, is_present in (
                (
                    "phone number",
                    any(pattern.search(description) for pattern in _PHONE_PATTERNS),
                ),
                ("email address", _EMAIL_PATTERN.search(description) is not None),
                ("CNIC number", _CNIC_PATTERN.search(description) is not None),
            )
            if is_present
        ]
        if not found:
            return None
        return TriageFlag(
            kind="pii_detected",
            detail="The description may contain: " + ", ".join(found) + ".",
            confidence=Confidence.MEDIUM,
        )


class SpamRule:
    """Flag descriptions that look like spam rather than an observation.

    Signals: fewer than 10 characters, more than 3 links, or one character repeated
    10 or more times in a row.

    Implements: Chain of Responsibility (handler).
    """

    def evaluate(self, context: TriageContext) -> TriageFlag | None:
        """Flag the report if its description shows a spam signal.

        Args:
            context: The report.

        Returns:
            A ``spam_suspected`` flag naming the signals, or ``None``.
        """
        description = context.report.description
        signals = [
            signal
            for signal, applies in (
                (
                    "very short description",
                    len(description) < SPAM_MIN_DESCRIPTION_LENGTH,
                ),
                (
                    f"more than {SPAM_MAX_URLS} links",
                    len(_URL_PATTERN.findall(description)) > SPAM_MAX_URLS,
                ),
                (
                    "long run of one repeated character",
                    _REPEATED_CHARACTER_PATTERN.search(description) is not None,
                ),
            )
            if applies
        ]
        if not signals:
            return None
        return TriageFlag(
            kind="spam_suspected",
            detail="Spam signals: " + ", ".join(signals) + ".",
            confidence=Confidence.LOW,
        )


class TriageChain:
    """Run triage rules in order and collect their flags.

    Implements: Chain of Responsibility.
    """

    def __init__(self, rules: Sequence[TriageRule]) -> None:
        """Create the chain.

        Args:
            rules: The rules, in the order their flags should be listed.
        """
        self._rules = tuple(rules)

    @classmethod
    def default(cls) -> Self:
        """Return the chain in the order of the Phase 3 plan.

        Returns:
            EXIF plausibility, duplicate suspicion, personal data, spam.
        """
        return cls(
            (
                ExifPlausibilityRule(),
                DuplicateSuspicionRule(),
                PiiScrubRule(),
                SpamRule(),
            )
        )

    @property
    def rules(self) -> tuple[TriageRule, ...]:
        """Return the rules in running order.

        Returns:
            The rules.
        """
        return self._rules

    def run(self, context: TriageContext) -> TriageResult:
        """Run every rule over ``context``; no rule can stop the others.

        Args:
            context: The report and what is known around it.

        Returns:
            The flags in rule order, stamped with ``context.now``.
        """
        flags = tuple(
            flag for rule in self._rules if (flag := rule.evaluate(context)) is not None
        )
        return TriageResult(flags=flags, evaluated_at=context.now)
