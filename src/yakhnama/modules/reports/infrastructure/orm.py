"""SQLAlchemy row model of the ``reports`` bounded context.

Rows are persistence shapes only (ADR 0004): ``mappers.py`` translates them to and
from the frozen ``Report`` aggregate, and nothing outside this package sees them.

``observation`` is the reporter's **exact, private** position as a WGS84 point
(EPSG:4326, ADR 0002) with ``spatial_index=False`` and an explicit, named GiST index;
public reads round it in SQL (``queries.rounded_degrees``). ``media_ids`` is a JSONB
array of UUID strings in the reporter's order and ``triage`` a JSONB object holding
exactly ``TriageResult.model_dump(mode="json")``; both are validated back through the
domain on every read.

``channel`` says how a report reached the platform (``account``, ``guest``,
``assisted``; ADR 0019); the three ``assisted_*`` columns hold the consent record
of an assisted report and are set exactly for that channel (checked by
``assisted_matches_channel``); a guest report names no organisation
(``guest_without_organisation``). ``guest_submissions`` and ``guest_challenges``
hold the guest channel's access records (ADR 0020): a submission's capability is
stored only as its SHA-256 digest, and a spent challenge only as its salt and
expiry. A submission's report is first reserved (``content_fingerprint``,
``source_id``, ``submitted_at``) and then filed (``report_id``, ``reference``); the
checks mirror the aggregate's invariants.

The revision chain is kept inside the table: ``supersedes_id`` and
``superseded_by_id`` reference ``reports.id`` (``ON DELETE RESTRICT``, and reports are
never deleted), and ``supersedes_id`` is unique, so at most one revision can ever
replace a report even if two corrections race. The reporter, organisation and source
ids carry no foreign key: they belong to the ``identity`` and ``provenance`` modules,
which this module knows only through their facades.

``lineage_id`` names the report's lineage, the id of its revision 1 (ADR 0022). The
repository sets it when it inserts a row (a revision copies it from the report it
supersedes); ``lineage_root_is_first_revision`` checks that revision 1 is its own
lineage and no other revision is. ``report_reviews`` holds one row per marked
lineage (its current mark, denormalised from the last one) and
``report_review_marks`` every mark ever made, insert-only; neither changes a
report row. ``media_ids`` has a GIN index (``jsonb_path_ops``) so the media
module's moderation queue can find the report that lists an asset uploaded before
it.

Patterns: Adapter (ORM row models behind the repository and query service adapters).
"""

from datetime import datetime
from typing import Final
from uuid import UUID

from geoalchemy2 import Geometry, WKBElement
from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from yakhnama.platform.db import Base

WGS84_SRID: Final = 4326
"""EPSG code of every stored geometry (ADR 0002)."""

REPORTS_TABLE: Final = "reports"
REPORT_REVIEWS_TABLE: Final = "report_reviews"
REPORT_REVIEW_MARKS_TABLE: Final = "report_review_marks"
REVIEW_STATES_CHECK: Final = "state IN ('new', 'reviewed', 'archived')"
GUEST_SUBMISSIONS_TABLE: Final = "guest_submissions"
GUEST_CHALLENGES_TABLE: Final = "guest_challenges"


