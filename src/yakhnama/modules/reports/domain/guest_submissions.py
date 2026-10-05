"""Guest submissions: how a person without an account puts one report on record.

A guest first solves a **proof-of-work challenge** the platform signed (ADR 0020):
find a decimal ``nonce`` such that ``SHA-256(salt + nonce)`` starts with
``difficulty_bits`` zero bits. Solving it costs a phone a few seconds of computation
and nothing else; no third party learns who reports. A solved challenge opens one
``GuestSubmission`` and is spent for good, so it cannot open a second one.

The submission is a short-lived access record. The guest proves they hold it with a
**capability**: a random secret the platform hands out once and stores only as its
SHA-256 digest. Until it expires the capability allows at most
``GUEST_MEDIA_MAX`` photo uploads and exactly one report.

A submission moves through three states, each written with an optimistic version
check so concurrent requests cannot both win:

1. **open**: photos may be reserved (``attach_media``), one slot at a time, before
   the media module creates the asset;
2. **reserved**: ``reserve_report`` records the report's content fingerprint, the
   id of its platform source and the submission time *before* the source or the
   report exists, so exactly one request goes on to create them;
3. **filed**: ``record_report`` adds the report id and the receipt reference.

After the reservation only a retry of the very same report is answered (with the
same receipt), also for ``GuestSubmissionLimits.receipt_grace`` after the
capability expired, so a lost response on a weak connection is harmless.

The difficulty of new challenges rises with the number of submissions opened in the
last hour (``GuestSubmissionLimits.difficulty_for``), and two hourly caps bound what
all guests together may do (``GuestCap``).

The submission is the **reporter** of the guest report and the **owner** of its
photos: an opaque principal that exists for one report and never identifies a person
(``ReportAttribution``).

Patterns: Entity, Aggregate Root, Value Object.
"""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Final, Self, get_args

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from yakhnama.modules.reports.domain.errors import (
    GuestMediaLimitError,
    GuestSubmissionClosedError,
)
from yakhnama.modules.reports.domain.value_objects import GuestImageType
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import EntityId

GUEST_MEDIA_MAX: Final = 3
"""Most photos one guest submission may upload (maintainer decision, 2026-10-05)."""

GUEST_IMAGE_TYPES: Final = frozenset(get_args(GuestImageType))
"""The media types a guest may upload: images only.

Mirrors the image members of the media module's ``MimeType`` (the domain may not
import another module); a unit test keeps the two equal.
"""

PROOF_OF_WORK_ALGORITHM: Final = "SHA-256"
"""The only proof-of-work hash; named in every challenge for the client."""

DIFFICULTY_BITS_MIN: Final = 1
DIFFICULTY_BITS_MAX: Final = 32
"""Bounds of the leading-zero-bit difficulty; 32 bits is minutes on a phone."""

DifficultyBits = Annotated[int, Field(ge=DIFFICULTY_BITS_MIN, le=DIFFICULTY_BITS_MAX)]

DIFFICULTY_BITS_CEILING: Final = 22
"""Most bits the platform ever asks for (**proposed**, ADR 0020, Q222).

About 4.2 million hashes on average: 85 to 140 seconds on a Moto G4 computing
SHA-256 with WebCrypto in a worker (30 000 to 50 000 hashes per second), so even at
the ceiling an honest guest on a low-end phone gets through in a few minutes.
"""

SALT_PATTERN: Final = r"^[A-Za-z0-9_-]{16,64}$"
ChallengeSalt = Annotated[str, StringConstraints(pattern=SALT_PATTERN)]
"""The random, URL-safe salt of one challenge."""

NONCE_PATTERN: Final = r"^[0-9]{1,20}$"
ProofNonce = Annotated[str, StringConstraints(pattern=NONCE_PATTERN)]
"""The guest's answer: a decimal number of at most 20 digits."""

CAPABILITY_PATTERN: Final = r"^[A-Za-z0-9_-]{43,128}$"
GuestCapability = Annotated[str, StringConstraints(pattern=CAPABILITY_PATTERN)]
"""A capability as the guest presents it: at least 256 bits, URL-safe base64."""

