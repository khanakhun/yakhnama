"""Format strategies of the exchange module: exporters, importers and their registry.

An ``Exporter`` writes the rows of one dataset in one file format; an ``Importer``
tokenises one file format into flat rows of the backfill column contract. Both are
Strategies looked up by format code in the ``FormatAdapterRegistry``, which the
composition root builds once: adding a format is one new class in infrastructure and
one registration line, and never touches another format, this module or the handlers.

Rows handed to an exporter are the frozen DTOs below, with explicit public fields
only, so an exporter cannot write a field the dataset does not publish:

- ``EventExportRow``: an event as the events facade shows it to the requesting actor
  (for a non-moderator only published and verified events);
- ``ClaimExportRow``: an impact claim without who recorded it, its note or its
  retraction reason;
- ``ReportExportRow``: a report with its position rounded by
  ``PublicCoordinatePolicy``, without the GPS accuracy, the reporter, the
  description or media ids (reports are exported to moderators only, **proposed**).

Patterns: Strategy, Registry, DTO.
"""

from collections.abc import AsyncIterator, Mapping
from typing import ClassVar, Final, Protocol, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from yakhnama.modules.events.public import EventGeometry, EventStatus
from yakhnama.modules.exchange.domain.errors import UnsupportedFormatError
from yakhnama.modules.exchange.domain.registry import (
    DEFAULT_FORMAT_REGISTRY,
    FormatDescriptor,
    FormatRegistry,
)
from yakhnama.modules.exchange.domain.value_objects import (
    ExportDataset,
    ExportFormat,
    ImportFormat,
)
from yakhnama.modules.geography.public import PlaceCode
from yakhnama.modules.hazards.public import HazardCode
from yakhnama.modules.impacts.public import (
    ClaimScope,
    ClaimStatus,
    ClaimValue,
    MetricCode,
    SourceTypeName,
)
from yakhnama.modules.reports.public import ReportStatus
from yakhnama.shared_kernel.errors import ConflictError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.value_objects import (
    Confidence,
    Coordinates,
    DateWithPrecision,
)

# The bounds mirror the events DTO fields the rows are copied from (EventTitle,
# EventSummary, the verification state name); the events facade does not export the
# text aliases, so the numbers are repeated here.
EXPORT_TITLE_MAX_LENGTH: Final = 200
EXPORT_SUMMARY_MAX_LENGTH: Final = 2000
EXPORT_STATE_MAX_LENGTH: Final = 32
EXPORT_LIST_MAX_LENGTH: Final = 1000
"""Technical cap on the places or sources one exported row lists."""


# --------------------------------------------------------------------------- #
# Byte streams                                                                #
# --------------------------------------------------------------------------- #


class BinarySink(Protocol):
    """Where an exporter writes the bytes of one file, in order.

    Implements: Adapter (port side).
    """

    async def write(self, data: bytes) -> None:
        """Append ``data`` to the file.

        Args:
            data: The next bytes.

        Raises:
            ValidationError: If the file would exceed the size the caller allows.
        """
        ...


class BinarySource(Protocol):
    """Where an importer reads the bytes of one file, in order.

    Implements: Adapter (port side).
    """

    async def read(self, size: int) -> bytes:
        """Return up to ``size`` further bytes.

        Args:
            size: The most bytes to return, at least 1.

        Returns:
            The next bytes; empty once the file is exhausted.

        Raises:
            ValidationError: If the file is larger than the caller allows.
        """
        ...


# --------------------------------------------------------------------------- #
# Export rows                                                                 #
# --------------------------------------------------------------------------- #


