"""Unit tests for the PostToolUse hook ``.claude/hooks/format_and_check_file.py``.

These tests spawn no process: the tools are replaced by :class:`FakeCommandRunner` and
the repository root is a temporary directory injected through ``hook_file``. Tests that
run the real ruff, mypy or shell live in ``tests/integration/hooks/``.
"""

from __future__ import annotations

import io
import json
import runpy
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import format_and_check_file
import pytest
from format_and_check_file import (
    CommandResult,
    ToolStep,
    build_steps,
    main,
    resolve_repository_root,
    run_steps,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

HOOK_PATH = Path(format_and_check_file.__file__)
CLEAN = CommandResult(returncode=0, output="")


class FakeCommandRunner:
    """CommandRunner that records calls and answers from a script.

    Implements: Fake
    """

    def __init__(
        self,
        results: dict[str, CommandResult] | None = None,
        missing: frozenset[str] = frozenset(),
    ) -> None:
        """Create the fake.

        Args:
            results: Result per tool keyword (``format``, ``check``, ``mypy``);
                unscripted tools succeed.
            missing: Tool keywords that raise FileNotFoundError, as if not installed.
        """
        self.results = results or {}
        self.missing = missing
        self.calls: list[tuple[tuple[str, ...], Path]] = []

    def run(self, command: Sequence[str], working_directory: Path) -> CommandResult:
        """Record the call and return the scripted result.

        Args:
            command: Argument vector.
            working_directory: Directory the command runs in.

        Returns:
            The scripted result, or success.

        Raises:
            FileNotFoundError: If the tool is scripted as missing.
        """
        self.calls.append((tuple(command), working_directory))
        keyword = next(word for word in ("format", "check", "mypy") if word in command)
        if keyword in self.missing:
            message = f"No such file or directory: {keyword}"
            raise FileNotFoundError(message)
        return self.results.get(keyword, CLEAN)


class ExplodingCommandRunner:
    """CommandRunner that raises, standing in for any unexpected hook failure.

    Implements: Fake
    """

    def run(self, command: Sequence[str], working_directory: Path) -> CommandResult:
        """Raise instead of running.

        Args:
            command: Ignored.
            working_directory: Ignored.

        Returns:
            Never returns.

        Raises:
            RuntimeError: Always.
        """
        message = "runner exploded"
        raise RuntimeError(message)


class TickingClock:
    """Deterministic clock advancing a fixed step per reading.

    Implements: Fake
    """

    def __init__(self, step: float) -> None:
        """Create the clock.

        Args:
            step: Seconds added on every reading.
        """
        self.ticks: Iterator[float] = (index * step for index in range(1_000))

    def __call__(self) -> float:
        """Read the clock.

        Returns:
            The next reading.
        """
        return next(self.ticks)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """Return a temporary repository root containing one Python file."""
    root = tmp_path / "repository"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = 'fixture'\n")
    module = root / "src" / "module.py"
    module.parent.mkdir()
    module.write_text('"""Fixture module."""\n')
    return root.resolve()


@pytest.fixture
def hook_file(repository: Path) -> Path:
    """Return where the hook would live inside the temporary repository."""
    return repository / ".claude" / "hooks" / "format_and_check_file.py"


def build_payload(
    repository: Path, file_path: str | None, *, working_directory: Path | None = None
) -> io.StringIO:
    """Build a PostToolUse stdin stream.

    Args:
        repository: Default session cwd.
        file_path: Value of ``tool_input.file_path``; omitted when None.
        working_directory: Session cwd; defaults to ``repository``.

    Returns:
        A stream with the JSON payload.
    """
    tool_input = {} if file_path is None else {"file_path": file_path}
    body = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Edit",
        "cwd": str(working_directory or repository),
        "tool_input": tool_input,
        "tool_response": {"success": True},
    }
    return io.StringIO(json.dumps(body))


