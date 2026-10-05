"""Guest submissions: how a person without an account puts one report on record.

A guest first solves a **proof-of-work challenge** the platform signed (ADR 0020):
find a decimal ``nonce`` such that ``SHA-256(salt + nonce)`` starts with
``difficulty_bits`` zero bits. Solving it costs a phone a few seconds of computation
and nothing else; no third party learns who reports. A solved challenge opens one
``GuestSubmission`` and is spent for good, so it cannot open a second one.

The submission is a short-lived access record. The guest proves they hold it with a
**capability**: a random secret the platform hands out once and stores only as its
SHA-256 digest. Until it expires the capability allows at most
``GUEST_MEDIA_MAX`` photo uploads and exactly one report; after the report the
submission is closed and only a retry of the very same report is answered (with the
same receipt), so a lost response on a weak connection is harmless.

The submission is the **reporter** of the guest report and the **owner** of its
photos: an opaque principal that exists for one report and never identifies a person
(``ReportAttribution``).

Patterns: Entity, Aggregate Root, Value Object.
"""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
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


class GuestSubmissionLimits(BaseModel):
    """The configurable limits of guest submissions (settings, ADR 0020).

    Implements: Value Object.

    Attributes:
        difficulty_bits: Difficulty of every new challenge.
        challenge_ttl: How long a challenge may be solved and redeemed.
        capability_ttl: How long a submission's capability works.
        submissions_per_hour: Most submissions opened in any rolling hour across
            all guests together.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    difficulty_bits: DifficultyBits = 16
    challenge_ttl: timedelta = timedelta(minutes=10)
    capability_ttl: timedelta = timedelta(minutes=30)
    submissions_per_hour: int = Field(default=200, ge=1, le=100_000)

    @model_validator(mode="after")
    def _check_durations(self) -> Self:
        if self.challenge_ttl <= timedelta(0) or self.capability_ttl <= timedelta(0):
            message = "challenge_ttl and capability_ttl must be positive"
            raise ValueError(message)
        return self


class GuestSubmission(BaseModel):
    """One guest's access to put one report, with up to three photos, on record.

    Invariants, checked on every construction:

    - ``report_id``, ``reference``, ``content_fingerprint`` and ``submitted_at``
      are all set (closed) or all unset (open);
    - at most ``GUEST_MEDIA_MAX`` media ids, all different;
    - ``created_at <= updated_at`` and ``created_at < expires_at``.

    Implements: Entity / Aggregate Root.

    Attributes:
        id: Stable identity (UUIDv7); the reporter of its report and the owner of
            its photos.
        capability_digest: SHA-256 of the capability; the capability itself is
            never stored.
        expires_at: When the capability stops working, UTC.
        media_ids: The photo assets granted to it, in order.
        report_id: The report it carried, once submitted.
        reference: The receipt code of that report.
        content_fingerprint: SHA-256 of the submitted content, to tell a retry of
            the same report from a different one.
        submitted_at: When the report was submitted, UTC.
        version: Optimistic-concurrency version.
        created_at: When the submission was opened, UTC.
        updated_at: When it last changed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    capability_digest: Sha256Hex
    expires_at: AwareDatetime
    media_ids: tuple[EntityId, ...] = Field(default=(), max_length=GUEST_MEDIA_MAX)
    report_id: EntityId | None = None
    reference: GuestReference | None = None
    content_fingerprint: Sha256Hex | None = None
    submitted_at: AwareDatetime | None = None
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
        closing_fields = (
            self.report_id,
            self.reference,
            self.content_fingerprint,
            self.submitted_at,
        )
        if any(value is None for value in closing_fields) and any(
            value is not None for value in closing_fields
        ):
            message = (
                "report_id, reference, content_fingerprint and submitted_at are "
                "set together"
            )
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
            ``True`` until a report was submitted.
        """
        return self.report_id is None

    def is_expired(self, now: datetime) -> bool:
        """Tell whether the capability has expired.

        Args:
            now: The current instant.

        Returns:
            ``True`` from ``expires_at`` on.
        """
        return now >= self.expires_at

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
        """Record that a photo upload was granted to this submission.

        Args:
            asset_id: The new media asset.
            clock: Source of ``updated_at``.

        Returns:
            The submission with the asset, or unchanged if it already has it.

        Raises:
            GuestSubmissionClosedError: If the report was already submitted.
            GuestMediaLimitError: If ``GUEST_MEDIA_MAX`` photos were granted.
        """
        if asset_id in self.media_ids:
            return AggregateChange[GuestSubmission](state=self)
        self._require_open()
        if len(self.media_ids) >= GUEST_MEDIA_MAX:
            raise GuestMediaLimitError.for_submission(self.id, GUEST_MEDIA_MAX)
        return AggregateChange[GuestSubmission](
            state=self._evolve(clock.now(), media_ids=(*self.media_ids, asset_id))
        )

    def record_report(
        self,
        report_id: EntityId,
        reference: str,
        content_fingerprint: str,
        *,
        clock: Clock,
    ) -> AggregateChange["GuestSubmission"]:
        """Close the submission with the report it carried.

        Args:
            report_id: The new guest report.
            reference: Its receipt code.
            content_fingerprint: SHA-256 of its content.
            clock: Source of ``submitted_at`` and ``updated_at``.

        Returns:
            The closed submission.

        Raises:
            GuestSubmissionClosedError: If a report was already submitted.
        """
        self._require_open()
        now = clock.now()
        return AggregateChange[GuestSubmission](
            state=self._evolve(
                now,
                report_id=report_id,
                reference=reference,
                content_fingerprint=content_fingerprint,
                submitted_at=now,
            )
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
