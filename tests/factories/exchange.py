"""Factories for the ``exchange`` domain: export and import jobs and their values.

``ExportJobTestFactory`` and ``ImportJobTestFactory`` are suffixed ``TestFactory``
because the domain already has ``ExportJobFactory`` and ``ImportJobFactory``. They
build queued jobs directly for arranging state; tests reach later states through
the aggregates' own methods so every invariant is exercised on the way. Digests are
hashes of a counter, never of a real file, and every text is synthetic.

Patterns: Factory.
"""

import hashlib
import itertools
from datetime import UTC, datetime

from polyfactory import Use

from tests.factories.base import FACTORY_IDS, YakhnamaModelFactory, random_instant
from yakhnama.modules.events.public import EventGeometry, EventPeriod
from yakhnama.modules.exchange.domain.backfill import (
    ImportedClaimDraft,
    ImportedEventDraft,
    ImportedSourceDraft,
)
from yakhnama.modules.exchange.domain.entities import ExportJob, ImportJob
from yakhnama.modules.exchange.domain.registry import DEFAULT_FORMAT_REGISTRY
from yakhnama.modules.exchange.domain.value_objects import (
    EXPORT_SCHEMA_VERSION,
    PROPOSED_DATASET_LICENCE,
    ArtifactRef,
    ExportDataset,
    ExportFilters,
    ExportFormat,
    ImportFormat,
    JobStatus,
    MetadataSidecar,
)
from yakhnama.modules.impacts.public import CountValue
from yakhnama.shared_kernel.value_objects import (
    Confidence,
    Coordinates,
    DatePrecision,
    DateWithPrecision,
)

_DIGEST_COUNTER = itertools.count(1)

SYNTHETIC_GENERATOR = "yakhnama/0.0.0-test"
"""Generator string of test sidecars; not a real release."""


def synthetic_sha256() -> str:
    """Return a distinct, synthetic SHA-256 digest on every call.

    Returns:
        64 lower-case hexadecimal digits.
    """
    return hashlib.sha256(f"test-artifact-{next(_DIGEST_COUNTER)}".encode()).hexdigest()


def artifact_ref(
    *,
    media_type: str = "application/geo+json",
    byte_size: int = 2048,
    object_key: str = "exports/test/events.geojson",
    sha256: str | None = None,
) -> ArtifactRef:
    """Return a synthetic stored artifact.

    Args:
        media_type: The file's media type.
        byte_size: Its size in bytes.
        object_key: Its storage key.
        sha256: Its digest, or ``None`` for a fresh synthetic one.

    Returns:
        The artifact reference.
    """
    return ArtifactRef(
        object_key=object_key,
        byte_size=byte_size,
        sha256=synthetic_sha256() if sha256 is None else sha256,
        media_type=media_type,
    )


def artifact_for(job: ExportJob) -> ArtifactRef:
    """Return a synthetic artifact whose media type fits ``job``'s format.

    Args:
        job: The export job.

    Returns:
        The artifact reference.
    """
    descriptor = DEFAULT_FORMAT_REGISTRY.export_descriptor(job.format.value)
    return artifact_ref(
        media_type=descriptor.media_type,
        object_key=f"exports/{job.id}/{job.dataset.value}{descriptor.extension}",
    )


def sidecar_for(
    job: ExportJob,
    artifact: ArtifactRef,
    *,
    row_count: int = 3,
    generated_at: datetime | None = None,
) -> MetadataSidecar:
    """Return the sidecar that describes ``artifact`` for ``job``.

    Args:
        job: The export job.
        artifact: The stored file.
        row_count: Rows in the file.
        generated_at: When it was generated; the job's request time by default.

    Returns:
        A sidecar that satisfies the job's invariants.
    """
    return MetadataSidecar(
        licence=PROPOSED_DATASET_LICENCE,
        generated_at=job.requested_at if generated_at is None else generated_at,
        dataset=job.dataset,
        format=job.format,
        filters=job.filters,
        schema_version=EXPORT_SCHEMA_VERSION,
        citation=f"Synthetic test citation; {job.filters.to_citation_fragment()}",
        row_count=row_count,
        checksum=artifact.sha256,
        generator=SYNTHETIC_GENERATOR,
    )


class ExportJobTestFactory(YakhnamaModelFactory[ExportJob]):
    """Builds queued GeoJSON exports of events without filters, at version 1.

    Implements: Factory.
    """

    __model__ = ExportJob

    id = Use(FACTORY_IDS.new_id)
    requested_by = Use(FACTORY_IDS.new_id)
    dataset = ExportDataset.EVENTS
    format = ExportFormat.GEOJSON
    filters = ExportFilters()
    status = JobStatus.QUEUED
    artifact = None
    sidecar = None
    error_summary = None
    requested_at = Use(random_instant)
    started_at = None
    finished_at = None
    version = 1


class ImportJobTestFactory(YakhnamaModelFactory[ImportJob]):
    """Builds queued real (not dry-run) CSV imports at version 1.

    Implements: Factory.
    """

    __model__ = ImportJob

    id = Use(FACTORY_IDS.new_id)
    requested_by = Use(FACTORY_IDS.new_id)
    format = ImportFormat.CSV
    dry_run = False
    source_artifact = Use(
        lambda: artifact_ref(media_type="text/csv", object_key="imports/test/rows.csv")
    )
    status = JobStatus.QUEUED
    report = None
    error_summary = None
    requested_at = Use(random_instant)
    started_at = None
    finished_at = None
    version = 1


def imported_event_draft(**fields: object) -> ImportedEventDraft:
    """Return a valid synthetic backfill draft with one count claim.

    Args:
        **fields: Fields to override.

    Returns:
        The draft.
    """
    started = DateWithPrecision(
        value=datetime(2022, 7, 15, 6, tzinfo=UTC), precision=DatePrecision.DAY
    )
    claim = ImportedClaimDraft(
        metric_code="test_metric",
        value=CountValue(count=4),
        confidence=Confidence.MEDIUM,
        claimed_at=started,
    )
    defaults: dict[str, object] = {
        "title": "Synthetic test event",
        "hazard_type": "test_hazard",
        "period": EventPeriod(started_at=started),
        # A synthetic point, not a real place.
        "geometry": EventGeometry.from_coordinates(
            Coordinates(longitude=74.5, latitude=36.5)
        ),
        "source": ImportedSourceDraft(citation="Synthetic test source"),
        "claims": (claim,),
    }
    return ImportedEventDraft.model_validate(defaults | fields)