SHA256_HEX_PATTERN: Final = r"^[0-9a-f]{64}$"
Sha256Hex = Annotated[str, StringConstraints(pattern=SHA256_HEX_PATTERN)]
"""A SHA-256 digest in lower-case hexadecimal."""

REFERENCE_ALPHABET: Final = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
"""Characters of a receipt reference: no ``0``/``O``, ``1``/``I``/``L`` or ``U``."""

REFERENCE_GROUP_LENGTH: Final = 4
REFERENCE_PATTERN: Final = (
    rf"^YK-[{REFERENCE_ALPHABET}]{{{REFERENCE_GROUP_LENGTH}}}"
    rf"-[{REFERENCE_ALPHABET}]{{{REFERENCE_GROUP_LENGTH}}}$"
)
GuestReference = Annotated[str, StringConstraints(pattern=REFERENCE_PATTERN)]
"""The receipt code a guest can quote, such as ``YK-7KQM-3HXA`` (about 39 bits)."""

_BITS_PER_BYTE: Final = 8


def sha256_hex(text: str) -> str:
    """Return the SHA-256 digest of ``text`` (UTF-8) in hexadecimal.

    Args:
        text: Any text, for example a capability.

    Returns:
        64 lower-case hexadecimal characters.
    """
    return hashlib.sha256(text.encode()).hexdigest()


def leading_zero_bits(digest: bytes) -> int:
    """Count the zero bits at the start of ``digest``.

    Args:
        digest: Any byte string.

    Returns:
        The number of leading zero bits, ``8 * len(digest)`` if all are zero.
    """
    count = 0
    for byte in digest:
        if byte == 0:
            count += _BITS_PER_BYTE
            continue
        return count + _BITS_PER_BYTE - byte.bit_length()
    return count


def is_proof_of_work_valid(salt: str, nonce: str, difficulty_bits: int) -> bool:
    """Tell whether ``nonce`` solves the challenge with ``salt``.

    Args:
        salt: The challenge's salt.
        nonce: The guest's decimal answer.
        difficulty_bits: How many leading zero bits the hash needs.

    Returns:
        ``True`` if ``SHA-256(salt + nonce)`` has at least ``difficulty_bits``
        leading zero bits.
    """
    digest = hashlib.sha256(f"{salt}{nonce}".encode()).digest()
    return leading_zero_bits(digest) >= difficulty_bits


