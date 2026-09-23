"""Claude Code PostToolUse hook that formats and checks the Python file just edited.

After every ``Edit``, ``Write`` or ``MultiEdit`` of a ``.py`` file inside the
repository, the hook runs ``ruff format``, ``ruff check --fix`` and ``mypy`` on that
file. Remaining errors are written to stderr with exit code 2, which Claude Code feeds
back to the model so they are fixed immediately. Malformed input and a missing tool
also exit 2, and so does any unexpected exception: a check that cannot run must never
look like a check that passed. ``.claude/settings.json`` adds ``|| exit 2`` for the
failures that happen before Python runs (``cd`` or ``poetry`` failing).

The repository root is the directory two levels above this file, never the payload's
``cwd``; ``cwd`` only anchors a relative ``file_path``.

Set ``YAKHNAMA_HOOK_TIMING=1`` to log the wall time of each tool to stderr.

Patterns: Adapter
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

HOOK_NAME = "format_and_check_file"
EXIT_CLEAN = 0
EXIT_ERRORS = 2
REPOSITORY_MARKER = "pyproject.toml"
TIMING_VARIABLE = "YAKHNAMA_HOOK_TIMING"
HOOK_FILE = Path(__file__)


class ToolInput(BaseModel):
    """The subset of a tool call's arguments the hook needs.

    Implements: Value Object

    Attributes:
        file_path: File the tool wrote; absolute or relative to the session cwd.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    file_path: str | None = None


class HookInput(BaseModel):
    """The PostToolUse payload Claude Code writes to the hook's stdin.

    Implements: Value Object

    Attributes:
        working_directory: Working directory of the session when the hook fired;
            the payload key is ``cwd``.
        tool_name: Name of the tool that ran.
        tool_input: Arguments of the tool call.
    """

    model_config = ConfigDict(
        frozen=True, extra="ignore", validate_by_alias=True, validate_by_name=True
    )

    working_directory: str | None = Field(default=None, alias="cwd")
    tool_name: str | None = None
    tool_input: ToolInput = ToolInput()


class CommandResult(BaseModel):
    """Outcome of one external command.

    Implements: Value Object

    Attributes:
        returncode: Process exit status.
        output: Combined stdout and stderr.
    """

    model_config = ConfigDict(frozen=True)

    returncode: int
    output: str


class ToolStep(BaseModel):
    """One tool invocation of the check sequence.

    Implements: Value Object

    Attributes:
        label: Human-readable name used in messages.
        command: Full argument vector.
    """

    model_config = ConfigDict(frozen=True)

    label: str
    command: tuple[str, ...]


class CommandRunner(Protocol):
    """Port running an external command without a shell.

    Implements: Adapter (port side)
    """

    def run(self, command: Sequence[str], working_directory: Path) -> CommandResult:
        """Run ``command`` in ``working_directory``.

        Args:
            command: Argument vector.
            working_directory: Directory the command runs in.

        Returns:
            The exit status and combined output.

        Raises:
            FileNotFoundError: If the executable does not exist.
        """
        ...


class SubprocessRunnerAdapter:
    """CommandRunner backed by :func:`subprocess.run`.

    Implements: Adapter
    """

    def run(self, command: Sequence[str], working_directory: Path) -> CommandResult:
        """Run ``command`` in ``working_directory`` and capture its output.

        Args:
            command: Argument vector.
            working_directory: Directory the command runs in.

        Returns:
            The exit status and combined stdout and stderr.

        Raises:
            FileNotFoundError: If the executable does not exist.
        """
        completed = subprocess.run(  # noqa: S603  # reason: arguments are a fixed list built from sys.executable, no shell
            list(command),
            cwd=working_directory,
            capture_output=True,
            text=True,
            check=False,
        )
        return CommandResult(
            returncode=completed.returncode,
            output=completed.stdout + completed.stderr,
        )


def build_steps(path: str) -> tuple[ToolStep, ...]:
    """Build the ordered tool sequence for one file.

    ``--force-exclude`` makes ruff honour ``extend-exclude`` (for example
    ``migrations/versions``) even though the path is passed explicitly. ``--`` ends
    option parsing, so a file named like ``--config=evil.toml`` stays a file name.

    Args:
        path: File path relative to the repository root.

    Returns:
        ``ruff format``, ``ruff check --fix`` and ``mypy``, in that order.
    """
    python = sys.executable
    return (
        ToolStep(
            label="ruff format",
            command=(python, "-m", "ruff", "format", "--force-exclude", "--", path),
        ),
        ToolStep(
            label="ruff check",
            command=(
                python,
                "-m",
                "ruff",
                "check",
                "--fix",
                "--force-exclude",
                "--",
                path,
            ),
        ),
        ToolStep(label="mypy", command=(python, "-m", "mypy", "--", path)),
    )


def resolve_repository_root(hook_file: Path) -> Path:
    """Derive the repository root from the location of the hook file.

    Args:
        hook_file: Path of this module, ``<root>/.claude/hooks/<name>.py``.

    Returns:
        The absolute, resolved repository root.

    Raises:
        FileNotFoundError: If the derived root holds no ``pyproject.toml``.
    """
    repository_root = hook_file.resolve().parents[2]
    if not (repository_root / REPOSITORY_MARKER).is_file():
        message = f"{repository_root} has no {REPOSITORY_MARKER}; cannot locate root"
        raise FileNotFoundError(message)
    return repository_root


