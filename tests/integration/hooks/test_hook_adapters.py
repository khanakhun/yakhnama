"""Integration tests for the real adapters of the Claude Code hooks.

They run the local ``git``, ``ruff`` and ``mypy`` binaries against throwaway
repositories in ``tmp_path``. No network and no services are involved, but they spawn
processes, which is why they live here and not in ``tests/unit/hooks/``.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

import format_and_check_file
import guard_protected_paths
import pytest
from format_and_check_file import SubprocessRunnerAdapter
from guard_protected_paths import GitCliAdapter

pytestmark = pytest.mark.integration


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """Return a temporary git repository marked as a project root."""
    root = tmp_path / "repository"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = 'fixture'\n")
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)  # noqa: S607  # reason: git from PATH, fixed arguments
    return root.resolve()


def build_payload(repository: Path, file_path: str) -> io.StringIO:
    """Build a hook stdin stream targeting one file.

    Args:
        repository: Session cwd.
        file_path: Value of ``tool_input.file_path``.

    Returns:
        A stream with the JSON payload.
    """
    body = {"cwd": str(repository), "tool_input": {"file_path": file_path}}
    return io.StringIO(json.dumps(body))


def test_git_cli_adapter_tracked_and_untracked_files_answer_correctly(
    repository: Path,
) -> None:
    migration = repository / "migrations" / "versions" / "0001_init.py"
    migration.parent.mkdir(parents=True)
    migration.write_text("")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)  # noqa: S607  # reason: git from PATH, fixed arguments
    adapter = GitCliAdapter()

    is_tracked = adapter.is_tracked(
        repository, PurePosixPath("migrations/versions/0001_init.py")
    )
    is_new_tracked = adapter.is_tracked(
        repository, PurePosixPath("migrations/versions/0002_new.py")
    )

    assert is_tracked
    assert not is_new_tracked


def test_git_cli_adapter_without_git_fails_closed(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "")
    adapter = GitCliAdapter()

    is_tracked = adapter.is_tracked(repository, PurePosixPath("migrations/versions/x"))

    assert is_tracked


def test_git_cli_adapter_outside_a_git_repository_fails_closed(
    tmp_path: Path,
) -> None:
    # git exits 128 ("not a git repository"), which must not read as "untracked".
    not_a_repository = tmp_path / "plain"
    not_a_repository.mkdir()
    adapter = GitCliAdapter()

    is_tracked = adapter.is_tracked(
        not_a_repository, PurePosixPath("migrations/versions/0001_init.py")
    )

    assert is_tracked


def test_guard_migration_outside_a_git_repository_blocks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "plain"
    root.mkdir()
    (root / "pyproject.toml").write_text("")
    hook_file = root / ".claude" / "hooks" / "guard_protected_paths.py"

    exit_code = guard_protected_paths.main(
        build_payload(root, "migrations/versions/0002_new.py"), hook_file=hook_file
    )

    assert exit_code == 2
    assert "committed migration" in capsys.readouterr().err


def test_git_cli_adapter_hung_git_fails_closed(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    # A Python shebang, because PATH below holds only fake_bin (no `sleep` binary).
    fake_git.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))
    monkeypatch.setattr(guard_protected_paths, "GIT_TIMEOUT_SECONDS", 0.2)
    adapter = GitCliAdapter()

    started = time.monotonic()
    is_tracked = adapter.is_tracked(repository, PurePosixPath("migrations/versions/x"))
    elapsed = time.monotonic() - started

    assert is_tracked
    assert elapsed < 10


def test_guard_real_git_tracked_migration_blocks(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    migration = repository / "migrations" / "versions" / "0001_init.py"
    migration.parent.mkdir(parents=True)
    migration.write_text("")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)  # noqa: S607  # reason: git from PATH, fixed arguments
    hook_file = repository / ".claude" / "hooks" / "guard_protected_paths.py"

    exit_code = guard_protected_paths.main(
        build_payload(repository, "migrations/versions/0001_init.py"),
        hook_file=hook_file,
    )

    assert exit_code == 2
    assert "committed migration" in capsys.readouterr().err


def test_subprocess_runner_real_command_captures_status_and_output(
    tmp_path: Path,
) -> None:
    runner = SubprocessRunnerAdapter()
    script = "import os, sys; sys.stdout.write(os.getcwd()); sys.exit(3)"

    result = runner.run([sys.executable, "-c", script], tmp_path)

    assert result.returncode == 3
    assert Path(result.output).resolve() == tmp_path.resolve()


def test_subprocess_runner_missing_executable_raises(tmp_path: Path) -> None:
    runner = SubprocessRunnerAdapter()

    with pytest.raises(FileNotFoundError):
        runner.run([str(tmp_path / "no-such-tool")], tmp_path)


def test_format_hook_real_toolchain_on_well_formed_file_exits_zero(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = repository / "src" / "module.py"
    module.parent.mkdir()
    module.write_text(
        '"""Fixture module."""\n\n\ndef add(left: int, right: int) -> int:\n'
        '    """Add two integers."""\n    return left + right\n'
    )
    hook_file = repository / ".claude" / "hooks" / "format_and_check_file.py"

    exit_code = format_and_check_file.main(
        build_payload(repository, "src/module.py"), environ={}, hook_file=hook_file
    )

    assert capsys.readouterr().err == ""
    assert exit_code == 0


def test_format_hook_real_toolchain_on_type_error_exits_two(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = repository / "src" / "module.py"
    module.parent.mkdir()
    module.write_text('"""Fixture module."""\n\nVALUE: int = "text"\n')
    hook_file = repository / ".claude" / "hooks" / "format_and_check_file.py"

    exit_code = format_and_check_file.main(
        build_payload(repository, "src/module.py"), environ={}, hook_file=hook_file
    )

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "mypy exited 1" in stderr
    assert "Incompatible types in assignment" in stderr
