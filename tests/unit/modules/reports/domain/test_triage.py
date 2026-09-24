"""Unit tests for ``yakhnama.modules.reports.domain.triage``."""

import math
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.factories.base import FACTORY_IDS
from tests.factories.reports import ReportTestFactory
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.triage import (
    DUPLICATE_DISTANCE_METRES,
    DUPLICATE_TIME_WINDOW,
    EXIF_DISTANCE_TOLERANCE_METRES,
    EXIF_TIME_TOLERANCE,
    MEAN_EARTH_RADIUS_METRES,
    DuplicateSuspicionRule,
    ExifPlausibilityRule,
    PhotoEvidence,
    PiiScrubRule,
    ReportSummaryForTriage,
    SpamRule,
    TriageChain,
    TriageContext,
    measure_distance_metres,
)
from yakhnama.modules.reports.domain.value_objects import (
    ObservationPoint,
    TriageFlag,
)
from yakhnama.shared_kernel.value_objects import (
    Confidence,
    Coordinates,
    DatePrecision,
    DateWithPrecision,
)

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
OBSERVED = datetime(2026, 9, 22, 8, 0, tzinfo=UTC)
# A synthetic test point, not a real place.
ORIGIN = Coordinates(longitude=74.5, latitude=36.3)
# One degree of latitude on the mean sphere, in metres.
METRES_PER_DEGREE = MEAN_EARTH_RADIUS_METRES * math.pi / 180

coordinates = st.builds(
    Coordinates,
    longitude=st.floats(min_value=-180.0, max_value=180.0),
    latitude=st.floats(min_value=-90.0, max_value=90.0),
)


def _north_of(origin: Coordinates, metres: float) -> Coordinates:
    return Coordinates(
        longitude=origin.longitude,
        latitude=origin.latitude + metres / METRES_PER_DEGREE,
    )


def _exact(value: datetime) -> DateWithPrecision:
    return DateWithPrecision(value=value, precision=DatePrecision.EXACT)


def _report(description: str = "Water is rising in the channel.") -> Report:
    return ReportTestFactory.build(
        factory_use_construct=False,
        created_at=NOW - timedelta(hours=1),
        observed_at=_exact(OBSERVED),
        observation=ObservationPoint(coordinates=ORIGIN),
        description=description,
    )


def _context(
    report: Report | None = None,
    *,
    nearby: tuple[ReportSummaryForTriage, ...] = (),
    photos: tuple[PhotoEvidence, ...] = (),
) -> TriageContext:
    return TriageContext(
        report=_report() if report is None else report,
        nearby_reports=nearby,
        photos=photos,
        now=NOW,
    )


def _nearby(
    metres: float, hours: float, precision: DatePrecision = DatePrecision.EXACT
) -> ReportSummaryForTriage:
    return ReportSummaryForTriage(
        report_id=FACTORY_IDS.new_id(),
        observed_at=DateWithPrecision(
            value=OBSERVED + timedelta(hours=hours), precision=precision
        ),
        coordinates=_north_of(ORIGIN, metres),
    )


# --------------------------------------------------------------------------- #
# measure_distance_metres                                                     #
# --------------------------------------------------------------------------- #


@given(origin=coordinates, destination=coordinates)
def test_measure_distance_metres_is_symmetric(
    origin: Coordinates, destination: Coordinates
) -> None:
    there = measure_distance_metres(origin, destination)

    back = measure_distance_metres(destination, origin)

    assert there == pytest.approx(back, abs=1e-6)


@given(point=coordinates)
def test_measure_distance_metres_to_itself_is_zero(point: Coordinates) -> None:
    distance = measure_distance_metres(point, point)

    assert distance == pytest.approx(0.0, abs=1e-6)


@given(origin=coordinates, destination=coordinates)
def test_measure_distance_metres_lies_within_half_circumference(
    origin: Coordinates, destination: Coordinates
) -> None:
    distance = measure_distance_metres(origin, destination)

    assert 0.0 <= distance <= math.pi * MEAN_EARTH_RADIUS_METRES + 1e-6


def test_measure_distance_metres_between_antipodes_is_half_circumference() -> None:
    north = Coordinates(longitude=0.0, latitude=90.0)
    south = Coordinates(longitude=0.0, latitude=-90.0)

    distance = measure_distance_metres(north, south)

    assert distance == pytest.approx(math.pi * MEAN_EARTH_RADIUS_METRES)


def test_measure_distance_metres_one_degree_of_latitude_matches_arc_length() -> None:
    destination = Coordinates(longitude=ORIGIN.longitude, latitude=ORIGIN.latitude + 1)

    distance = measure_distance_metres(ORIGIN, destination)

    assert distance == pytest.approx(METRES_PER_DEGREE)


# --------------------------------------------------------------------------- #
# ExifPlausibilityRule                                                        #
# --------------------------------------------------------------------------- #


