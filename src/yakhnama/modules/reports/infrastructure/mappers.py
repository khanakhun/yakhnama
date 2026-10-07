"""Translate between the ``Report`` aggregate, its row model and its read records.

Shapely and WKB stay on this side of the boundary: the observation travels as the
kernel's ``Coordinates`` in the domain and as a GeoAlchemy2 ``WKBElement`` here. WKB
keeps every coordinate bit for bit, so a stored point reads back equal to the one
saved. Every read goes through ``model_validate``, so a row that no longer satisfies
the domain's invariants fails loudly instead of producing an invalid aggregate.

Patterns: Anti-Corruption Layer (mapper).
"""

from datetime import datetime
from uuid import UUID

from geoalchemy2 import WKBElement
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point as ShapelyPoint

from yakhnama.modules.reports.application.dto import ReportRecord, ReportReviewRecord
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.guest_submissions import GuestSubmission
from yakhnama.modules.reports.domain.reviews import (
    ReportReview,
    ReviewMark,
    ReviewState,
)
from yakhnama.modules.reports.domain.value_objects import AssistedSubmission
from yakhnama.modules.reports.infrastructure.orm import (
    WGS84_SRID,
    GuestSubmissionRow,
    ReportReviewMarkRow,
    ReportReviewRow,
    ReportRow,
)
from yakhnama.platform.db import dump_json_column
from yakhnama.shared_kernel.value_objects import Coordinates, DateWithPrecision


def coordinates_to_element(coordinates: Coordinates) -> WKBElement:
    """Convert coordinates to a WGS84 point ``WKBElement``.

    Args:
        coordinates: The point.

    Returns:
        The extended WKB value for a ``geometry(Point, 4326)`` column.
    """
    return from_shape(
        ShapelyPoint(coordinates.longitude, coordinates.latitude),
        srid=WGS84_SRID,
        extended=True,
    )


def element_to_coordinates(element: WKBElement) -> Coordinates:
    """Convert a stored point back to coordinates.

    Args:
        element: The column value.

    Returns:
        The validated ``Coordinates``.

    Raises:
        TypeError: If the column holds something other than a point, which the
            ``POINT`` column type rules out.
    """
    point = to_shape(element)
    if not isinstance(point, ShapelyPoint):
        message = f"observation column holds a {point.geom_type}, not a Point"
        raise TypeError(message)
    return Coordinates(longitude=point.x, latitude=point.y)


def observed_at_from_columns(instant: datetime, precision: str) -> DateWithPrecision:
    """Join the observation instant and precision columns into one value.

    Args:
        instant: The stored ``observed_at``.
        precision: The stored ``observed_at_precision``.

    Returns:
        The validated value.
    """
    return DateWithPrecision.model_validate({"value": instant, "precision": precision})


def report_to_values(report: Report) -> dict[str, object]:
    """Return every column value of ``report`` except the primary key.

    Args:
        report: The aggregate.

    Returns:
        Column name to value, for inserts and versioned updates alike.
    """
    guess = report.hazard_guess
    assisted = report.assisted
    return {
        "reporter_id": report.reporter_id,
        "organization_id": report.organization_id,
        "source_id": report.source_id,
        "observed_at": report.observed_at.value,
        "observed_at_precision": report.observed_at.precision.value,
        "observation": coordinates_to_element(report.observation.coordinates),
        "accuracy_metres": (
            None
            if report.observation.accuracy is None
            else report.observation.accuracy.value
        ),
        "description": report.description,
        "original_language": report.original_language,
        "hazard_code": None if guess is None else guess.hazard_code,
        "hazard_confidence": None if guess is None else guess.confidence.value,
        "place_hint": report.place_hint,
        "media_ids": [str(media_id) for media_id in report.media_ids],
        "status": report.status.value,
        "revision": report.revision,
        "supersedes_id": report.supersedes_id,
        "superseded_by_id": report.superseded_by_id,
        "withdrawal_reason": report.withdrawal_reason,
        "triage": dump_json_column(report.triage),
        "submitted_at": report.submitted_at,
        "version": report.version,
        "created_at": report.created_at,
        "updated_at": report.updated_at,
        "channel": report.channel.value,
        "assisted_consent_method": (
            None if assisted is None else assisted.consent_method.value
        ),
        "assisted_consent_statement_version": (
            None if assisted is None else assisted.consent_statement_version
        ),
        "assisted_note": None if assisted is None else assisted.note,
    }