class EventExportRow(BaseModel):
    """One event in an ``events`` export.

    Implements: DTO.

    Attributes:
        dataset: Always ``events``; tells the exporter which layout to write.
        event_id: The event.
        hazard_code: Its hazard type code.
        title: Its short name.
        summary: The moderators' summary, if any.
        started_at: When it started, with precision.
        ended_at: When it ended, with precision, if known.
        centroid: Its representative point as the events facade publishes it
            (the geometry's centroid, or the mean of rounded report points).
        geometry: Its mapped point or extent, if any.
        place_codes: Codes of the gazetteer places it concerns, sorted.
        source_ids: The sources it cites.
        status: Its editorial status.
        verification_state: The state of its verification case, if any.
        updated_at: When it last changed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset: ClassVar[ExportDataset] = ExportDataset.EVENTS

    event_id: EntityId
    hazard_code: HazardCode
    title: str = Field(min_length=1, max_length=EXPORT_TITLE_MAX_LENGTH)
    summary: str | None = Field(
        default=None, min_length=1, max_length=EXPORT_SUMMARY_MAX_LENGTH
    )
    started_at: DateWithPrecision
    ended_at: DateWithPrecision | None = None
    centroid: Coordinates | None = None
    geometry: EventGeometry | None = None
    place_codes: tuple[PlaceCode, ...] = Field(
        default=(), max_length=EXPORT_LIST_MAX_LENGTH
    )
    source_ids: tuple[EntityId, ...] = Field(
        default=(), max_length=EXPORT_LIST_MAX_LENGTH
    )
    status: EventStatus
    verification_state: str | None = Field(
        default=None, min_length=1, max_length=EXPORT_STATE_MAX_LENGTH
    )
    updated_at: AwareDatetime


class ClaimExportRow(BaseModel):
    """One impact claim in a ``claims`` export.

    Implements: DTO.

    Attributes:
        dataset: Always ``claims``.
        claim_id: The claim.
        event_id: The event it is about.
        metric_code: The impact metric.
        value: The figure with its unit or currency.
        confidence: How far the source's figure can be trusted.
        source_id: The source the figure comes from.
        source_type: That source's type.
        claimed_at: When the source made the claim, with precision.
        scope: The part of the event it covers.
        status: ``active`` or ``retracted``.
        supersedes_id: The claim it corrects, if any.
        created_at: When it was recorded in Yakhnama, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset: ClassVar[ExportDataset] = ExportDataset.CLAIMS

    claim_id: EntityId
    event_id: EntityId
    metric_code: MetricCode
    value: ClaimValue
    confidence: Confidence
    source_id: EntityId
    source_type: SourceTypeName
    claimed_at: DateWithPrecision
    scope: ClaimScope
    status: ClaimStatus
    supersedes_id: EntityId | None = None
    created_at: AwareDatetime