def locate_python_file(hook_input: HookInput, repository_root: Path) -> str | None:
    """Resolve the edited file when it is a Python file inside the repository.

    A relative path is anchored at the payload's ``cwd`` when given, otherwise at the
    repository root.

    Args:
        hook_input: The parsed hook payload.
        repository_root: Absolute, resolved repository root.

    Returns:
        The file path relative to the repository root, or None when there is nothing
        to check (no path, not ``.py``, outside the repository, or deleted).
    """
    raw_path = hook_input.tool_input.file_path
    if not raw_path or not raw_path.endswith(".py"):
        return None
    anchor = (
        repository_root / hook_input.working_directory
        if hook_input.working_directory
        else repository_root
    )
    absolute = (anchor / raw_path).resolve()
    if not absolute.is_relative_to(repository_root) or not absolute.is_file():
        return None
    return absolute.relative_to(repository_root).as_posix()


def parse_hook_input(raw: str) -> HookInput:
    """Parse the stdin payload.

    Args:
        raw: The text Claude Code wrote to stdin.

    Returns:
        The validated payload.

    Raises:
        ValueError: If the text is not a JSON object matching the payload shape.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        message = f"stdin is not valid JSON ({error.msg})"
        raise ValueError(message) from error
    if not isinstance(payload, dict):
        message = "stdin JSON is not an object"
        raise ValueError(message)
    try:
        return HookInput.model_validate(payload)
    except ValidationError as error:
        message = (
            f"stdin JSON does not match the hook payload ({error.error_count()} errors)"
        )
        raise ValueError(message) from error


def run_steps(
    steps: Sequence[ToolStep],
    repository_root: Path,
    runner: CommandRunner,
    *,
    is_timed: bool,
    clock: Callable[[], float] = time.perf_counter,
) -> list[str]:
    """Run every step and collect the failures.

    Every step runs even after a failure so the model sees all remaining errors in
    one round trip.

    Args:
        steps: Tools to run, in order.
        repository_root: Working directory for the tools (where the config lives).
        runner: Port executing the commands.
        is_timed: Whether to log each tool's wall time to stderr.
        clock: Monotonic clock in seconds, injectable for tests.

    Returns:
        One report per failed step; empty when the file is clean.
    """
    failures: list[str] = []
    for step in steps:
        started = clock()
        try:
            result = runner.run(step.command, repository_root)
        except FileNotFoundError as error:
            failures.append(f"{step.label} could not run: {error}")
            continue
        finally:
            if is_timed:
                elapsed = clock() - started
                sys.stderr.write(f"{HOOK_NAME}: {step.label} took {elapsed:.3f}s\n")
        if result.returncode != 0:
            failures.append(
                f"{step.label} exited {result.returncode}:\n{result.output.rstrip()}"
            )
    return failures


def check_payload(
    raw: str,
    runner: CommandRunner,
    environment: Mapping[str, str],
    repository_root: Path,
) -> int:
    """Check the file named by one PostToolUse payload.

    Args:
        raw: The text Claude Code wrote to stdin.
        runner: Port executing the tools.
        environment: Environment variables.
        repository_root: Absolute, resolved repository root.

    Returns:
        ``0`` when the file is clean or not checked, ``2`` when errors remain.

    Raises:
        ValueError: If the payload is malformed.
    """
    hook_input = parse_hook_input(raw)
    relative = locate_python_file(hook_input, repository_root)
    if relative is None:
        return EXIT_CLEAN
    failures = run_steps(
        build_steps(relative),
        repository_root,
        runner,
        is_timed=environment.get(TIMING_VARIABLE) == "1",
    )
    if not failures:
        return EXIT_CLEAN
    report = "\n\n".join(failures)
    sys.stderr.write(f"{HOOK_NAME}: remaining errors in {relative}:\n{report}\n")
    return EXIT_ERRORS


def main(
    stdin: IO[str] = sys.stdin,
    runner: CommandRunner | None = None,
    environ: Mapping[str, str] | None = None,
    hook_file: Path = HOOK_FILE,
) -> int:
    """Run the hook on one PostToolUse payload.

    Args:
        stdin: Stream carrying the hook payload.
        runner: Port executing the tools; defaults to :mod:`subprocess`.
        environ: Environment variables; defaults to :data:`os.environ`.
        hook_file: Location the repository root is derived from; defaults to this
            module, injectable for tests.

    Returns:
        ``0`` when the file is clean or not checked, ``2`` when errors remain or the
        hook itself fails.
    """
    try:
        return check_payload(
            stdin.read(),
            runner or SubprocessRunnerAdapter(),
            os.environ if environ is None else environ,
            resolve_repository_root(hook_file),
        )
    except ValueError as error:
        sys.stderr.write(f"{HOOK_NAME}: cannot check the file: {error}\n")
        return EXIT_ERRORS
    except Exception as error:  # noqa: BLE001  # reason: hook boundary must fail closed
        sys.stderr.write(
            f"{HOOK_NAME}: cannot check the file: hook failed "
            f"({type(error).__name__}: {error})\n"
        )
        return EXIT_ERRORS


if __name__ == "__main__":
    sys.exit(main())
