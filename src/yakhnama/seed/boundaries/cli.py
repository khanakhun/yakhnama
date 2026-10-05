"""Command line of the district boundary load: ``python -m yakhnama.seed.boundaries``.

``main`` parses the arguments, loads the settings, configures the structured
logger, reads the boundary source file (``boundary_source_file``, by default
``data/boundaries/cod_ab_pak_gb_districts.yaml``), builds the container, runs
``LoadDistrictBoundariesHandler`` once and disposes the engine. The handler
downloads the pinned archive into ``boundary_cache_dir`` the first time, verifies
its SHA-256 on every run, links the districts through the committed table, stores
the linked places' footprints and publishes the shared edges (ADR 0021).

The outcome is logged, never printed (``AGENTS.md`` §4): one ``boundary_mismatch``
warning per district the table, the file and the gazetteer disagree on, a
``boundary_centroid_kept`` warning per place whose centroid came from another
source and was left alone, a ``boundary_payload_over_budget`` warning when the
public payload exceeds ``PAYLOAD_BUDGET_BYTES``, then one ``boundaries_loaded`` line
with the counts, or one ``boundaries_failed`` line with the error code and details.

Exit codes: ``0`` on success (mismatches are reported, not failures), ``1`` on a
``YakhnamaError`` (a refused actor, an invalid source file, a failed download or
checksum), ``2`` on invalid arguments.

Patterns: Composition Root.
"""

import argparse
import asyncio
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Final

import structlog
from pydantic import BaseModel, ConfigDict

from yakhnama.modules.geography.public import (
    BoundaryLoadReport,
    DistrictBoundarySource,
    LoadDistrictBoundaries,
)
from yakhnama.platform.container import (
    Container,
    build_container,
    build_district_boundary_handler,
)
from yakhnama.platform.logging import configure_logging
from yakhnama.platform.settings import Settings, get_settings
from yakhnama.seed.cli import (
    EXIT_FAILURE,
    EXIT_SUCCESS,
    build_system_actor,
    resolve_actor_id,
)
from yakhnama.seed.infrastructure import YamlReferenceFileReader
from yakhnama.shared_kernel.errors import YakhnamaError

PROGRAM_NAME: Final = "python -m yakhnama.seed.boundaries"

PAYLOAD_BUDGET_BYTES: Final = 150_000
"""Size the public edge payload should stay under for slow connections (Phase 2
plan, task B2); exceeding it is a warning, not a failure."""

type BoundaryHandler = Callable[[LoadDistrictBoundaries], Awaitable[BoundaryLoadReport]]
"""The load use case, usually ``LoadDistrictBoundariesHandler``."""

type BoundaryHandlerBuilder = Callable[[Container], BoundaryHandler]
"""Builds the load use case from the container."""