def test_format_hook_clean_file_runs_tools_in_order_and_exits_zero(
    repository: Path, hook_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = FakeCommandRunner()
    stdin = build_payload(repository, "src/module.py")

    exit_code = main(stdin, runner, environ={}, hook_file=hook_file)

    assert exit_code == 0
    assert [call[0][3:] for call in runner.calls] == [
        ("format", "--force-exclude", "--", "src/module.py"),
        ("check", "--fix", "--force-exclude", "--", "src/module.py"),
        ("--", "src/module.py"),
    ]
    assert all(call[0][:2] == (sys.executable, "-m") for call in runner.calls)
    assert all(call[1] == repository for call in runner.calls)
    assert capsys.readouterr().err == ""


def test_format_hook_absolute_path_is_made_repository_relative(
    repository: Path, hook_file: Path
) -> None:
    runner = FakeCommandRunner()
    stdin = build_payload(repository, str(repository / "src" / "module.py"))

    exit_code = main(stdin, runner, environ={}, hook_file=hook_file)

    assert exit_code == 0
    assert runner.calls[0][0][-1] == "src/module.py"


def test_format_hook_relative_path_from_subdirectory_working_directory_is_resolved(
    repository: Path, hook_file: Path
) -> None:
    runner = FakeCommandRunner()
    stdin = build_payload(repository, "module.py", working_directory=repository / "src")

    exit_code = main(stdin, runner, environ={}, hook_file=hook_file)

    assert exit_code == 0
    assert runner.calls[0][0][-1] == "src/module.py"
    assert runner.calls[0][1] == repository


def test_format_hook_working_directory_outside_repository_keeps_repository_root(
    repository: Path, hook_file: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "pyproject.toml").write_text("")
    runner = FakeCommandRunner()
    stdin = build_payload(
        repository, str(repository / "src" / "module.py"), working_directory=outside
    )

    exit_code = main(stdin, runner, environ={}, hook_file=hook_file)

    assert exit_code == 0
    assert all(call[1] == repository for call in runner.calls)


@pytest.mark.parametrize("name", ["README.md", "pyproject.toml", "src/module.pyi"])
def test_format_hook_non_python_file_is_skipped(
    repository: Path, hook_file: Path, name: str
) -> None:
    runner = FakeCommandRunner()

    exit_code = main(
        build_payload(repository, name), runner, environ={}, hook_file=hook_file
    )

    assert exit_code == 0
    assert runner.calls == []


def test_format_hook_missing_file_path_is_skipped(
    repository: Path, hook_file: Path
) -> None:
    runner = FakeCommandRunner()

    exit_code = main(
        build_payload(repository, None), runner, environ={}, hook_file=hook_file
    )

    assert exit_code == 0
    assert runner.calls == []


def test_format_hook_file_outside_repository_is_skipped(
    repository: Path, hook_file: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside" / "script.py"
    outside.parent.mkdir()
    outside.write_text("")
    runner = FakeCommandRunner()

    exit_code = main(
        build_payload(repository, str(outside)), runner, environ={}, hook_file=hook_file
    )

    assert exit_code == 0
    assert runner.calls == []


def test_format_hook_deleted_file_is_skipped(repository: Path, hook_file: Path) -> None:
    runner = FakeCommandRunner()

    exit_code = main(
        build_payload(repository, "src/gone.py"),
        runner,
        environ={},
        hook_file=hook_file,
    )

    assert exit_code == 0
    assert runner.calls == []


def test_format_hook_missing_working_directory_resolves_against_repository_root(
    hook_file: Path,
) -> None:
    runner = FakeCommandRunner()
    stdin = io.StringIO(json.dumps({"tool_input": {"file_path": "src/module.py"}}))

    exit_code = main(stdin, runner, environ={}, hook_file=hook_file)

    assert exit_code == 0
    assert len(runner.calls) == 3


def test_format_hook_ruff_failure_exits_two_with_output(
    repository: Path, hook_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = FakeCommandRunner(
        {"check": CommandResult(returncode=1, output="src/module.py:1:1: F401\n")}
    )
    stdin = build_payload(repository, "src/module.py")

    exit_code = main(stdin, runner, environ={}, hook_file=hook_file)

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert stderr.startswith(
        "format_and_check_file: remaining errors in src/module.py:"
    )
    assert "ruff check exited 1:\nsrc/module.py:1:1: F401" in stderr
    assert len(runner.calls) == 3


def test_format_hook_mypy_failure_exits_two_with_output(
    repository: Path, hook_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = FakeCommandRunner(
        {"mypy": CommandResult(returncode=1, output="error: Missing return")}
    )
    stdin = build_payload(repository, "src/module.py")

    exit_code = main(stdin, runner, environ={}, hook_file=hook_file)

    assert exit_code == 2
    assert "mypy exited 1:\nerror: Missing return" in capsys.readouterr().err


def test_format_hook_ruff_and_mypy_failures_are_combined(
    repository: Path, hook_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = FakeCommandRunner(
        {
            "check": CommandResult(returncode=1, output="lint error"),
            "mypy": CommandResult(returncode=1, output="type error"),
        }
    )
    stdin = build_payload(repository, "src/module.py")

    exit_code = main(stdin, runner, environ={}, hook_file=hook_file)

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "lint error" in stderr
    assert "type error" in stderr


def test_format_hook_format_failure_exits_two(
    repository: Path, hook_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = FakeCommandRunner(
        {"format": CommandResult(returncode=2, output="error: Failed to parse")}
    )
    stdin = build_payload(repository, "src/module.py")

    exit_code = main(stdin, runner, environ={}, hook_file=hook_file)

    assert exit_code == 2
    assert "ruff format exited 2" in capsys.readouterr().err


def test_format_hook_missing_tool_exits_two(
    repository: Path, hook_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = FakeCommandRunner(missing=frozenset({"format", "check"}))
    stdin = build_payload(repository, "src/module.py")

    exit_code = main(stdin, runner, environ={}, hook_file=hook_file)

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "ruff format could not run" in stderr
    assert "ruff check could not run" in stderr
    assert len(runner.calls) == 3


def test_format_hook_unexpected_exception_exits_two_fail_closed(
    repository: Path, hook_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stdin = build_payload(repository, "src/module.py")

    exit_code = main(stdin, ExplodingCommandRunner(), environ={}, hook_file=hook_file)

    assert exit_code == 2
    assert capsys.readouterr().err == (
        "format_and_check_file: cannot check the file: hook failed "
        "(RuntimeError: runner exploded)\n"
    )


def test_format_hook_outside_a_repository_exits_two_fail_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    orphan_hook = tmp_path / "copy" / ".claude" / "hooks" / "format.py"
    runner = FakeCommandRunner()

    exit_code = main(
        build_payload(tmp_path, "x.py"), runner, environ={}, hook_file=orphan_hook
    )

    assert exit_code == 2
    assert "has no pyproject.toml" in capsys.readouterr().err
    assert runner.calls == []


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("", "not valid JSON"),
        ("{", "not valid JSON"),
        ('"text"', "not an object"),
        ('{"cwd": 5}', "does not match"),
    ],
)
def test_format_hook_malformed_input_exits_two(
    hook_file: Path, raw: str, message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = FakeCommandRunner()

    exit_code = main(io.StringIO(raw), runner, environ={}, hook_file=hook_file)

    assert exit_code == 2
    assert message in capsys.readouterr().err
    assert runner.calls == []


def test_format_hook_timing_variable_logs_each_tool(
    repository: Path, hook_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = FakeCommandRunner()
    stdin = build_payload(repository, "src/module.py")

    exit_code = main(
        stdin, runner, environ={"YAKHNAMA_HOOK_TIMING": "1"}, hook_file=hook_file
    )

    assert exit_code == 0
    lines = capsys.readouterr().err.splitlines()
    assert [line.split(" took ")[0] for line in lines] == [
        "format_and_check_file: ruff format",
        "format_and_check_file: ruff check",
        "format_and_check_file: mypy",
    ]


@pytest.mark.parametrize("value", ["0", "true", ""])
def test_format_hook_timing_variable_other_value_is_silent(
    repository: Path, hook_file: Path, value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = FakeCommandRunner()
    stdin = build_payload(repository, "src/module.py")

    exit_code = main(
        stdin, runner, environ={"YAKHNAMA_HOOK_TIMING": value}, hook_file=hook_file
    )

    assert exit_code == 0
    assert capsys.readouterr().err == ""


def test_run_steps_timed_reports_elapsed_seconds_even_for_missing_tool(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    steps = (ToolStep(label="mypy", command=("mypy", "x.py")),)
    runner = FakeCommandRunner(missing=frozenset({"mypy"}))

    failures = run_steps(
        steps, repository, runner, is_timed=True, clock=TickingClock(0.25)
    )

    assert failures == ["mypy could not run: No such file or directory: mypy"]
    assert capsys.readouterr().err == "format_and_check_file: mypy took 0.250s\n"


def test_build_steps_any_file_runs_current_interpreter_modules_with_path_last() -> None:
    steps = build_steps("a.py")

    assert [step.label for step in steps] == ["ruff format", "ruff check", "mypy"]
    assert [step.command[2] for step in steps] == ["ruff", "ruff", "mypy"]
    assert all(step.command[0] == sys.executable for step in steps)
    assert all(step.command[-2:] == ("--", "a.py") for step in steps)


def test_build_steps_option_like_file_name_follows_separator() -> None:
    steps = build_steps("--config=evil.toml")

    assert all(step.command.index("--") == len(step.command) - 2 for step in steps)


def test_resolve_repository_root_with_marker_returns_grandparent_of_hooks(
    repository: Path, hook_file: Path
) -> None:
    root = resolve_repository_root(hook_file)

    assert root == repository


def test_resolve_repository_root_without_marker_raises(tmp_path: Path) -> None:
    orphan_hook = tmp_path / ".claude" / "hooks" / "format.py"

    with pytest.raises(FileNotFoundError, match=r"has no pyproject\.toml"):
        resolve_repository_root(orphan_hook)


def test_format_hook_script_entry_point_exits_with_main_code(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # README.md is not Python, so the real hook exits before running any tool.
    monkeypatch.setattr(sys, "stdin", build_payload(repository, "README.md"))

    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(HOOK_PATH), run_name="__main__")

    assert raised.value.code == 0
