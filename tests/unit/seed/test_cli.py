"""Unit tests for ``yakhnama.seed.cli`` with a fake seed handler; no database I/O.

``build_container`` is real (it opens no connection), and the seed use case is
replaced through the ``build_handler`` seam, so the tests cover argument parsing,
actor resolution, logging and exit codes without PostGIS.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import pytest
from structlog.testing import capture_logs

from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.geography.public import LoadReport as PlaceLoadReport
from yakhnama.modules.hazards.public import LoadReport as HazardTypeLoadReport
from yakhnama.modules.hazards.public import SkippedChange
from yakhnama.modules.identity.public import CanManageReferenceData, Role
from yakhnama.modules.impacts.public import LoadReport as ImpactMetricLoadReport
from yakhnama.platform.container import Container, build_container
from yakhnama.platform.settings import Settings, get_settings
from yakhnama.seed.application import SeedReferenceData, SeedReport
from yakhnama.seed.cli import (
    EXIT_FAILURE,
    EXIT_SUCCESS,
    SeedArguments,
    apply_arguments,
    build_parser,
    build_system_actor,
    log_report,
    main,
    parse_arguments,
    resolve_actor_id,
    run_seed,
    seed,
)
from yakhnama.shared_kernel.errors import PermissionDeniedError, YakhnamaError
from yakhnama.shared_kernel.ids import is_uuid7

CONFIGURED_ACTOR: Final = SequentialIdGenerator(seed=7).new_id()
SYSTEM_ACTOR: Final = build_system_actor(CONFIGURED_ACTOR)
DATA_VERSION: Final = "2026-09-23"


def _report(*, is_dry_run: bool = False, has_skipped: bool = False) -> SeedReport:
    skipped = (
        (SkippedChange(code="flood", reason="parent differs; left for a human"),)
        if has_skipped
        else ()
    )
    return SeedReport(
        dry_run=is_dry_run,
        hazard_types=HazardTypeLoadReport(
            data_version=DATA_VERSION,
            dry_run=is_dry_run,
            created=("flood", "mass_movement"),
            updated=(),
            unchanged=(),
            skipped_with_reason=skipped,
        ),
        impact_metrics=ImpactMetricLoadReport(
            data_version=DATA_VERSION,
            dry_run=is_dry_run,
            created=(),
            updated=("deaths",),
            unchanged=(),
            skipped_with_reason=(),
        ),
        places=PlaceLoadReport(
            data_version=DATA_VERSION,
            dry_run=is_dry_run,
            created=(),
            updated=(),
            unchanged=("pk", "pk.gb"),
            skipped_with_reason=(),
        ),
    )


@dataclass
class FakeSeedHandler:
    """Records every command and returns a fixed report or raises a fixed error.

    Implements: Fake (of Command Handler).

    Attributes:
        error: Raised instead of returning a report, if set.
        commands: The commands received, in call order.
    """

    error: YakhnamaError | None = None
    commands: list[SeedReferenceData] = field(default_factory=list)

    async def __call__(self, command: SeedReferenceData) -> SeedReport:
        """Record ``command``, then raise ``error`` or return a report."""
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        return _report(is_dry_run=command.dry_run)


@dataclass
class FakeHandlerBuilder:
    """Stands in for ``build_seed_handler`` and records what it was given.

    Implements: Fake (of Composition Root).

    Attributes:
        handler: The handler handed out.
        containers: The containers received.
        pools: The engine pool of each container when the handler was built.
    """

    handler: FakeSeedHandler = field(default_factory=FakeSeedHandler)
    containers: list[Container] = field(default_factory=list)
    pools: list[object] = field(default_factory=list)

    def __call__(self, container: Container) -> FakeSeedHandler:
        """Record the container and the engine's pool, and return ``handler``."""
        self.containers.append(container)
        self.pools.append(container.engine.sync_engine.pool)
        return self.handler


@pytest.fixture
def builder() -> FakeHandlerBuilder:
    return FakeHandlerBuilder()


def test_parse_arguments_without_flags_returns_defaults() -> None:
    arguments = parse_arguments([])

    assert arguments == SeedArguments(is_dry_run=False, reference_dir=None)


def test_parse_arguments_with_flags_returns_dry_run_and_directory() -> None:
    arguments = parse_arguments(["--dry-run", "--reference-dir", "some/dir"])

    assert arguments.is_dry_run is True
    assert arguments.reference_dir == Path("some/dir")


def test_parse_arguments_unknown_flag_exits_with_code_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as caught:
        parse_arguments(["--wipe"])

    assert caught.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_build_parser_program_name_is_module_invocation() -> None:
    parser = build_parser()

    assert parser.prog == "python -m yakhnama.seed"


def test_apply_arguments_without_directory_returns_same_settings(
    settings: Settings,
) -> None:
    resolved = apply_arguments(settings, SeedArguments())

    assert resolved is settings


def test_apply_arguments_with_directory_overrides_reference_data_dir(
    settings: Settings, tmp_path: Path
) -> None:
    resolved = apply_arguments(settings, SeedArguments(reference_dir=tmp_path))

    assert resolved.reference_data_dir == tmp_path
    assert settings.reference_data_dir == Path("data/reference")


async def test_run_seed_success_returns_zero_and_logs_counts() -> None:
    handler = FakeSeedHandler()
    command = SeedReferenceData(actor=SYSTEM_ACTOR, dry_run=True)

    with capture_logs() as logs:
        exit_code = await run_seed(handler, command)

    assert exit_code == EXIT_SUCCESS
    assert handler.commands == [command]
    (completed,) = logs
    assert completed["event"] == "seed_completed"
    assert completed["dry_run"] is True
    assert completed["is_unchanged"] is False
    assert completed["hazard_types"] == {
        "data_version": DATA_VERSION,
        "created": 2,
        "updated": 0,
        "unchanged": 0,
        "skipped": 0,
    }
    assert completed["impact_metrics"]["updated"] == 1
    assert completed["places"]["unchanged"] == 2