class BoundaryArguments(BaseModel):
    """The parsed command line of the boundary load.

    Implements: API Schema (the command-line interface is the load's only API).

    Attributes:
        is_dry_run: Compute and report everything but roll every change back.
        source_file: Boundary source file to read instead of
            ``settings.boundary_source_file``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    is_dry_run: bool = False
    source_file: Path | None = None


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser of ``python -m yakhnama.seed.boundaries``.

    Returns:
        A parser for ``[--dry-run] [--source-file PATH]``.
    """
    parser = argparse.ArgumentParser(
        prog=PROGRAM_NAME,
        description=(
            "Download the pinned district boundary archive (once), link its "
            "districts to the gazetteer and publish the edges they share."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="compute and report everything, then roll every change back",
    )
    parser.add_argument(
        "--source-file",
        type=Path,
        metavar="PATH",
        help="the boundary source YAML file (default: settings)",
    )
    return parser


def parse_arguments(argv: Sequence[str] | None = None) -> BoundaryArguments:
    """Parse the command line.

    Args:
        argv: The arguments after the program name; ``None`` reads ``sys.argv``.

    Returns:
        The validated arguments.

    Raises:
        SystemExit: With code 2 on invalid arguments, or 0 after ``--help``.
    """
    namespace = build_parser().parse_args(argv)
    # Namespace attributes are untyped; they are validated into the model at once.
    return BoundaryArguments(
        is_dry_run=namespace.dry_run, source_file=namespace.source_file
    )


def apply_arguments(settings: Settings, arguments: BoundaryArguments) -> Settings:
    """Return ``settings`` with the command-line overrides applied.

    Args:
        settings: The settings loaded from the environment.
        arguments: The parsed command line.

    Returns:
        The same settings, or a copy with ``boundary_source_file`` replaced.
    """
    if arguments.source_file is None:
        return settings
    return settings.model_copy(update={"boundary_source_file": arguments.source_file})


def log_report(report: BoundaryLoadReport) -> None:
    """Log what a load did: one warning per mismatch, then one summary line.

    Args:
        report: The report of the run.
    """
    logger = structlog.get_logger(__name__)
    match = report.match
    # District names are logged under "place_name", the one name key the logger's
    # personal-data filter allows (they are public reference data); the name a
    # difference was compared with is in the link table or the gazetteer.
    for link in match.unlinked:
        logger.warning(
            "boundary_mismatch",
            kind="unlinked",
            source_code=link.source_code,
            place_name=link.source_name,
        )
    for code in match.not_in_link_table:
        logger.warning("boundary_mismatch", kind="not_in_link_table", source_code=code)
    for code in match.not_in_source:
        logger.warning("boundary_mismatch", kind="not_in_source", source_code=code)
    for code in match.missing_places:
        logger.warning("boundary_mismatch", kind="missing_place", place_code=code)
    for code in match.places_without_boundary:
        logger.warning(
            "boundary_mismatch", kind="place_without_boundary", place_code=code
        )
    for difference in match.name_differences:
        logger.warning(
            "boundary_mismatch",
            kind="name_differs",
            compared_with=difference.compared_with,
            source_code=difference.source_code,
            place_code=match.place_code_of(difference.source_code),
            place_name=difference.source_name,
        )
    for code in report.centroid_kept:
        logger.warning("boundary_centroid_kept", place_code=code)
    if report.payload_bytes > PAYLOAD_BUDGET_BYTES:
        logger.warning(
            "boundary_payload_over_budget",
            payload_bytes=report.payload_bytes,
            budget_bytes=PAYLOAD_BUDGET_BYTES,
        )
    logger.info(
        "boundaries_loaded",
        dry_run=report.dry_run,
        dataset_version=report.dataset_version,
        sha256=report.sha256,
        region_code=report.region_code,
        districts_in_source=report.districts_in_source,
        districts_linked=len(match.linked),
        has_mismatches=match.has_mismatches,
        is_coverage_valid=report.is_coverage_valid,
        edges=report.edges,
        edges_with_unlinked_district=report.edges_with_unlinked_district,
        dropped_parts=report.dropped_parts,
        positions=report.positions,
        payload_bytes=report.payload_bytes,
        footprints_updated=len(report.geometry_updated),
        footprints_unchanged=len(report.geometry_unchanged),
        centroids_updated=len(report.centroid_updated),
        centroids_unchanged=len(report.centroid_unchanged),
        centroids_kept=len(report.centroid_kept),
        edge_set_id=str(report.edge_set_id),
        is_edge_set_created=report.is_edge_set_created,
    )


def _log_failure(error: YakhnamaError, *, is_dry_run: bool) -> None:
    # One line, no exc_info: a YakhnamaError's message and details are written to
    # be safe to show; a traceback would carry locals and chained causes.
    structlog.get_logger(__name__).error(
        "boundaries_failed",
        error_code=error.code,
        message=error.message,
        details=dict(error.details),
        dry_run=is_dry_run,
    )


async def run_load(handler: BoundaryHandler, command: LoadDistrictBoundaries) -> int:
    """Run the load once and log its outcome.

    Args:
        handler: The load use case.
        command: The source, the actor and whether it is a dry run.

    Returns:
        ``EXIT_SUCCESS``, or ``EXIT_FAILURE`` if the handler raised a
        ``YakhnamaError``.
    """
    try:
        report = await handler(command)
    except YakhnamaError as error:
        _log_failure(error, is_dry_run=command.dry_run)
        return EXIT_FAILURE
    log_report(report)
    return EXIT_SUCCESS


def read_source(settings: Settings) -> DistrictBoundarySource:
    """Read the committed boundary source file.

    Args:
        settings: Supplies ``boundary_source_file``.

    Returns:
        The validated source.

    Raises:
        ValidationError: If the file is missing or invalid.
    """
    path = settings.boundary_source_file
    return YamlReferenceFileReader(path.parent).read_district_boundary_source(path.name)


async def load(
    settings: Settings,
    *,
    is_dry_run: bool,
    build_handler: BoundaryHandlerBuilder = build_district_boundary_handler,
) -> int:
    """Read the source, build the container, run the load once and dispose it.

    Args:
        settings: The settings to build the container from.
        is_dry_run: Roll every change back.
        build_handler: Wires the load use case; tests pass a fake.

    Returns:
        The exit code of ``run_load``, or ``EXIT_FAILURE`` if the source file is
        invalid.
    """
    try:
        source = read_source(settings)
    except YakhnamaError as error:
        _log_failure(error, is_dry_run=is_dry_run)
        return EXIT_FAILURE
    container = build_container(settings)
    try:
        actor = build_system_actor(resolve_actor_id(settings, container))
        return await run_load(
            build_handler(container),
            LoadDistrictBoundaries(source=source, actor=actor, dry_run=is_dry_run),
        )
    finally:
        await container.aclose()


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    build_handler: BoundaryHandlerBuilder = build_district_boundary_handler,
) -> int:
    """Run ``python -m yakhnama.seed.boundaries``.

    Args:
        argv: The arguments after the program name; ``None`` reads ``sys.argv``.
        settings: Settings to use; ``None`` loads them from the environment.
        build_handler: Wires the load use case; tests pass a fake.

    Returns:
        The process exit code.

    Raises:
        SystemExit: With code 2 on invalid arguments.
        pydantic.ValidationError: If the environment settings are invalid.
    """
    arguments = parse_arguments(argv)
    resolved_settings = apply_arguments(
        settings if settings is not None else get_settings(), arguments
    )
    configure_logging(resolved_settings)
    return asyncio.run(
        load(
            resolved_settings,
            is_dry_run=arguments.is_dry_run,
            build_handler=build_handler,
        )
    )