class GuestChallenge(BaseModel):
    """One proof-of-work challenge, as the platform signs and later reads it.

    Implements: Value Object.

    Attributes:
        salt: Random, unique per challenge.
        difficulty_bits: Leading zero bits the answer's hash needs.
        expires_at: After this instant the challenge opens nothing, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    salt: ChallengeSalt
    difficulty_bits: DifficultyBits
    expires_at: AwareDatetime

    @field_validator("expires_at", mode="after")
    @classmethod
    def _normalise_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    def is_expired(self, now: datetime) -> bool:
        """Tell whether the challenge has expired.

        Args:
            now: The current instant.

        Returns:
            ``True`` from ``expires_at`` on.
        """
        return now >= self.expires_at

    def is_solved_by(self, nonce: str) -> bool:
        """Tell whether ``nonce`` solves this challenge.

        Args:
            nonce: The guest's answer.

        Returns:
            ``is_proof_of_work_valid(salt, nonce, difficulty_bits)``.
        """
        return is_proof_of_work_valid(self.salt, nonce, self.difficulty_bits)


class GuestCap(StrEnum):
    """The two hourly caps every guest shares (ADR 0020).

    Each cap is checked under its own database lock, so concurrent requests are
    counted one after the other and cannot overshoot it together.

    Implements: Value Object.
    """

    OPENED = "opened"
    """Submissions opened (challenges redeemed) in the last hour."""
    REPORTS = "reports"
    """Guest reports submitted (reserved or filed) in the last hour."""


class GuestSubmissionLimits(BaseModel):
    """The configurable limits of guest submissions (settings, ADR 0020).

    The difficulty of a new challenge is ``difficulty_for(opened)``: the base
    ``difficulty_bits`` plus one bit for every ``difficulty_step`` submissions
    opened in the last hour, never more than ``difficulty_max_bits``. Each bit
    doubles the expected work, so a flood makes every further submission more
    expensive while a quiet hour costs an honest guest only the base.

    Implements: Value Object.

    Attributes:
        difficulty_bits: Base difficulty of every new challenge.
        difficulty_max_bits: The most bits the curve reaches, at most
            ``DIFFICULTY_BITS_CEILING``.
        difficulty_step: Submissions opened in the last hour per extra bit.
        challenge_ttl: How long a challenge may be solved and redeemed.
        capability_ttl: How long a submission's capability works.
        opened_per_hour: Most submissions opened in any rolling hour, by all
            guests together (``GuestCap.OPENED``).
        reports_per_hour: Most guest reports submitted in any rolling hour
            (``GuestCap.REPORTS``); what protects the moderators' queue.
        receipt_grace: How long after the capability expired a retry of the
            submitted report still gets its receipt.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    difficulty_bits: DifficultyBits = 18
    difficulty_max_bits: int = Field(
        default=DIFFICULTY_BITS_CEILING,
        ge=DIFFICULTY_BITS_MIN,
        le=DIFFICULTY_BITS_CEILING,
    )
    difficulty_step: int = Field(default=200, ge=1, le=100_000)
    challenge_ttl: timedelta = timedelta(minutes=10)
    capability_ttl: timedelta = timedelta(minutes=30)
    opened_per_hour: int = Field(default=2000, ge=1, le=1_000_000)
    reports_per_hour: int = Field(default=200, ge=1, le=100_000)
    receipt_grace: timedelta = timedelta(hours=24)

    @model_validator(mode="after")
    def _check_limits(self) -> Self:
        if (
            self.challenge_ttl <= timedelta(0)
            or self.capability_ttl <= timedelta(0)
            or self.receipt_grace < timedelta(0)
        ):
            message = (
                "challenge_ttl and capability_ttl must be positive, receipt_grace "
                "not negative"
            )
            raise ValueError(message)
        if self.difficulty_bits > self.difficulty_max_bits:
            message = "difficulty_bits must not exceed difficulty_max_bits"
            raise ValueError(message)
        return self

    def difficulty_for(self, opened_last_hour: int) -> int:
        """Return the difficulty of a challenge issued now.

        Args:
            opened_last_hour: Submissions opened in the last hour, by all guests.

        Returns:
            ``difficulty_bits + opened_last_hour // difficulty_step``, at most
            ``difficulty_max_bits``.
        """
        extra = max(0, opened_last_hour) // self.difficulty_step
        return min(self.difficulty_max_bits, self.difficulty_bits + extra)

    def cap_of(self, cap: GuestCap) -> int:
        """Return the hourly limit of ``cap``.

        Args:
            cap: Which cap.

        Returns:
            ``opened_per_hour`` or ``reports_per_hour``.
        """
        return self.opened_per_hour if cap is GuestCap.OPENED else self.reports_per_hour


