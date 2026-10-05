"""Unit tests for ``yakhnama.seed.boundaries.cli`` with a fake load handler.

``build_container`` is real (it opens no connection) and the load use case is
replaced through the ``build_handler`` seam, so these tests cover argument parsing,
reading the committed source file, logging and exit codes without PostGIS or a
download.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import pytest
from structlog.testing import capture_logs

from tests.factories.boundaries import boundary_source
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.geography.application.dto import BoundaryLoadReport
from yakhnama.modules.geography.domain.boundaries import (
    DistrictLink,
    DistrictMatch,
    NameDifference,
)
from yakhnama.modules.geography.public import LoadDistrictBoundaries
from yakhnama.platform.container import Container
from yakhnama.platform.settings import Settings
from yakhnama.seed.boundaries.cli import (
    PAYLOAD_BUDGET_BYTES,
    PROGRAM_NAME,
    BoundaryArguments,
    apply_arguments,
    build_parser,
    load,
    log_report,
    main,
    parse_arguments,
    run_load,
)
from yakhnama.seed.cli import EXIT_FAILURE, EXIT_SUCCESS, build_system_actor
from yakhnama.shared_kernel.errors import YakhnamaError

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]
SOURCE_FILE: Final = (
    REPOSITORY_ROOT / "data" / "boundaries" / "cod_ab_pak_gb_districts.yaml"
)
CONFIGURED_ACTOR: Final = SequentialIdGenerator(seed=7).new_id()
EDGE_SET_ID: Final = SequentialIdGenerator(seed=8).new_id()


def _match(*, has_mismatches: bool) -> DistrictMatch:
    linked = (
        DistrictLink(source_code="XX101", source_name="Synthetic", place_code="xx.a"),
    )
    if not has_mismatches:
        return DistrictMatch(
            linked=linked,
            unlinked=(),
            not_in_link_table=(),
            not_in_source=(),
            missing_places=(),
            places_without_boundary=(),
            name_differences=(),
        )
    return DistrictMatch(
        linked=linked,
        unlinked=(
            DistrictLink(
                source_code="XX102", source_name="Synthetic B", place_code=None
            ),
        ),
        not_in_link_table=("XX103",),
        not_in_source=("XX104",),
        missing_places=("xx.missing",),
        places_without_boundary=("xx.orphan",),
        name_differences=(
            NameDifference(
                source_code="XX101",
                source_name="Synthetic",
                other_name="Synthetic A",
                compared_with="gazetteer",
            ),
        ),
    )


def _report(
    *, is_dry_run: bool = False, has_mismatches: bool = False, payload_bytes: int = 100
) -> BoundaryLoadReport:
    return BoundaryLoadReport(
        dry_run=is_dry_run,
        dataset_version="synthetic v1",
        sha256="a" * 64,
        region_code="XX1",
        districts_in_source=2,
        match=_match(has_mismatches=has_mismatches),
        is_coverage_valid=True,
        dropped_parts=0,
        edges=1,
        edges_with_unlinked_district=0,
        positions=2,
        payload_bytes=payload_bytes,
        geometry_updated=("xx.a",),
        geometry_unchanged=(),
        centroid_updated=("xx.a",),
        centroid_unchanged=(),
        centroid_kept=("xx.kept",) if has_mismatches else (),
        edge_set_id=EDGE_SET_ID,
        is_edge_set_created=True,
    )


@dataclass
class FakeBoundaryHandler:
    """Records every command and returns a fixed report or raises a fixed error.

    Implements: Fake (of Command Handler).

    Attributes:
        error: Raised instead of returning a report, if set.
        commands: The commands received, in call order.
    """

    error: YakhnamaError | None = None
    commands: list[LoadDistrictBoundaries] = field(default_factory=list)

    async def __call__(self, command: LoadDistrictBoundaries) -> BoundaryLoadReport:
        """Record ``command``, then raise ``error`` or return a report."""
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        return _report(is_dry_run=command.dry_run)


@dataclass
class FakeHandlerBuilder:
    """Stands in for ``build_district_boundary_handler``.

    Implements: Fake (of Composition Root).

    Attributes:
        handler: The handler handed out.
        containers: The containers received.
    """

    handler: FakeBoundaryHandler = field(default_factory=FakeBoundaryHandler)
    containers: list[Container] = field(default_factory=list)

    def __call__(self, container: Container) -> FakeBoundaryHandler:
        """Record ``container`` and hand out the fake handler."""
        self.containers.append(container)
        return self.handler


@pytest.fixture
def configured(settings: Settings) -> Settings:
    """Return test settings with a fixed actor and the committed source file."""
    return settings.model_copy(
        update={"seed_actor_id": CONFIGURED_ACTOR, "boundary_source_file": SOURCE_FILE}
    )


def test_parse_arguments_without_flags_returns_defaults() -> None:
    assert parse_arguments([]) == BoundaryArguments()


def test_parse_arguments_with_flags_returns_dry_run_and_file(tmp_path: Path) -> None:
    result = parse_arguments(["--dry-run", "--source-file", str(tmp_path / "x.yaml")])

    assert result == BoundaryArguments(is_dry_run=True, source_file=tmp_path / "x.yaml")


def test_build_parser_program_name_is_module_invocation() -> None:
    assert build_parser().prog == PROGRAM_NAME


def test_apply_arguments_without_file_returns_same_settings(settings: Settings) -> None:
    assert apply_arguments(settings, BoundaryArguments()) is settings


def test_apply_arguments_with_file_overrides_setting(
    settings: Settings, tmp_path: Path
) -> None:
    result = apply_arguments(settings, BoundaryArguments(source_file=tmp_path))

    assert result.boundary_source_file == tmp_path


def test_log_report_without_mismatches_logs_one_summary_line() -> None:
    with capture_logs() as logs:
        log_report(_report())

    assert [entry["event"] for entry in logs] == ["boundaries_loaded"]
    assert logs[0]["edges"] == 1
    assert logs[0]["has_mismatches"] is False


def test_log_report_logs_one_warning_per_mismatch_and_over_budget() -> None:
    report = _report(has_mismatches=True, payload_bytes=PAYLOAD_BUDGET_BYTES + 1)

    with capture_logs() as logs:
        log_report(report)

    kinds = [
        entry.get("kind") for entry in logs if entry["event"] == "boundary_mismatch"
    ]
    assert kinds == [
        "unlinked",
        "not_in_link_table",
        "not_in_source",
        "missing_place",
        "place_without_boundary",
        "name_differs",
    ]
    assert [entry["event"] for entry in logs][-3:] == [
        "boundary_centroid_kept",
        "boundary_payload_over_budget",
        "boundaries_loaded",
    ]


async def test_run_load_error_returns_one_and_logs_failure() -> None:
    handler = FakeBoundaryHandler(error=YakhnamaError("checksum", details={"a": 1}))
    command = LoadDistrictBoundaries(
        source=boundary_source([("XX101", None)]),
        actor=build_system_actor(CONFIGURED_ACTOR),
        dry_run=True,
    )

    with capture_logs() as logs:
        exit_code = await run_load(handler, command)

    assert exit_code == EXIT_FAILURE
    assert logs[-1]["event"] == "boundaries_failed"
    assert logs[-1]["details"] == {"a": 1}


async def test_load_reads_source_and_runs_handler_as_system_actor(
    configured: Settings,
) -> None:
    builder = FakeHandlerBuilder()

    with capture_logs():
        exit_code = await load(configured, is_dry_run=True, build_handler=builder)

    assert exit_code == EXIT_SUCCESS
    (command,) = builder.handler.commands
    assert command.dry_run is True
    assert command.actor == build_system_actor(CONFIGURED_ACTOR)
    assert command.source.region_place_code == "pk.gb"


async def test_load_with_missing_source_file_returns_one_without_container(
    configured: Settings, tmp_path: Path
) -> None:
    builder = FakeHandlerBuilder()
    missing = configured.model_copy(
        update={"boundary_source_file": tmp_path / "missing.yaml"}
    )

    with capture_logs() as logs:
        exit_code = await load(missing, is_dry_run=False, build_handler=builder)

    assert exit_code == EXIT_FAILURE
    assert builder.containers == []
    assert logs[-1]["details"]["reason"] == "not_found"


def test_main_runs_load_and_returns_zero(configured: Settings) -> None:
    builder = FakeHandlerBuilder()

    exit_code = main(
        ["--dry-run", "--source-file", str(SOURCE_FILE)],
        settings=configured,
        build_handler=builder,
    )

    assert exit_code == EXIT_SUCCESS
    assert builder.handler.commands[0].dry_run is True


def test_main_handler_error_returns_one(configured: Settings) -> None:
    builder = FakeHandlerBuilder(
        handler=FakeBoundaryHandler(error=YakhnamaError("download failed"))
    )

    exit_code = main([], settings=configured, build_handler=builder)

    assert exit_code == EXIT_FAILURE


def test_main_without_settings_loads_them_from_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An empty working directory, so no developer .env file is read.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("YAKHNAMA_SEED_ACTOR_ID", str(CONFIGURED_ACTOR))
    monkeypatch.setenv("YAKHNAMA_LOG_FORMAT", "console")
    monkeypatch.setenv("YAKHNAMA_BOUNDARY_SOURCE_FILE", str(SOURCE_FILE))
    builder = FakeHandlerBuilder()

    exit_code = main([], build_handler=builder)

    assert exit_code == EXIT_SUCCESS
    assert builder.containers[0].settings.boundary_source_file == SOURCE_FILE
