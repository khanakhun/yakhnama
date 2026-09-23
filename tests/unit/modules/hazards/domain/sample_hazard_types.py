"""Deterministic clock, ids and sample hazard types for the hazards domain tests.

Patterns: Fake.
"""

from datetime import UTC, datetime, timedelta

from yakhnama.modules.hazards.domain.entities import HazardType
from yakhnama.modules.hazards.domain.value_objects import (
    HazardTypeStatus,
    IrdrAlignment,
    RetirementReason,
)
from yakhnama.shared_kernel.ids import Uuid7Generator
from yakhnama.shared_kernel.value_objects import LocalizedText

CREATED_AT = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
ALIGNMENT = IrdrAlignment(family="hydrological", main_event="Flood")


class SteppingClock:
    """``Clock`` that starts at ``CREATED_AT`` and advances one minute per call.

    Implements: Fake.
    """

    def __init__(self, start: datetime = CREATED_AT) -> None:
        """Create the clock.

        Args:
            start: The first instant returned.
        """
        self._next = start

    def now(self) -> datetime:
        """Return the next instant.

        Returns:
            A UTC instant one minute after the previous one.
        """
        current = self._next
        self._next = current + timedelta(minutes=1)
        return current


ids = Uuid7Generator(SteppingClock())


def labels(text: str = "Flood") -> LocalizedText:
    """Return English-only labels.

    Args:
        text: The English label.

    Returns:
        The labels.
    """
    return LocalizedText(texts={"en": text})


def hazard_type(
    code: str,
    parent_code: str | None = None,
    *,
    replaced_by: str | None = None,
    is_retired: bool = False,
) -> HazardType:
    """Build a valid hazard type at version 1.

    Args:
        code: The code.
        parent_code: The parent code, if any.
        replaced_by: Replacement code; implies ``is_retired``.
        is_retired: Whether the type is retired.

    Returns:
        The hazard type.
    """
    retired = is_retired or replaced_by is not None
    return HazardType(
        id=ids.new_id(),
        code=code,
        parent_code=parent_code,
        labels=labels(code),
        alignment=ALIGNMENT,
        status=HazardTypeStatus.RETIRED if retired else HazardTypeStatus.ACTIVE,
        retirement=(
            RetirementReason(text="merged", replaced_by=replaced_by)
            if retired
            else None
        ),
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
    )