def report_to_row(report: Report) -> ReportRow:
    """Build the ``reports`` row of ``report``.

    Args:
        report: The aggregate.

    Returns:
        A transient ``ReportRow`` carrying the same values.
    """
    return ReportRow(id=report.id, **report_to_values(report))


def _observation(coordinates: Coordinates, accuracy_metres: float | None) -> object:
    return {
        "coordinates": coordinates,
        "accuracy": None if accuracy_metres is None else {"value": accuracy_metres},
    }


def _assisted(row: ReportRow) -> AssistedSubmission | None:
    if row.assisted_consent_method is None:
        return None
    return AssistedSubmission.model_validate(
        {
            "consent_method": row.assisted_consent_method,
            "consent_statement_version": row.assisted_consent_statement_version,
            "note": row.assisted_note,
        }
    )


def _common_fields(row: ReportRow) -> dict[str, object]:
    # Every field ``Report`` and ``ReportRecord`` share, except the observation,
    # which the caller supplies exact or rounded.
    guess = (
        None
        if row.hazard_code is None
        else {"hazard_code": row.hazard_code, "confidence": row.hazard_confidence}
    )
    return {
        "id": row.id,
        "reporter_id": row.reporter_id,
        "organization_id": row.organization_id,
        "source_id": row.source_id,
        "observed_at": observed_at_from_columns(
            row.observed_at, row.observed_at_precision
        ),
        "description": row.description,
        "original_language": row.original_language,
        "hazard_guess": guess,
        "place_hint": row.place_hint,
        "media_ids": tuple(UUID(media_id) for media_id in row.media_ids),
        "status": row.status,
        "revision": row.revision,
        "supersedes_id": row.supersedes_id,
        "superseded_by_id": row.superseded_by_id,
        "withdrawal_reason": row.withdrawal_reason,
        "triage": row.triage,
        "submitted_at": row.submitted_at,
        "version": row.version,
        "created_at": row.created_at,
        "channel": row.channel,
        "assisted": _assisted(row),
    }


def row_to_report(row: ReportRow) -> Report:
    """Rebuild a report from its row, exact position included.

    Args:
        row: A fully loaded row of ``reports``.

    Returns:
        The validated ``Report``.
    """
    return Report.model_validate(
        {
            **_common_fields(row),
            "observation": _observation(
                element_to_coordinates(row.observation), row.accuracy_metres
            ),
            "updated_at": row.updated_at,
        }
    )


def row_to_exact_record(
    row: ReportRow, review: ReportReviewRecord | None = None
) -> ReportRecord:
    """Build the internal read record of a row with the exact position and accuracy.

    Args:
        row: A fully loaded row of ``reports``.
        review: The lineage's stored review, if the query joined one.

    Returns:
        The validated ``ReportRecord``.
    """
    return ReportRecord.model_validate(
        {
            **_common_fields(row),
            "observation": _observation(
                element_to_coordinates(row.observation), row.accuracy_metres
            ),
            "lineage_id": row.lineage_id,
            "review": review,
        }
    )