class ReportExportRow(BaseModel):
    """One report in a ``reports`` export, with its position rounded.

    Implements: DTO.

    Attributes:
        dataset: Always ``reports``.
        report_id: The report.
        status: Its lifecycle status.
        revision: Its position in the revision chain, from 1.
        observed_at: When it was observed, with precision.
        coordinates: Its position rounded by ``PublicCoordinatePolicy``; the export
            handler rounds it again, so an adapter mistake cannot leak it.
        hazard_code: The reporter's hazard guess, if any.
        place_hint: The place the reporter picked, if any.
        media_count: How many media assets are attached.
        submitted_at: When it was submitted, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset: ClassVar[ExportDataset] = ExportDataset.REPORTS

    report_id: EntityId
    status: ReportStatus
    revision: int = Field(ge=1)
    observed_at: DateWithPrecision
    coordinates: Coordinates
    hazard_code: HazardCode | None = None
    place_hint: PlaceCode | None = None
    media_count: int = Field(default=0, ge=0)
    submitted_at: AwareDatetime | None = None


type ExportRow = EventExportRow | ClaimExportRow | ReportExportRow
"""Any row an exporter may receive; ``row.dataset`` tells which layout applies."""


# --------------------------------------------------------------------------- #
# Strategies                                                                  #
# --------------------------------------------------------------------------- #


class Exporter(Protocol):
    """Writes the rows of a dataset as one file of one format.

    The handler around it counts the rows it pulls, measures and hashes the bytes
    it writes and builds the metadata sidecar; an exporter only serialises, in a
    deterministic column or property order, UTF-8 where the format is text.

    Implements: Strategy.
    """

    @property
    def format(self) -> ExportFormat:
        """Return the format code this exporter is registered under."""
        ...

    @property
    def media_type(self) -> str:
        """Return the media type of the files it writes.

        It must equal the format's descriptor in the domain registry; the
        ``FormatAdapterRegistry`` refuses an exporter that disagrees.
        """
        ...

    async def write(
        self,
        dataset: ExportDataset,
        rows: AsyncIterator[ExportRow],
        sink: BinarySink,
    ) -> None:
        """Serialise every row of ``rows`` into ``sink``.

        Args:
            dataset: Which layout to write, also for an empty export (a CSV header,
                an empty FeatureCollection).
            rows: The rows, already filtered and made public; every row's
                ``dataset`` equals ``dataset``. Consume them all.
            sink: Where the bytes go.

        Raises:
            ValidationError: If the sink refuses more bytes.
        """
        ...


class Importer(Protocol):
    """Tokenises one file format into flat rows of the backfill column contract.

    ``header`` is called first, then ``read`` on the same source, which continues
    where ``header`` stopped (an importer may buffer what it needs). Importers never
    validate cell content and never write: ``ImportedEventDraft.from_flat_row`` and
    the import handler do.

    Implements: Strategy.
    """

    @property
    def format(self) -> ImportFormat:
        """Return the format code this importer is registered under."""
        ...

    async def header(self, source: BinarySource) -> tuple[str, ...]:
        """Read the column names of the file.

        Args:
            source: The file, not read yet.

        Returns:
            The column names in file order (for GeoJSON, every property name used
            plus ``geometry``), checked by the handler with
            ``require_backfill_header``.

        Raises:
            ImportContractError: If the file has no readable header.
        """
        ...

    def read(
        self, source: BinarySource
    ) -> AsyncIterator[tuple[int, Mapping[str, str]]]:
        """Yield every data row after the header.

        Args:
            source: The same source ``header`` read from.

        Yields:
            ``(row_number, cells)``: the 1-based data row number and the row's
            column-to-text mapping (absent columns may be left out).

        Raises:
            ImportContractError: If the file's structure breaks the format.
        """
        ...


# --------------------------------------------------------------------------- #
# Registry                                                                    #
# --------------------------------------------------------------------------- #


class FormatAdapterRegistry:
    """The exporter and importer strategy for every supported format code.

    Built once by the composition root with ``register_exporter`` and
    ``register_importer``; the handlers only look strategies up. Only formats the
    domain ``FormatRegistry`` knows can be registered, so a file's media type and
    extension always come from the domain descriptor.

    Implements: Registry.

    Attributes:
        formats: The domain catalogue of formats.
    """

    def __init__(self, formats: FormatRegistry = DEFAULT_FORMAT_REGISTRY) -> None:
        """Create an empty registry.

        Args:
            formats: The domain catalogue; the default knows every format of this
                version.
        """
        self.formats = formats
        self._exporters: dict[ExportFormat, Exporter] = {}
        self._importers: dict[ImportFormat, Importer] = {}

    def register_exporter(self, exporter: Exporter) -> Self:
        """Add the exporter of one format.

        Args:
            exporter: The strategy.

        Returns:
            The registry, so registrations can be chained.

        Raises:
            ConflictError: If an exporter is registered for that format already, or
                its media type differs from the format's descriptor.
            UnsupportedFormatError: If the domain catalogue lacks the format.
        """
        descriptor = self.formats.export_descriptor(exporter.format.value)
        if exporter.format in self._exporters:
            message = "an exporter is already registered for this format"
            raise ConflictError(message, details={"format": exporter.format.value})
        if exporter.media_type != descriptor.media_type:
            message = "the exporter's media type differs from the format's"
            raise ConflictError(message, details={"format": exporter.format.value})
        self._exporters[exporter.format] = exporter
        return self

    def register_importer(self, importer: Importer) -> Self:
        """Add the importer of one format.

        Args:
            importer: The strategy.

        Returns:
            The registry, so registrations can be chained.

        Raises:
            ConflictError: If an importer is registered for that format already.
            UnsupportedFormatError: If the domain catalogue lacks the format.
        """
        self.formats.import_descriptor(importer.format.value)
        if importer.format in self._importers:
            message = "an importer is already registered for this format"
            raise ConflictError(message, details={"format": importer.format.value})
        self._importers[importer.format] = importer
        return self

    def exporter(self, format_code: ExportFormat) -> Exporter:
        """Return the exporter of a format.

        Args:
            format_code: The format.

        Returns:
            Its strategy.

        Raises:
            UnsupportedFormatError: If no exporter is registered for it.
        """
        found = self._exporters.get(format_code)
        if found is None:
            raise UnsupportedFormatError.for_code(format_code.value, "export")
        return found

    def importer(self, format_code: ImportFormat) -> Importer:
        """Return the importer of a format.

        Args:
            format_code: The format.

        Returns:
            Its strategy.

        Raises:
            UnsupportedFormatError: If no importer is registered for it.
        """
        found = self._importers.get(format_code)
        if found is None:
            raise UnsupportedFormatError.for_code(format_code.value, "import")
        return found

    def export_descriptor(self, format_code: ExportFormat) -> FormatDescriptor:
        """Return how files of an export format are labelled.

        Args:
            format_code: The format.

        Returns:
            Its descriptor (media type, extension).

        Raises:
            UnsupportedFormatError: If no exporter is registered for it.
        """
        self.exporter(format_code)
        return self.formats.export_descriptor(format_code.value)

    def import_descriptor(self, format_code: ImportFormat) -> FormatDescriptor:
        """Return how files of an import format are labelled.

        Args:
            format_code: The format.

        Returns:
            Its descriptor (media type, extension).

        Raises:
            UnsupportedFormatError: If no importer is registered for it.
        """
        self.importer(format_code)
        return self.formats.import_descriptor(format_code.value)
