"""Validation models for the dataset catalog reference file.

``data/reference/datasets.yaml`` is parsed with ``yaml.safe_load`` outside the domain
and validated here immediately, so the untyped structure never travels further. Every
entry carries its licence: the file cannot even describe a dataset without one, which
is the first line of "never ingest without a licence". Entries are never removed; a
dataset that is no longer ingested keeps its entry with ``status`` ``deprecated`` or
``retired``.

Patterns: Value Object.
"""

from collections import Counter
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from yakhnama.modules.ingestion.domain.value_objects import (
    DatasetCode,
    DatasetDescription,
    DatasetDetails,
    DatasetLicence,
    DatasetStatus,
    DatasetTitle,
    DatasetUrl,
    Notes,
    Publisher,
    SpatialCoverage,
    TemporalCoverage,
    UpdateFrequency,
)
from yakhnama.shared_kernel.text import safe_text

REFERENCE_SCHEMA_VERSION: Final = 1
MAX_REFERENCE_DATASETS: Final = 1000
ENTRY_SOURCE_MAX_LENGTH: Final = 500

EntrySource = Annotated[str, *safe_text(ENTRY_SOURCE_MAX_LENGTH)]
"""Where the entry's facts come from: a citation of the publisher's documentation,
``proposed`` until one exists, or ``synthetic fixture`` for test data."""


class DatasetReferenceEntry(BaseModel):
    """One dataset as written in the reference file.

    Implements: Value Object.

    Attributes:
        code: Catalog code; never reused.
        title: Human-readable title.
        publisher: Who publishes the data.
        licence: The publisher's terms and attribution; required.
        update_frequency: How often the publisher releases data.
        status: The dataset's catalog status.
        description: Long-form description, if any.
        homepage_url: The dataset's page, if online.
        spatial_coverage: The covered area, if known.
        temporal_coverage: The covered period, if known.
        is_fixture: ``True`` for synthetic test data that must never be published
            as if it were real.
        source: Where the entry's facts come from.
        notes: Remarks for reviewers, for example an open question.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: DatasetCode
    title: DatasetTitle
    publisher: Publisher
    licence: DatasetLicence
    update_frequency: UpdateFrequency
    status: DatasetStatus = DatasetStatus.ACTIVE
    description: DatasetDescription | None = None
    homepage_url: DatasetUrl | None = None
    spatial_coverage: SpatialCoverage | None = None
    temporal_coverage: TemporalCoverage | None = None
    is_fixture: bool = False
    source: EntrySource
    notes: Notes | None = None

    def to_details(self) -> DatasetDetails:
        """Return the registration input for ``DatasetFactory.register``.

        ``status`` is not part of the details: a seed registers the dataset and then
        applies ``deprecate`` or ``retire`` so the history records the move.

        Returns:
            The dataset details, licence included.
        """
        return DatasetDetails(
            code=self.code,
            title=self.title,
            publisher=self.publisher,
            licence=self.licence,
            update_frequency=self.update_frequency,
            description=self.description,
            homepage_url=self.homepage_url,
            spatial_coverage=self.spatial_coverage,
            temporal_coverage=self.temporal_coverage,
        )


class DatasetReferenceFile(BaseModel):
    """The whole ``datasets.yaml`` file.

    Implements: Value Object.

    Attributes:
        schema_version: Layout version of the file; only ``1`` exists.
        datasets: The entries, unique by code, at most 1000.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    datasets: tuple[DatasetReferenceEntry, ...] = Field(
        default=(), max_length=MAX_REFERENCE_DATASETS
    )

    @model_validator(mode="after")
    def _require_unique_codes(self) -> Self:
        duplicates = sorted(
            code
            for code, count in Counter(entry.code for entry in self.datasets).items()
            if count > 1
        )
        if duplicates:
            message = f"duplicate dataset codes: {duplicates}"
            raise ValueError(message)
        return self

    def find(self, code: str) -> DatasetReferenceEntry | None:
        """Return the entry with ``code``, if the file has one.

        Args:
            code: A dataset code.

        Returns:
            The entry, or ``None``.
        """
        return next((entry for entry in self.datasets if entry.code == code), None)
