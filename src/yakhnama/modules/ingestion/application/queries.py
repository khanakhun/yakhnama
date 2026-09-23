"""Read requests accepted by the ingestion query service, and the cursor rules.

Every listing uses keyset pagination (``shared_kernel.pagination``). The orders and
cursor contents are fixed here so the SQL adapter and the in-memory Fake page the
same way:

- datasets: ``code`` ascending; ``sort_key`` is the code, ``last_id`` the id;
- runs: ``created_at`` descending, then id descending; ``sort_key`` is
  ``created_at`` in ISO 8601;
- observations: ``(observed_at, site_ref, dataset_version_id)`` ascending;
  ``sort_key`` is ``"<observed_at ISO 8601>|<site_ref>"`` and ``last_id`` the
  dataset version id (``observation_cursor`` and ``decode_observation_position``);
- raster assets: acquisition start descending, then id descending; ``sort_key`` is
  the acquisition start in ISO 8601.

Time windows are half-open, ``from <= t < to`` (**proposed**), so consecutive
windows never return an observation twice.

Patterns: Query, Value Object.
"""

from datetime import datetime
from typing import Final, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from yakhnama.modules.ingestion.application.dto import ObservationRecord
from yakhnama.modules.ingestion.domain.value_objects import (
    SITE_REF_MAX_LENGTH,
    DatasetCode,
    DatasetStatus,
    VariableCode,
    is_known_variable,
)
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import CursorPayload, PageRequest, encode_cursor
from yakhnama.shared_kernel.value_objects import BoundingBox

OBSERVATION_CURSOR_SEPARATOR: Final = "|"
"""Separates the instant from the site in an observation cursor's ``sort_key``; an
ISO 8601 instant never contains it, so the first occurrence is the boundary."""


def _require_window(start: datetime | None, end: datetime | None) -> None:
    if start is not None and end is not None and not start < end:
        message = "the time window must end after it starts"
        raise ValueError(message)


class ListDatasets(BaseModel):
    """Ask for one page of the dataset catalog, ordered by code.

    Implements: Query.

    Attributes:
        status: Only datasets with this status, or every dataset if ``None``.
        page: Page size and cursor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: DatasetStatus | None = None
    page: PageRequest = PageRequest()


class GetDataset(BaseModel):
    """Ask for one dataset by id or by code; exactly one is given.

    Implements: Query.

    Attributes:
        dataset_id: The dataset's id.
        code: The dataset's catalog code.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_id: EntityId | None = None
    code: DatasetCode | None = None

    @model_validator(mode="after")
    def _require_exactly_one(self) -> Self:
        if (self.dataset_id is None) == (self.code is None):
            message = "give exactly one of dataset_id and code"
            raise ValueError(message)
        return self


class ListRuns(BaseModel):
    """Ask for one page of a dataset's ingestion runs, newest first.

    Implements: Query.

    Attributes:
        dataset_id: The dataset.
        page: Page size and cursor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_id: EntityId
    page: PageRequest = PageRequest()


class GetRun(BaseModel):
    """Ask for one ingestion run with its report.

    Implements: Query.

    Attributes:
        run_id: The run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: EntityId


class QueryObservations(BaseModel):
    """Ask for one page of one variable's time series from one dataset.

    Implements: Query.

    Attributes:
        dataset_id: The dataset; every version of it unless ``dataset_version_id``
            is given, so a value re-published in a later version appears once per
            version (each record names its version).
        variable: A registered variable code.
        observed_from: Earliest instant, inclusive.
        observed_to: Latest instant, exclusive; after ``observed_from``.
        site_ref: Only this site (``station:<code>`` or ``grid_cell:<id>``).
        dataset_version_id: Only this version of the dataset.
        page: Page size and cursor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_id: EntityId
    variable: VariableCode
    observed_from: AwareDatetime
    observed_to: AwareDatetime
    site_ref: str | None = Field(
        default=None, min_length=1, max_length=SITE_REF_MAX_LENGTH
    )
    dataset_version_id: EntityId | None = None
    page: PageRequest = PageRequest()

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not is_known_variable(self.variable):
            message = f"unknown variable {self.variable!r}"
            raise ValueError(message)
        _require_window(self.observed_from, self.observed_to)
        return self


class ListRasterAssets(BaseModel):
    """Ask for one page of catalogued rasters, newest acquisition first.

    Implements: Query.

    Attributes:
        bbox: Only rasters whose footprint intersects this box.
        acquired_from: Only rasters acquired at or after this instant.
        acquired_to: Only rasters acquired before this instant.
        dataset_id: Only rasters of this dataset.
        page: Page size and cursor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    bbox: BoundingBox | None = None
    acquired_from: AwareDatetime | None = None
    acquired_to: AwareDatetime | None = None
    dataset_id: EntityId | None = None
    page: PageRequest = PageRequest()

    @model_validator(mode="after")
    def _check_window(self) -> Self:
        _require_window(self.acquired_from, self.acquired_to)
        return self


class ObservationPosition(BaseModel):
    """Where an observation page ended: the last record's sort key.

    Implements: Value Object.

    Attributes:
        observed_at: The last record's instant, UTC.
        site_ref: The last record's site.
        dataset_version_id: The last record's version, the final tie-breaker.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    observed_at: AwareDatetime
    site_ref: str = Field(min_length=1, max_length=SITE_REF_MAX_LENGTH)
    dataset_version_id: EntityId

    def as_tuple(self) -> tuple[datetime, str, EntityId]:
        """Return the position as the tuple observations are ordered by.

        Returns:
            ``(observed_at, site_ref, dataset_version_id)``.
        """
        return self.observed_at, self.site_ref, self.dataset_version_id


def observation_position(record: ObservationRecord) -> ObservationPosition:
    """Return the sort position of ``record``.

    Args:
        record: An observation of a page.

    Returns:
        Its position in the observation order.
    """
    return ObservationPosition(
        observed_at=record.observed_at.value,
        site_ref=record.site_ref,
        dataset_version_id=record.dataset_version_id,
    )


def observation_cursor(record: ObservationRecord) -> str:
    """Return the cursor continuing after ``record``.

    Args:
        record: The last observation of a page.

    Returns:
        The opaque token.
    """
    sort_key = (
        f"{record.observed_at.value.isoformat()}"
        f"{OBSERVATION_CURSOR_SEPARATOR}{record.site_ref}"
    )
    return encode_cursor(
        CursorPayload(sort_key=sort_key, last_id=record.dataset_version_id)
    )


def decode_observation_position(payload: CursorPayload) -> ObservationPosition:
    """Read the position an observation cursor carries.

    Args:
        payload: A decoded cursor, untrusted client input.

    Returns:
        The position to continue after.

    Raises:
        ValidationError: If the sort key is not ``"<instant>|<site_ref>"`` with an
            aware ISO 8601 instant.
    """
    instant, separator, site_ref = payload.sort_key.partition(
        OBSERVATION_CURSOR_SEPARATOR
    )
    try:
        observed_at = datetime.fromisoformat(instant)
        if not separator or observed_at.tzinfo is None:
            message = "the cursor's instant is naive or has no site"
            raise ValueError(message)
        return ObservationPosition(
            observed_at=observed_at,
            site_ref=site_ref,
            dataset_version_id=payload.last_id,
        )
    except ValueError as error:
        # ``pydantic.ValidationError`` is a ``ValueError`` too. The key is not
        # echoed back: it is client input.
        message = "the pagination cursor is invalid"
        raise ValidationError(message, details={"field": "cursor"}) from error