def test_exif_rule_with_plausible_photo_returns_none() -> None:
    photo = PhotoEvidence(
        media_id=FACTORY_IDS.new_id(),
        taken_at=_exact(OBSERVED + timedelta(days=1)),
        location=_north_of(ORIGIN, 1_000),
    )

    flag = ExifPlausibilityRule().evaluate(_context(photos=(photo,)))

    assert flag is None


def test_exif_rule_with_photo_without_metadata_returns_none() -> None:
    photo = PhotoEvidence(media_id=FACTORY_IDS.new_id())

    flag = ExifPlausibilityRule().evaluate(_context(photos=(photo,)))

    assert flag is None


def test_exif_rule_with_photo_taken_too_early_flags_time() -> None:
    taken = OBSERVED - EXIF_TIME_TOLERANCE - timedelta(minutes=1)
    photo = PhotoEvidence(media_id=FACTORY_IDS.new_id(), taken_at=_exact(taken))

    flag = ExifPlausibilityRule().evaluate(_context(photos=(photo,)))

    assert flag is not None
    assert flag.kind == "exif_implausible"
    assert "7 days" in flag.detail
    assert "5 km" not in flag.detail
    assert flag.confidence is Confidence.MEDIUM


def test_exif_rule_with_photo_taken_far_away_flags_place() -> None:
    far = _north_of(ORIGIN, EXIF_DISTANCE_TOLERANCE_METRES + 100)
    photo = PhotoEvidence(media_id=FACTORY_IDS.new_id(), location=far)

    flag = ExifPlausibilityRule().evaluate(_context(photos=(photo,)))

    assert flag is not None
    assert "5 km" in flag.detail
    assert "7 days" not in flag.detail


def test_exif_rule_with_late_and_far_photos_flags_both_once() -> None:
    late = PhotoEvidence(
        media_id=FACTORY_IDS.new_id(), taken_at=_exact(OBSERVED + timedelta(days=30))
    )
    far = PhotoEvidence(media_id=FACTORY_IDS.new_id(), location=_north_of(ORIGIN, 9e3))

    flag = ExifPlausibilityRule().evaluate(_context(photos=(late, far)))

    assert flag is not None
    assert "7 days" in flag.detail
    assert "5 km" in flag.detail


def test_exif_rule_with_month_precision_photo_time_is_not_compared() -> None:
    taken = DateWithPrecision(
        value=OBSERVED - timedelta(days=20), precision=DatePrecision.MONTH
    )
    photo = PhotoEvidence(media_id=FACTORY_IDS.new_id(), taken_at=taken)

    flag = ExifPlausibilityRule().evaluate(_context(photos=(photo,)))

    assert flag is None


# --------------------------------------------------------------------------- #
# DuplicateSuspicionRule                                                      #
# --------------------------------------------------------------------------- #


def test_duplicate_rule_without_candidates_returns_none() -> None:
    flag = DuplicateSuspicionRule().evaluate(_context())

    assert flag is None


def test_duplicate_rule_with_close_candidate_links_the_nearest() -> None:
    near = _nearby(metres=500, hours=2)
    nearer = _nearby(metres=100, hours=-3)

    flag = DuplicateSuspicionRule().evaluate(_context(nearby=(near, nearer)))

    assert flag is not None
    assert flag.kind == "duplicate_suspected"
    assert flag.related_report_id == nearer.report_id
    assert flag.detail.startswith("2 other report(s)")
    assert flag.confidence is Confidence.LOW


@pytest.mark.parametrize(
    ("metres", "hours"),
    [
        (DUPLICATE_DISTANCE_METRES + 50, 1),
        (100, DUPLICATE_TIME_WINDOW.total_seconds() / 3600 + 1),
    ],
    ids=["too-far", "too-late"],
)
def test_duplicate_rule_with_distant_candidate_returns_none(
    metres: float, hours: float
) -> None:
    flag = DuplicateSuspicionRule().evaluate(_context(nearby=(_nearby(metres, hours),)))

    assert flag is None


def test_duplicate_rule_with_coarse_precision_candidate_returns_none() -> None:
    candidate = _nearby(metres=10, hours=0, precision=DatePrecision.SEASON)

    flag = DuplicateSuspicionRule().evaluate(_context(nearby=(candidate,)))

    assert flag is None


def test_duplicate_rule_ignores_the_report_itself_and_its_predecessor() -> None:
    predecessor_id = FACTORY_IDS.new_id()
    report = ReportTestFactory.build(
        factory_use_construct=False,
        created_at=NOW - timedelta(hours=1),
        observed_at=_exact(OBSERVED),
        observation=ObservationPoint(coordinates=ORIGIN),
        revision=2,
        supersedes_id=predecessor_id,
    )
    same = _nearby(0, 0).model_copy(update={"report_id": report.id})
    previous = _nearby(0, 0).model_copy(update={"report_id": predecessor_id})

    flag = DuplicateSuspicionRule().evaluate(_context(report, nearby=(same, previous)))

    assert flag is None