def row_to_rounded_record(
    row: ReportRow, rounded: Coordinates, review: ReportReviewRecord | None = None
) -> ReportRecord:
    """Build a read record carrying an already rounded position and no accuracy.

    Used by listings, which never load the exact point (see ``queries``).

    Args:
        row: A row of ``reports`` loaded without ``observation`` and
            ``accuracy_metres``.
        rounded: The position rounded in SQL.
        review: The lineage's stored review, if the query joined one.

    Returns:
        The validated ``ReportRecord``.
    """
    return ReportRecord.model_validate(
        {
            **_common_fields(row),
            "observation": _observation(rounded, None),
            "lineage_id": row.lineage_id,
            "review": review,
        }
    )


def report_review_to_values(review: ReportReview) -> dict[str, object]:
    """Return every column value of a review except the primary key.

    Args:
        review: The aggregate.

    Returns:
        Column name to value.
    """
    mark = review.last_mark
    return {
        "state": review.state.value,
        "last_mark_id": mark.id,
        "reviewed_report_id": mark.report_id,
        "reviewed_revision": mark.revision,
        "updated_by": mark.actor_id,
        "version": review.version,
        "created_at": review.created_at,
        "updated_at": review.updated_at,
    }


def review_mark_to_row(lineage_id: UUID, mark: ReviewMark) -> ReportReviewMarkRow:
    """Build the history row of one mark.

    Args:
        lineage_id: The lineage the mark belongs to.
        mark: The mark.

    Returns:
        A new, unsaved row.
    """
    return ReportReviewMarkRow(
        id=mark.id,
        lineage_id=lineage_id,
        state=mark.state.value,
        reason=mark.reason,
        report_id=mark.report_id,
        revision=mark.revision,
        actor_id=mark.actor_id,
        marked_at=mark.marked_at,
    )


def row_to_review_mark(row: ReportReviewMarkRow) -> ReviewMark:
    """Rebuild one mark from its history row.

    Args:
        row: A row of ``report_review_marks``.

    Returns:
        The validated ``ReviewMark``.
    """
    return ReviewMark(
        id=row.id,
        state=ReviewState(row.state),
        reason=row.reason,
        report_id=row.report_id,
        revision=row.revision,
        actor_id=row.actor_id,
        marked_at=row.marked_at,
    )


def rows_to_report_review(
    row: ReportReviewRow, last_mark: ReportReviewMarkRow
) -> ReportReview:
    """Rebuild a review from its row and the row of its last mark.

    Args:
        row: A row of ``report_reviews``.
        last_mark: The row of ``row.last_mark_id``.

    Returns:
        The validated ``ReportReview``.
    """
    return ReportReview(
        id=row.lineage_id,
        state=ReviewState(row.state),
        last_mark=row_to_review_mark(last_mark),
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def guest_submission_to_values(submission: GuestSubmission) -> dict[str, object]:
    """Return every column value of a guest submission except the primary key.

    Args:
        submission: The aggregate.

    Returns:
        Column name to value.
    """
    return {
        "capability_digest": submission.capability_digest,
        "expires_at": submission.expires_at,
        "media_ids": [str(media_id) for media_id in submission.media_ids],
        "report_id": submission.report_id,
        "reference": submission.reference,
        "content_fingerprint": submission.content_fingerprint,
        "source_id": submission.source_id,
        "submitted_at": submission.submitted_at,
        "version": submission.version,
        "created_at": submission.created_at,
        "updated_at": submission.updated_at,
    }


def row_to_guest_submission(row: GuestSubmissionRow) -> GuestSubmission:
    """Rebuild a guest submission from its row.

    Args:
        row: A fully loaded row of ``guest_submissions``.

    Returns:
        The validated ``GuestSubmission``.
    """
    return GuestSubmission.model_validate(
        {
            "id": row.id,
            "capability_digest": row.capability_digest,
            "expires_at": row.expires_at,
            "media_ids": tuple(UUID(media_id) for media_id in row.media_ids),
            "report_id": row.report_id,
            "reference": row.reference,
            "content_fingerprint": row.content_fingerprint,
            "source_id": row.source_id,
            "submitted_at": row.submitted_at,
            "version": row.version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )
