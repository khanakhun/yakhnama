"""Command line of the reference-data seed: ``python -m yakhnama.seed``.

``main`` parses the arguments, loads the settings, configures the structured logger,
builds the container, runs ``SeedReferenceDataHandler`` once and disposes the engine.
The outcome is logged, never printed (``AGENTS.md`` §4): one ``seed_completed`` line
with the counts and ``data_version`` of every file, one ``seed_change_skipped``
warning per difference the loaders left for a human, or one ``seed_failed`` line
with the error code and details, without a stack trace or file content.

Exit codes: ``0`` on success, ``1`` on a ``YakhnamaError`` (a refused actor, an
invalid or missing reference file, a conflict), ``2`` on invalid arguments
(``argparse``'s own convention). Any other exception is a bug or an unreachable
database and propagates with its traceback.

``__main__.py`` only calls ``main``; everything testable lives here, where coverage
is measured.

Patterns: Composition Root.
"""

import argparse
import asyncio
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Final

import structlog
from pydantic import BaseModel, ConfigDict

from yakhnama.modules.geography.public import LoadReport as PlaceLoadReport
from yakhnama.modules.hazards.public import LoadReport as HazardTypeLoadReport
from yakhnama.modules.identity.public import Actor, Role
from yakhnama.modules.impacts.public import LoadReport as ImpactMetricLoadReport
from yakhnama.platform.container import Container, build_container, build_seed_handler
from yakhnama.platform.logging import configure_logging
from yakhnama.platform.settings import Settings, get_settings
from yakhnama.seed.application import SeedReferenceData, SeedReport
from yakhnama.shared_kernel.errors import YakhnamaError
from yakhnama.shared_kernel.ids import EntityId

EXIT_SUCCESS: Final = 0
"""Exit code of a seed run that completed, dry or not."""

EXIT_FAILURE: Final = 1
"""Exit code of a seed run stopped by a ``YakhnamaError``."""

PROGRAM_NAME: Final = "python -m yakhnama.seed"

type SeedHandler = Callable[[SeedReferenceData], Awaitable[SeedReport]]
"""The seed use case, usually ``SeedReferenceDataHandler``."""

type SeedHandlerBuilder = Callable[[Container], SeedHandler]
"""Builds the seed use case from the container, usually ``build_seed_handler``."""

type LoadReport = HazardTypeLoadReport | ImpactMetricLoadReport | PlaceLoadReport