# --------------------------------------------------------------------------- #
# PiiScrubRule                                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("description", "kind"),
    [
        ("Call me on 0312-3456789 for details.", "phone number"),
        ("Call me on +92 312 3456789 for details.", "phone number"),
        ("Call me on 00923123456789 for details.", "phone number"),
        ("Office line +92-5811-920123 is working.", "phone number"),
        ("Write to someone@example.org for photos.", "email address"),
        ("My CNIC is 71501-1234567-1, please check.", "CNIC number"),
    ],
)
def test_pii_rule_with_personal_data_flags_kind_without_value(
    description: str, kind: str
) -> None:
    flag = PiiScrubRule().evaluate(_context(_report(description)))

    assert flag is not None
    assert flag.kind == "pii_detected"
    assert kind in flag.detail
    assert not any(character.isdigit() for character in flag.detail)
    assert "@" not in flag.detail


@pytest.mark.parametrize(
    "description",
    [
        "The bridge at kilometre 12 was washed away around 3 pm.",
        "Water level rose 2 metres; 150 houses affected.",
        "Reference 123456-1234567-12 is not a CNIC layout.",
    ],
)
def test_pii_rule_without_personal_data_returns_none(description: str) -> None:
    flag = PiiScrubRule().evaluate(_context(_report(description)))

    assert flag is None


def test_pii_rule_with_several_kinds_lists_each_once() -> None:
    description = "Call 03123456789 or mail a.b@example.org about it."

    flag = PiiScrubRule().evaluate(_context(_report(description)))

    assert flag is not None
    assert flag.detail == "The description may contain: phone number, email address."


# --------------------------------------------------------------------------- #
# SpamRule                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("description", "signal"),
    [
        ("help", "very short description"),
        (
            "see https://a.example www.b.example http://c.example https://d.example",
            "more than 3 links",
        ),
        ("Flood flood!!!!!!!!!! here", "long run of one repeated character"),
    ],
)
def test_spam_rule_with_signal_flags_it(description: str, signal: str) -> None:
    flag = SpamRule().evaluate(_context(_report(description)))

    assert flag is not None
    assert flag.kind == "spam_suspected"
    assert signal in flag.detail


@pytest.mark.parametrize(
    "description",
    [
        "The stream turned muddy and loud after the rain.",
        "Photos: https://a.example https://b.example https://c.example",
        "Water rose fast......... then stopped",
    ],
)
def test_spam_rule_without_signal_returns_none(description: str) -> None:
    flag = SpamRule().evaluate(_context(_report(description)))

    assert flag is None


# --------------------------------------------------------------------------- #
# TriageChain                                                                 #
# --------------------------------------------------------------------------- #


class _FixedRule:
    """A rule that always returns the same flag or nothing.

    Implements: Fake (of Chain of Responsibility handler).
    """

    def __init__(self, flag: TriageFlag | None) -> None:
        self.flag = flag
        self.calls = 0

    def evaluate(self, context: TriageContext) -> TriageFlag | None:
        del context
        self.calls += 1
        return self.flag


def test_triage_chain_default_runs_rules_in_plan_order() -> None:
    chain = TriageChain.default()

    kinds = [type(rule) for rule in chain.rules]

    assert kinds == [
        ExifPlausibilityRule,
        DuplicateSuspicionRule,
        PiiScrubRule,
        SpamRule,
    ]


def test_triage_chain_run_collects_flags_in_rule_order_and_runs_every_rule() -> None:
    first = TriageFlag(kind="spam_suspected", detail="a", confidence=Confidence.LOW)
    second = TriageFlag(kind="pii_detected", detail="b", confidence=Confidence.HIGH)
    rules = [_FixedRule(first), _FixedRule(None), _FixedRule(second)]

    result = TriageChain(rules).run(_context())

    assert result.flags == (first, second)
    assert result.evaluated_at == NOW
    assert [rule.calls for rule in rules] == [1, 1, 1]


def test_triage_chain_default_on_clean_report_returns_no_flags() -> None:
    result = TriageChain.default().run(_context())

    assert result.has_flags is False


def test_triage_chain_default_on_messy_report_returns_flags_in_order() -> None:
    report = _report("Call 03123456789!!!!!!!!!!")
    photo = PhotoEvidence(
        media_id=FACTORY_IDS.new_id(), location=_north_of(ORIGIN, 50_000)
    )
    context = _context(report, nearby=(_nearby(10, 1),), photos=(photo,))

    result = TriageChain.default().run(context)

    assert result.kinds == (
        "exif_implausible",
        "duplicate_suspected",
        "pii_detected",
        "spam_suspected",
    )


def test_triage_chain_run_does_not_change_the_report() -> None:
    context = _context(_report("spam"))

    TriageChain.default().run(context)

    assert context.report.triage is None