class ReportRow(Base):
    """Row model of the ``reports`` table: one row per ``Report`` revision.

    Implements: Adapter (ORM row model of ``SqlAlchemyReportRepository``).

    Attributes:
        id: Primary key, the client-generated report id (UUIDv7).
        reporter_id: The reporting user.
        organization_id: The organisation reported for, if any.
        source_id: The provenance source the report is attributed to.
        observed_at: Start of the observation-time period, UTC.
        observed_at_precision: ``DatePrecision`` of ``observed_at``.
        observation: Exact WGS84 point of the reporter; private.
        accuracy_metres: The device's horizontal accuracy radius, if known.
        description: The reporter's words.
        original_language: BCP 47 language code of ``description``.
        hazard_code: The reporter's hazard guess, if any.
        hazard_confidence: The reporter's confidence in the guess.
        place_hint: Code of the place the reporter picked, if any.
        media_ids: Attached media asset ids, as a JSON array of strings.
        status: ``ReportStatus`` value.
        revision: Position in the revision chain, 1 for the original.
        supersedes_id: The revision this one replaces, if any.
        superseded_by_id: The revision that replaced this one, if any.
        withdrawal_reason: Why the reporter withdrew it, if they did.
        triage: The latest ``TriageResult`` as a JSON object, if any.
        submitted_at: When the platform accepted the submission, UTC.
        version: Optimistic-concurrency version, compared on every update.
        created_at: When the record was created, UTC.
        updated_at: When the record last changed, UTC.
        channel: ``ReportChannel`` value; ``account`` for rows older than it.
        assisted_consent_method: ``ConsentMethod`` of an assisted report.
        assisted_consent_statement_version: The consent statement's version.
        assisted_note: The assisting person's private note, if any.
        lineage_id: The id of the lineage's revision 1; set on insert.
    """

    __tablename__ = REPORTS_TABLE
    __table_args__ = (
        CheckConstraint(
            "(hazard_code IS NULL) = (hazard_confidence IS NULL)",
            name="hazard_guess_complete",
        ),
        CheckConstraint(
            "channel IN ('account', 'guest', 'assisted')", name="channel_known"
        ),
        CheckConstraint(
            "channel <> 'guest' OR organization_id IS NULL",
            name="guest_without_organisation",
        ),
        CheckConstraint(
            "assisted_consent_method IS NULL "
            "OR assisted_consent_method IN ('verbal', 'written')",
            name="assisted_consent_method_known",
        ),
        CheckConstraint(
            "assisted_consent_statement_version IS NULL "
            "OR assisted_consent_statement_version ~ '^[a-z0-9][a-z0-9._-]{0,31}$'",
            name="assisted_statement_version_format",
        ),
        CheckConstraint(
            "(channel = 'assisted') = (assisted_consent_method IS NOT NULL) "
            "AND (assisted_consent_method IS NULL) = "
            "(assisted_consent_statement_version IS NULL) "
            "AND (assisted_consent_method IS NOT NULL OR assisted_note IS NULL)",
            name="assisted_matches_channel",
        ),
        CheckConstraint(
            "(revision = 1) = (lineage_id = id)",
            name="lineage_root_is_first_revision",
        ),
        Index("ix_reports_observation_gist", "observation", postgresql_using="gist"),
        # Serves "which report lists this media asset" for the media moderation
        # queue (containment, ``@>``), read by the media module's SQL only.
        Index(
            "ix_reports_media_ids_gin",
            "media_ids",
            postgresql_using="gin",
            postgresql_ops={"media_ids": "jsonb_path_ops"},
        ),
        # Serves the newest-first keyset listing (read backwards).
        Index("ix_reports_created_at_id", "created_at", "id"),
        # Serves the moderators' guest and assisted queues (``channel=...``,
        # newest first); account reports, the bulk, stay out of it.
        Index(
            "ix_reports_channel_created_at_id_not_account",
            "channel",
            "created_at",
            "id",
            postgresql_where=text("channel <> 'account'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    reporter_id: Mapped[UUID] = mapped_column(index=True)
    organization_id: Mapped[UUID | None]
    source_id: Mapped[UUID]
    observed_at: Mapped[datetime] = mapped_column(index=True)
    observed_at_precision: Mapped[str] = mapped_column(String(16))
    observation: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="POINT", srid=WGS84_SRID, spatial_index=False)
    )
    accuracy_metres: Mapped[float | None] = mapped_column(Float)
    # Lengths follow the domain bounds: DESCRIPTION_MAX_LENGTH, the kernel's
    # eight-character language code, HAZARD_CODE_PATTERN and PLACE_CODE_PATTERN
    # (at most 64 characters) and REASON_MAX_LENGTH.
    description: Mapped[str] = mapped_column(String(4000))
    original_language: Mapped[str] = mapped_column(String(8))
    hazard_code: Mapped[str | None] = mapped_column(String(64), index=True)
    hazard_confidence: Mapped[str | None] = mapped_column(String(16))
    place_hint: Mapped[str | None] = mapped_column(String(64))
    media_ids: Mapped[list[str]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    supersedes_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reports.id", ondelete="RESTRICT"), unique=True
    )
    superseded_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reports.id", ondelete="RESTRICT")
    )
    withdrawal_reason: Mapped[str | None] = mapped_column(String(500))
    triage: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    submitted_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    channel: Mapped[str] = mapped_column(String(16), server_default=text("'account'"))
    assisted_consent_method: Mapped[str | None] = mapped_column(String(16))
    # CONSENT_STATEMENT_VERSION_PATTERN and ASSISTANCE_NOTE_MAX_LENGTH.
    assisted_consent_statement_version: Mapped[str | None] = mapped_column(String(32))
    assisted_note: Mapped[str | None] = mapped_column(String(500))
    lineage_id: Mapped[UUID] = mapped_column(
        ForeignKey("reports.id", ondelete="RESTRICT"), index=True
    )