class SeedArguments(BaseModel):
    """The parsed command line of the seed.

    Implements: API Schema (the command-line interface is the seed's only API).

    Attributes:
        is_dry_run: Compute every report but roll every load back.
        reference_dir: Directory to read instead of ``settings.reference_data_dir``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    is_dry_run: bool = False
    reference_dir: Path | None = None


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser of ``python -m yakhnama.seed``.

    Returns:
        A parser for ``[--dry-run] [--reference-dir PATH]``.
    """
    parser = argparse.ArgumentParser(
        prog=PROGRAM_NAME,
        description=(
            "Load the versioned reference data (hazard types, impact metrics, "
            "places) idempotently into the configured database."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="compute what would change, then roll every change back",
    )
    parser.add_argument(
        "--reference-dir",
        type=Path,
        metavar="PATH",
        help="directory of the reference YAML files (default: settings)",
    )
    return parser


def parse_arguments(argv: Sequence[str] | None = None) -> SeedArguments:
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
    return SeedArguments(
        is_dry_run=namespace.dry_run, reference_dir=namespace.reference_dir
    )


def apply_arguments(settings: Settings, arguments: SeedArguments) -> Settings:
    """Return ``settings`` with the command-line overrides applied.

    Args:
        settings: The settings loaded from the environment.
        arguments: The parsed command line.

    Returns:
        The same settings, or a copy with ``reference_data_dir`` replaced.
    """
    if arguments.reference_dir is None:
        return settings
    return settings.model_copy(update={"reference_data_dir": arguments.reference_dir})


def _file_summary(report: LoadReport) -> dict[str, object]:
    # A plain mapping for the structured logger only; it never leaves this module.
    return {
        "data_version": report.data_version,
        "created": len(report.created),
        "updated": len(report.updated),
        "unchanged": len(report.unchanged),
        "skipped": len(report.skipped_with_reason),
    }


def log_report(report: SeedReport) -> None:
    """Log what a seed run did: one summary line and one warning per skipped change.

    Args:
        report: The report of the run.
    """
    logger = structlog.get_logger(__name__)
    files: tuple[tuple[str, LoadReport], ...] = (
        ("hazard_types", report.hazard_types),
        ("impact_metrics", report.impact_metrics),
        ("places", report.places),
    )
    for file_key, file_report in files:
        for skipped in file_report.skipped_with_reason:
            logger.warning(
                "seed_change_skipped",
                file=file_key,
                code=skipped.code,
                reason=skipped.reason,
            )
    logger.info(
        "seed_completed",
        dry_run=report.dry_run,
        is_unchanged=report.is_unchanged,
        **{file_key: _file_summary(file_report) for file_key, file_report in files},
    )


async def run_seed(handler: SeedHandler, command: SeedReferenceData) -> int:
    """Run the seed once and log its outcome.

    Args:
        handler: The seed use case.
        command: Who seeds, and whether it is a dry run.

    Returns:
        ``EXIT_SUCCESS``, or ``EXIT_FAILURE`` if the handler raised a
        ``YakhnamaError``.
    """
    try:
        report = await handler(command)
    except YakhnamaError as error:
        # One line, no exc_info: the message and details of a YakhnamaError are
        # written to be safe to show, while a traceback would carry locals and
        # chained causes that are not.
        structlog.get_logger(__name__).error(
            "seed_failed",
            error_code=error.code,
            message=error.message,
            details=dict(error.details),
            dry_run=command.dry_run,
        )
        return EXIT_FAILURE
    log_report(report)
    return EXIT_SUCCESS


def resolve_actor_id(settings: Settings, container: Container) -> EntityId:
    """Return the system actor the seed acts as.

    Args:
        settings: Supplies ``seed_actor_id``.
        container: Supplies the ``IdGenerator`` for a generated actor.

    Returns:
        ``settings.seed_actor_id``, or a new UUIDv7 that is logged as
        ``seed_actor_generated`` so the run's changes can be traced to it.
    """
    if settings.seed_actor_id is not None:
        return settings.seed_actor_id
    actor_id = container.id_generator.new_id()
    structlog.get_logger(__name__).info("seed_actor_generated", actor_id=str(actor_id))
    return actor_id


def build_system_actor(actor_id: EntityId) -> Actor:
    """Return the synthetic system actor the seed runs as.

    It holds ``admin`` (which ``CanManageReferenceData`` requires) and ``citizen``,
    which every active user holds explicitly, and belongs to no organisation.

    Args:
        actor_id: The resolved system actor id, recorded on every change.

    Returns:
        The actor.
    """
    return Actor(
        user_id=actor_id,
        roles=frozenset({Role.CITIZEN, Role.ADMIN}),
        memberships=frozenset(),
    )


async def seed(
    settings: Settings,
    *,
    is_dry_run: bool,
    build_handler: SeedHandlerBuilder = build_seed_handler,
) -> int:
    """Build the container, run the seed once and dispose the engine.

    Args:
        settings: The settings to build the container from.
        is_dry_run: Roll every load back.
        build_handler: Wires the seed use case; tests pass a fake.

    Returns:
        The exit code of ``run_seed``.
    """
    container = build_container(settings)
    try:
        actor = build_system_actor(resolve_actor_id(settings, container))
        handler = build_handler(container)
        return await run_seed(
            handler, SeedReferenceData(actor=actor, dry_run=is_dry_run)
        )
    finally:
        await container.aclose()


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    build_handler: SeedHandlerBuilder = build_seed_handler,
) -> int:
    """Run ``python -m yakhnama.seed``.

    Args:
        argv: The arguments after the program name; ``None`` reads ``sys.argv``.
        settings: Settings to use; ``None`` loads them from the environment.
        build_handler: Wires the seed use case; tests pass a fake.

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
        seed(
            resolved_settings,
            is_dry_run=arguments.is_dry_run,
            build_handler=build_handler,
        )
    )
