"""Integration tests for the hook commands as written in ``.claude/settings.json``.

Claude Code blocks a tool call only on exit code 2, so every way the command line can
fail (missing project directory, ``poetry`` unable to find the project, the script
itself) must surface as 2. These tests run the real shell and ``poetry``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SETTINGS_PATH = REPOSITORY_ROOT / ".claude" / "settings.json"
HOOK_EVENTS = ("PreToolUse", "PostToolUse")
GUARD_SCRIPT = REPOSITORY_ROOT / ".claude" / "hooks" / "guard_protected_paths.py"


def load_command(event: str) -> str:
    """Read the single hook command configured for one event.

    Args:
        event: ``PreToolUse`` or ``PostToolUse``.

    Returns:
        The shell command string.
    """
    settings = json.loads(SETTINGS_PATH.read_text())
    (matcher,) = settings["hooks"][event]
    (hook,) = matcher["hooks"]
    command: str = hook["command"]
    return command


def run_command(command: str, project_directory: Path, stdin: str) -> int:
    """Run a settings command through bash the way Claude Code does.

    Args:
        command: The shell command string.
        project_directory: Value of ``CLAUDE_PROJECT_DIR``.
        stdin: Hook payload.

    Returns:
        The exit status.
    """
    environment = {**os.environ, "CLAUDE_PROJECT_DIR": str(project_directory)}
    completed = subprocess.run(  # noqa: S603  # reason: runs the fixed command from our own settings.json
        ["bash", "-c", command],  # noqa: S607  # reason: bash from PATH, as Claude Code uses it
        input=stdin,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode


def build_stdin(file_path: str, *, tool_name: str = "Write") -> str:
    """Build a hook payload as JSON text.

    Args:
        file_path: Target file.
        tool_name: Tool about to run.

    Returns:
        The payload.
    """
    body = {
        "cwd": str(REPOSITORY_ROOT),
        "tool_name": tool_name,
        "tool_input": {"file_path": file_path},
    }
    return json.dumps(body)


@pytest.mark.parametrize("event", HOOK_EVENTS)
def test_settings_command_project_without_pyproject_exits_two(
    event: str, tmp_path: Path
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    exit_code = run_command(load_command(event), empty, build_stdin("README.md"))

    assert exit_code == 2


@pytest.mark.parametrize("event", HOOK_EVENTS)
def test_settings_command_missing_project_directory_exits_two(
    event: str, tmp_path: Path
) -> None:
    missing = tmp_path / "does-not-exist"

    exit_code = run_command(load_command(event), missing, build_stdin("README.md"))

    assert exit_code == 2


@pytest.mark.parametrize(
    ("file_path", "expected"), [("poetry.lock", 2), ("README.md", 0)]
)
def test_settings_guard_command_in_repository_decides(
    file_path: str, expected: int
) -> None:
    command = load_command("PreToolUse")

    exit_code = run_command(command, REPOSITORY_ROOT, build_stdin(file_path))

    assert exit_code == expected


@pytest.mark.parametrize("event", HOOK_EVENTS)
def test_settings_matcher_covers_every_file_writing_tool(event: str) -> None:
    settings = json.loads(SETTINGS_PATH.read_text())

    (matcher,) = settings["hooks"][event]

    assert set(matcher["matcher"].split("|")) == {
        "Edit",
        "Write",
        "MultiEdit",
        "NotebookEdit",
    }


def test_settings_permission_deny_list_is_exact() -> None:
    # `//` anchors at the file-system root, `/` at the project directory; `Edit(...)`
    # covers every built-in editing tool. `.env.*` is not denied wholesale so that
    # `.env.example` stays editable; the guard hook blocks the other variants.
    settings = json.loads(SETTINGS_PATH.read_text())

    deny = settings["permissions"]["deny"]

    assert deny == [
        "Read(//**/.env)",
        "Edit(//**/.env)",
        "Read(//**/.env.local)",
        "Edit(//**/.env.local)",
        "Edit(/poetry.lock)",
        "Edit(/LICENSE)",
        "Edit(/.github/CODEOWNERS)",
    ]


def test_guard_script_run_as_process_from_foreign_cwd_blocks_protected_path() -> None:
    completed = subprocess.run(  # noqa: S603  # reason: our own script under the current interpreter
        [sys.executable, str(GUARD_SCRIPT)],
        input=build_stdin(str(REPOSITORY_ROOT / "LICENSE")),
        capture_output=True,
        text=True,
        cwd="/",
        check=False,
    )

    assert completed.returncode == 2
    assert "LICENSE is maintainer-only" in completed.stderr