class GuestSubmission(BaseModel):
    """One guest's access to put one report, with up to three photos, on record.

    Invariants, checked on every construction:

    - ``content_fingerprint``, ``source_id`` and ``submitted_at`` are all set
      (reserved or filed) or all unset (open);
    - ``report_id`` and ``reference`` are both set (filed) or both unset, and only
      on a reserved submission;
    - at most ``GUEST_MEDIA_MAX`` media ids, all different;
    - ``created_at <= updated_at`` and ``created_at < expires_at``.

    Implements: Entity / Aggregate Root.

    Attributes:
        id: Stable identity (UUIDv7); the reporter of its report and the owner of
            its photos.
        capability_digest: SHA-256 of the capability; the capability itself is
            never stored.
        expires_at: When the capability stops working, UTC.
        media_ids: The photo slots reserved for it, in order; each names the
            asset the media module creates for it.
        content_fingerprint: SHA-256 of the submitted content, to tell a retry of
            the same report from a different one; set by the reservation.
        source_id: The platform source the report will cite, chosen by the
            reservation before the source exists.
        submitted_at: When the report was submitted (reserved), UTC; counted by
            ``GuestCap.REPORTS``.
        report_id: The report it carried, once filed.
        reference: The receipt code of that report, once filed.
        version: Optimistic-concurrency version.
        created_at: When the submission was opened, UTC.
        updated_at: When it last changed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    capability_digest: Sha256Hex
    expires_at: AwareDatetime
    media_ids: tuple[EntityId, ...] = Field(default=(), max_length=GUEST_MEDIA_MAX)
    content_fingerprint: Sha256Hex | None = None
    source_id: EntityId | None = None
    submitted_at: AwareDatetime | None = None
    report_id: EntityId | None = None
    reference: GuestReference | None = None
    version: int = Field(default=1, ge=1, le=2**31 - 1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator(
        "expires_at", "submitted_at", "created_at", "updated_at", mode="after"
    )
    @classmethod
    def _normalise_to_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        reservation = (self.content_fingerprint, self.source_id, self.submitted_at)
        filing = (self.report_id, self.reference)
        if not _all_or_none(reservation):
            message = "content_fingerprint, source_id and submitted_at are set together"
            raise ValueError(message)
        if not _all_or_none(filing):
            message = "report_id and reference are set together"
            raise ValueError(message)
        if self.report_id is not None and self.content_fingerprint is None:
            message = "a report is filed only after it was reserved"
            raise ValueError(message)
        if len(set(self.media_ids)) != len(self.media_ids):
            message = "a media asset is granted to a submission only once"
            raise ValueError(message)
        if self.updated_at < self.created_at or self.expires_at <= self.created_at:
            message = "created_at must precede updated_at and expires_at"
            raise ValueError(message)
        return self

    @classmethod
    def open(
        cls,
        submission_id: EntityId,
        capability: str,
        *,
        limits: GuestSubmissionLimits,
        clock: Clock,
    ) -> AggregateChange["GuestSubmission"]:
        """Open a submission whose capability is ``capability``.

        No domain event is recorded: the submission only grants access, and the
        report it carries records ``ReportSubmitted`` with the ``guest`` channel.

        Args:
            submission_id: The new id.
            capability: The secret handed to the guest; only its digest is kept.
            limits: Supplies the capability's lifetime.
            clock: Source of every timestamp.

        Returns:
            The open submission.
        """
        now = clock.now()
        state = cls(
            id=submission_id,
            capability_digest=sha256_hex(capability),
            expires_at=now + limits.capability_ttl,
            created_at=now,
            updated_at=now,
        )
        return AggregateChange[GuestSubmission](state=state)

    @property
    def is_open(self) -> bool:
        """Tell whether the submission still waits for its report.

        Returns:
            ``True`` until a report was reserved; only then may photos be added.
        """
        return self.content_fingerprint is None

    @property
    def is_filed(self) -> bool:
        """Tell whether the report was stored and the receipt drawn.

        Returns:
            ``True`` once ``record_report`` ran.
        """
        return self.report_id is not None

    def is_expired(self, now: datetime) -> bool:
        """Tell whether the capability has expired.

        Args:
            now: The current instant.

        Returns:
            ``True`` from ``expires_at`` on.
        """
        return now >= self.expires_at

    def is_retry_of(self, content_fingerprint: str) -> bool:
        """Tell whether a report with this fingerprint is the one reserved here.

        Args:
            content_fingerprint: SHA-256 of the content a request carries.

        Returns:
            ``True`` if a report was reserved and has exactly this fingerprint.
        """
        return self.content_fingerprint is not None and hmac.compare_digest(
            self.content_fingerprint, content_fingerprint
        )

    def is_within_receipt_grace(self, now: datetime, grace: timedelta) -> bool:
        """Tell whether a retry of the reserved report is still answered.

        Args:
            now: The current instant.
            grace: How long after expiry a retry is still answered.

        Returns:
            ``True`` before ``expires_at + grace``.
        """
        return now < self.expires_at + grace

    def is_capability(self, capability: str) -> bool:
        """Tell whether ``capability`` is this submission's.

        Args:
            capability: What the guest presented.

        Returns:
            ``True`` if its digest equals the stored one, compared in constant
            time.
        """
        return hmac.compare_digest(sha256_hex(capability), self.capability_digest)

    def attach_media(
        self, asset_id: EntityId, *, clock: Clock
    ) -> AggregateChange["GuestSubmission"]:
        """Reserve a photo slot for the asset ``asset_id`` is about to name.

        Args:
            asset_id: The id the media module will give the new asset.
            clock: Source of ``updated_at``.

        Returns:
            The submission with the asset, or unchanged if it already has it.

        Raises:
            GuestSubmissionClosedError: If the report was already submitted.
            GuestMediaLimitError: If ``GUEST_MEDIA_MAX`` slots are taken.
        """
        if asset_id in self.media_ids:
            return AggregateChange[GuestSubmission](state=self)
        self._require_open()
        if len(self.media_ids) >= GUEST_MEDIA_MAX:
            raise GuestMediaLimitError.for_submission(self.id, GUEST_MEDIA_MAX)
        return AggregateChange[GuestSubmission](
            state=self._evolve(clock.now(), media_ids=(*self.media_ids, asset_id))
        )

    def detach_media(
        self, asset_id: EntityId, *, clock: Clock
    ) -> AggregateChange["GuestSubmission"]:
        """Give back the slot of an asset whose upload grant failed.

        Only an open submission changes: once a report is reserved its photo
        list no longer matters, because no further upload can be granted.

        Args:
            asset_id: The asset whose creation or grant failed.
            clock: Source of ``updated_at``.

        Returns:
            The submission without the asset, or unchanged.
        """
        if asset_id not in self.media_ids or not self.is_open:
            return AggregateChange[GuestSubmission](state=self)
        remaining = tuple(
            media_id for media_id in self.media_ids if media_id != asset_id
        )
        return AggregateChange[GuestSubmission](
            state=self._evolve(clock.now(), media_ids=remaining)
        )

    def reserve_report(
        self,
        content_fingerprint: str,
        source_id: EntityId,
        *,
        clock: Clock,
    ) -> AggregateChange["GuestSubmission"]:
        """Claim the submission's one report before its source or row exists.

        Saved with a version check, so of two concurrent requests exactly one
        reserves; the other reloads and sees whose report it is.

        Args:
            content_fingerprint: SHA-256 of the report's content.
            source_id: The id the platform source of the report will have.
            clock: Source of ``submitted_at`` and ``updated_at``.

        Returns:
            The reserved submission.

        Raises:
            GuestSubmissionClosedError: If a report was already reserved.
        """
        self._require_open()
        now = clock.now()
        return AggregateChange[GuestSubmission](
            state=self._evolve(
                now,
                content_fingerprint=content_fingerprint,
                source_id=source_id,
                submitted_at=now,
            )
        )

    def record_report(
        self, report_id: EntityId, reference: str, *, clock: Clock
    ) -> AggregateChange["GuestSubmission"]:
        """File the reserved report: its id and the receipt reference.

        Args:
            report_id: The new guest report.
            reference: Its receipt code.
            clock: Source of ``updated_at``.

        Returns:
            The filed submission.

        Raises:
            GuestSubmissionClosedError: If no report was reserved, or one was
                already filed.
        """
        if self.is_open or self.is_filed:
            raise GuestSubmissionClosedError.for_submission(self.id)
        return AggregateChange[GuestSubmission](
            state=self._evolve(clock.now(), report_id=report_id, reference=reference)
        )

    def _require_open(self) -> None:
        if not self.is_open:
            raise GuestSubmissionClosedError.for_submission(self.id)

    def _evolve(self, now: datetime, **updates: object) -> "GuestSubmission":
        # model_validate, not model_copy, so a change cannot break an invariant.
        fields = {name: getattr(self, name) for name in type(self).model_fields}
        return self.model_validate(
            {**fields, **updates, "version": self.version + 1, "updated_at": now}
        )


def _all_or_none(values: tuple[object, ...]) -> bool:
    return all(value is None for value in values) or all(
        value is not None for value in values
    )