class ReportReviewRow(Base):
    """Row model of ``report_reviews``: the current mark of one report lineage.

    Implements: Adapter (ORM row model of ``SqlAlchemyReportReviewRepository``).

    Attributes:
        lineage_id: Primary key, the id of the lineage's revision 1.
        state: ``ReviewState`` value of the last mark.
        last_mark_id: The last mark in ``report_review_marks``.
        reviewed_report_id: The revision the last mark was made on.
        reviewed_revision: That revision's number.
        updated_by: The moderator who made the last mark.
        version: Optimistic-concurrency version, compared on every update.
        created_at: When the lineage was first marked, UTC.
        updated_at: When it was last marked, UTC.
    """

    __tablename__ = REPORT_REVIEWS_TABLE
    __table_args__ = (
        CheckConstraint(REVIEW_STATES_CHECK, name="state_known"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("reviewed_revision >= 1", name="revision_positive"),
        CheckConstraint("created_at <= updated_at", name="times_ordered"),
    )

    lineage_id: Mapped[UUID] = mapped_column(
        ForeignKey("reports.id", ondelete="RESTRICT"), primary_key=True
    )
    state: Mapped[str] = mapped_column(String(16), index=True)
    # No foreign key: the mark row references this row, and the two are written
    # in one transaction by the repository, review first.
    last_mark_id: Mapped[UUID]
    reviewed_report_id: Mapped[UUID] = mapped_column(
        ForeignKey("reports.id", ondelete="RESTRICT")
    )
    reviewed_revision: Mapped[int] = mapped_column(Integer)
    updated_by: Mapped[UUID]
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class ReportReviewMarkRow(Base):
    """Row model of ``report_review_marks``: one mark, inserted once, never changed.

    Implements: Adapter (ORM row model of ``SqlAlchemyReportReviewRepository``).

    Attributes:
        id: Primary key, the mark id (UUIDv7).
        lineage_id: The lineage's review.
        state: ``ReviewState`` value the mark set.
        reason: Why, or a note; moderators only.
        report_id: The revision the moderator looked at.
        revision: That revision's number.
        actor_id: The moderator.
        marked_at: When, UTC.
    """

    __tablename__ = REPORT_REVIEW_MARKS_TABLE
    __table_args__ = (
        CheckConstraint(REVIEW_STATES_CHECK, name="state_known"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        # Serves the newest-first history of one lineage.
        Index(
            "ix_report_review_marks_lineage_id_marked_at_id",
            "lineage_id",
            "marked_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    lineage_id: Mapped[UUID] = mapped_column(
        ForeignKey("report_reviews.lineage_id", ondelete="RESTRICT")
    )
    state: Mapped[str] = mapped_column(String(16))
    # REVIEW_REASON_MAX_LENGTH.
    reason: Mapped[str | None] = mapped_column(String(500))
    report_id: Mapped[UUID] = mapped_column(
        ForeignKey("reports.id", ondelete="RESTRICT")
    )
    revision: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[UUID]
    marked_at: Mapped[datetime]


class GuestSubmissionRow(Base):
    """Row model of the ``guest_submissions`` table: one guest's access record.

    Implements: Adapter (ORM row model of ``SqlAlchemyGuestSubmissionRepository``).

    Attributes:
        id: Primary key, the submission id (UUIDv7); the guest report's reporter.
        capability_digest: SHA-256 of the capability, hexadecimal.
        expires_at: When the capability stops working, UTC.
        media_ids: Granted photo asset ids, as a JSON array of strings.
        report_id: The guest report, once filed; unique.
        reference: The receipt code, once filed; unique.
        content_fingerprint: SHA-256 of the submitted content, once reserved.
        source_id: The platform source the report cites, reserved before it
            exists.
        submitted_at: When the report was submitted (reserved), UTC; counted by
            the reports cap.
        version: Optimistic-concurrency version.
        created_at: When the submission was opened, UTC; counted by the cap.
        updated_at: When it last changed, UTC.
    """

    __tablename__ = GUEST_SUBMISSIONS_TABLE
    __table_args__ = (
        CheckConstraint(
            "(content_fingerprint IS NULL) = (source_id IS NULL) "
            "AND (content_fingerprint IS NULL) = (submitted_at IS NULL)",
            name="reserved_fields_together",
        ),
        CheckConstraint(
            "(report_id IS NULL) = (reference IS NULL) "
            "AND (report_id IS NULL OR content_fingerprint IS NOT NULL)",
            name="filed_after_reserved",
        ),
        CheckConstraint(
            "jsonb_typeof(media_ids) = 'array' AND jsonb_array_length(media_ids) <= 3",
            name="media_ids_at_most_three",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "created_at < expires_at AND created_at <= updated_at",
            name="times_ordered",
        ),
        CheckConstraint(
            "capability_digest ~ '^[0-9a-f]{64}$'", name="capability_digest_format"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    capability_digest: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(index=True)
    media_ids: Mapped[list[str]] = mapped_column(JSONB)
    report_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reports.id", ondelete="RESTRICT"), unique=True
    )
    reference: Mapped[str | None] = mapped_column(String(12), unique=True)
    content_fingerprint: Mapped[str | None] = mapped_column(String(64))
    source_id: Mapped[UUID | None]
    submitted_at: Mapped[datetime | None] = mapped_column(index=True)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(index=True)
    updated_at: Mapped[datetime]


class GuestChallengeRow(Base):
    """Row model of the ``guest_challenges`` table: one redeemed challenge.

    Kept only until the challenge expires; after that it could open nothing.

    Implements: Adapter (ORM row model of ``SqlAlchemySpentChallengeRepository``).

    Attributes:
        salt: Primary key, the challenge's random salt.
        expires_at: When the challenge expired or expires, UTC.
    """

    __tablename__ = GUEST_CHALLENGES_TABLE

    salt: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(index=True)