async def test_run_seed_yakhnama_error_returns_one_and_logs_one_line() -> None:
    error = PermissionDeniedError("the actor may not seed", details={"action": "seed"})
    handler = FakeSeedHandler(error=error)

    with capture_logs() as logs:
        exit_code = await run_seed(handler, SeedReferenceData(actor=SYSTEM_ACTOR))

    assert exit_code == EXIT_FAILURE
    assert logs == [
        {
            "event": "seed_failed",
            "log_level": "error",
            "error_code": PermissionDeniedError.code,
            "message": "the actor may not seed",
            "details": {"action": "seed"},
            "dry_run": False,
        }
    ]


async def test_run_seed_other_exception_propagates() -> None:
    async def broken_handler(command: SeedReferenceData) -> SeedReport:
        del command
        message = "database unreachable"
        raise ConnectionRefusedError(message)

    with pytest.raises(ConnectionRefusedError):
        await run_seed(broken_handler, SeedReferenceData(actor=SYSTEM_ACTOR))


def test_log_report_skipped_changes_logged_as_warnings() -> None:
    with capture_logs() as logs:
        log_report(_report(has_skipped=True))

    warning, completed = logs
    assert warning == {
        "event": "seed_change_skipped",
        "log_level": "warning",
        "file": "hazard_types",
        "code": "flood",
        "reason": "parent differs; left for a human",
    }
    assert completed["hazard_types"]["skipped"] == 1


async def test_resolve_actor_id_configured_returns_setting(settings: Settings) -> None:
    configured = settings.model_copy(update={"seed_actor_id": CONFIGURED_ACTOR})
    container = build_container(configured)

    with capture_logs() as logs:
        actor_id = resolve_actor_id(configured, container)
    await container.engine.dispose()

    assert actor_id == CONFIGURED_ACTOR
    assert logs == []


async def test_resolve_actor_id_unset_generates_and_logs_uuid7(
    settings: Settings,
) -> None:
    container = build_container(settings)

    with capture_logs() as logs:
        actor_id = resolve_actor_id(settings, container)
    await container.engine.dispose()

    assert is_uuid7(actor_id)
    assert logs == [
        {
            "event": "seed_actor_generated",
            "log_level": "info",
            "actor_id": str(actor_id),
        }
    ]


async def test_seed_passes_actor_to_builder_and_command_and_disposes_engine(
    settings: Settings, builder: FakeHandlerBuilder
) -> None:
    configured = settings.model_copy(update={"seed_actor_id": CONFIGURED_ACTOR})

    with capture_logs():
        exit_code = await seed(configured, is_dry_run=True, build_handler=builder)

    assert exit_code == EXIT_SUCCESS
    assert builder.handler.commands == [
        SeedReferenceData(actor=SYSTEM_ACTOR, dry_run=True)
    ]
    (container,) = builder.containers
    assert container.settings is configured
    # AsyncEngine.dispose() replaces the pool, so a new pool proves the disposal.
    assert container.engine.sync_engine.pool is not builder.pools[0]


async def test_seed_builder_failure_still_disposes_engine(
    settings: Settings,
) -> None:
    pools: list[object] = []
    containers: list[Container] = []

    def failing_builder(container: Container) -> FakeSeedHandler:
        containers.append(container)
        pools.append(container.engine.sync_engine.pool)
        message = "wiring bug"
        raise RuntimeError(message)

    with capture_logs(), pytest.raises(RuntimeError, match="wiring bug"):
        await seed(settings, is_dry_run=False, build_handler=failing_builder)

    # AsyncEngine.dispose() replaces the pool, so a new pool proves the disposal.
    assert containers[0].engine.sync_engine.pool is not pools[0]


def test_main_with_settings_runs_seed_and_returns_zero(
    settings: Settings, builder: FakeHandlerBuilder, tmp_path: Path
) -> None:
    exit_code = main(
        ["--dry-run", "--reference-dir", str(tmp_path)],
        settings=settings,
        build_handler=builder,
    )

    assert exit_code == EXIT_SUCCESS
    (command,) = builder.handler.commands
    assert command.dry_run is True
    (container,) = builder.containers
    assert container.settings.reference_data_dir == tmp_path


def test_main_handler_error_returns_one(settings: Settings) -> None:
    builder = FakeHandlerBuilder(
        handler=FakeSeedHandler(error=YakhnamaError("reference file is invalid"))
    )

    exit_code = main([], settings=settings, build_handler=builder)

    assert exit_code == EXIT_FAILURE


def test_main_without_settings_loads_them_from_environment(
    builder: FakeHandlerBuilder,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # An empty working directory, so no developer .env file is read.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("YAKHNAMA_SEED_ACTOR_ID", str(CONFIGURED_ACTOR))
    monkeypatch.setenv("YAKHNAMA_LOG_FORMAT", "console")

    exit_code = main([], build_handler=builder)

    assert exit_code == EXIT_SUCCESS
    assert builder.handler.commands[0].actor.user_id == CONFIGURED_ACTOR
    assert builder.containers[0].settings is get_settings()


def test_build_system_actor_is_admin_citizen_without_memberships() -> None:
    actor = build_system_actor(CONFIGURED_ACTOR)

    assert actor.user_id == CONFIGURED_ACTOR
    assert actor.roles == frozenset({Role.CITIZEN, Role.ADMIN})
    assert actor.memberships == frozenset()
    assert CanManageReferenceData().is_allowed(actor) is True
