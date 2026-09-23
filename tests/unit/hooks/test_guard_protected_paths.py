"""Unit tests for the PreToolUse guard ``.claude/hooks/guard_protected_paths.py``.

These tests spawn no process: git is replaced by :class:`FakeGitTracker` and the
repository root is a temporary directory injected through ``hook_file``. Tests that run
real git or the real shell live in ``tests/integration/hooks/``.
"""

from __future__ import annotations

import io
import json
import runpy
import sys
from pathlib import Path, PurePosixPath

import guard_protected_paths
import pytest
from guard_protected_paths import (
    Decision,
    HookInput,
    ProtectedPathPolicy,
    ToolInput,
    main,
    resolve_repository_root,
)
from hypothesis import given
from hypothesis import strategies as st

HOOK_PATH = Path(guard_protected_paths.__file__)
LOCKED_PATHS = (
    "poetry.lock",
    "LICENSE",
    ".github/CODEOWNERS",
    "Poetry.LOCK",
    "license",
    ".github/codeowners",
)
LEAD_ONLY_PATHS = (
    "AGENTS.md",
    ".claude/settings.json",
    ".claude/hooks/guard_protected_paths.py",
    ".claude/hooks/format_and_check_file.py",
    ".claude/hooks/new_hook.py",
    ".claude/hooks",
    ".Claude/Hooks/guard_protected_paths.py",
    ".claude/settings.local.json",
    ".claude/agents/x.md",
    ".claude/skills/new-module/SKILL.md",
    ".claude",
)


class FakeGitTracker:
    """In-memory GitTracker recording every query.

    Implements: Fake
    """

    def __init__(self, tracked: frozenset[str] = frozenset()) -> None:
        """Create the fake.

        Args:
            tracked: Repository-relative POSIX paths git should report as tracked.
        """
        self.tracked = tracked
        self.queries: list[str] = []

    def is_tracked(self, repository_root: Path, relative: PurePosixPath) -> bool:
        """Answer from the in-memory set.

        Args:
            repository_root: Ignored.
            relative: Queried path.

        Returns:
            True when the path is in the tracked set.
        """
        self.queries.append(relative.as_posix())
        return relative.as_posix() in self.tracked


class ExplodingGitTracker:
    """GitTracker that raises, standing in for any unexpected failure in the guard.

    Implements: Fake
    """

    def is_tracked(self, repository_root: Path, relative: PurePosixPath) -> bool:
        """Raise instead of answering.

        Args:
            repository_root: Ignored.
            relative: Ignored.

        Returns:
            Never returns.

        Raises:
            RuntimeError: Always.
        """
        message = "index is corrupt"
        raise RuntimeError(message)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """Return a temporary directory marked as a repository root."""
    root = tmp_path / "repository"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = 'fixture'\n")
    return root.resolve()


@pytest.fixture
def hook_file(repository: Path) -> Path:
    """Return where the hook would live inside the temporary repository."""
    return repository / ".claude" / "hooks" / "guard_protected_paths.py"


def build_payload(  # noqa: PLR0913  # reason: one keyword per payload field under test
    repository: Path,
    file_path: str | None,
    *,
    agent_id: str | None = None,
    working_directory: Path | None = None,
    tool_name: str = "Write",
    path_field: str = "file_path",
    is_working_directory_omitted: bool = False,
) -> io.StringIO:
    """Build a PreToolUse stdin stream.

    Args:
        repository: Repository root used as the default cwd.
        file_path: Value of the target path field; omitted when None.
        agent_id: Subagent id; omitted when None (lead session).
        working_directory: Session cwd; defaults to ``repository``.
        tool_name: Tool about to run.
        path_field: ``file_path`` or ``notebook_path``.
        is_working_directory_omitted: Leave ``cwd`` out of the payload entirely.

    Returns:
        A stream with the JSON payload.
    """
    tool_input: dict[str, str] = {"content": ""}
    if file_path is not None:
        tool_input[path_field] = file_path
    body: dict[str, object] = {
        "session_id": "session",
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
    }
    if not is_working_directory_omitted:
        body["cwd"] = str(working_directory or repository)
    if agent_id is not None:
        body["agent_id"] = agent_id
        body["agent_type"] = "docs-writer"
    return io.StringIO(json.dumps(body))


@pytest.mark.parametrize("name", [".env", ".env.local", ".env.production", ".ENV"])
def test_guard_env_file_blocks_with_reason(
    repository: Path, hook_file: Path, name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    stdin = build_payload(repository, name)

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert stderr.startswith("guard_protected_paths: blocked:")
    assert "environment file" in stderr


def test_guard_env_file_in_subdirectory_blocks(
    repository: Path, hook_file: Path
) -> None:
    stdin = build_payload(repository, "deploy/.env.staging")

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 2


@pytest.mark.parametrize("name", [".env.example", "deploy/.env.example"])
def test_guard_env_example_is_exempt_and_allows(
    repository: Path, hook_file: Path, name: str
) -> None:
    stdin = build_payload(repository, name)

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 0


@pytest.mark.parametrize("name", ["env.py", ".envrc", "settings.env", ".environment"])
def test_guard_env_lookalike_allows(
    repository: Path, hook_file: Path, name: str
) -> None:
    stdin = build_payload(repository, name)

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 0


@pytest.mark.parametrize("name", LOCKED_PATHS)
def test_guard_locked_path_blocks(
    repository: Path, hook_file: Path, name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    stdin = build_payload(repository, name)

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 2
    assert name in capsys.readouterr().err


@pytest.mark.parametrize("name", ["docs/poetry.lock", "docs/LICENSE", "CODEOWNERS"])
def test_guard_locked_name_elsewhere_allows(
    repository: Path, hook_file: Path, name: str
) -> None:
    stdin = build_payload(repository, name)

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 0


def test_guard_tracked_migration_blocks(
    repository: Path, hook_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tracker = FakeGitTracker(frozenset({"migrations/versions/0001_init.py"}))
    stdin = build_payload(repository, "migrations/versions/0001_init.py")

    exit_code = main(stdin, tracker, hook_file)

    assert exit_code == 2
    assert "committed migration" in capsys.readouterr().err
    assert tracker.queries == ["migrations/versions/0001_init.py"]


def test_guard_untracked_migration_allows(repository: Path, hook_file: Path) -> None:
    tracker = FakeGitTracker()
    stdin = build_payload(repository, "migrations/versions/0002_new.py")

    exit_code = main(stdin, tracker, hook_file)

    assert exit_code == 0
    assert tracker.queries == ["migrations/versions/0002_new.py"]


@pytest.mark.parametrize(
    "name", ["migrations/env.py", "migrations/versions", "src/migrations_versions.py"]
)
def test_guard_migration_rule_outside_versions_skips_tracker(
    repository: Path, hook_file: Path, name: str
) -> None:
    tracker = FakeGitTracker(frozenset({name}))
    stdin = build_payload(repository, name)

    exit_code = main(stdin, tracker, hook_file)

    assert exit_code == 0
    assert tracker.queries == []


@pytest.mark.parametrize("name", LEAD_ONLY_PATHS)
def test_guard_lead_only_path_from_subagent_blocks(
    repository: Path, hook_file: Path, name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    stdin = build_payload(repository, name, agent_id="agent-1")

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "lead session, not docs-writer" in stderr


@pytest.mark.parametrize("name", LEAD_ONLY_PATHS)
def test_guard_lead_only_path_from_lead_allows(
    repository: Path, hook_file: Path, name: str
) -> None:
    stdin = build_payload(repository, name)

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 0


def test_guard_claude_directory_reason_names_directory(
    repository: Path, hook_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stdin = build_payload(repository, ".claude/settings.local.json", agent_id="agent-1")

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 2
    assert "is under .claude/" in capsys.readouterr().err


@pytest.mark.parametrize(
    "name",
    [
        "docs/AGENTS.md",
        ".claude.md",
        ".claudette/settings.json",
        "docs/.claude/settings.json",
        "tests/unit/hooks/test_guard_protected_paths.py",
    ],
)
def test_guard_lead_only_lookalike_from_subagent_allows(
    repository: Path, hook_file: Path, name: str
) -> None:
    stdin = build_payload(repository, name, agent_id="agent-1")

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 0


@pytest.mark.parametrize(
    "hook_input",
    [
        HookInput(tool_input=ToolInput(file_path="AGENTS.md"), agent_id="agent-7"),
        HookInput(
            tool_input=ToolInput(file_path=".claude/hooks/x.py"), agent_id="agent-7"
        ),
    ],
)
def test_guard_subagent_without_type_names_agent_id(
    repository: Path, hook_input: HookInput
) -> None:
    decision = ProtectedPathPolicy(repository, FakeGitTracker()).evaluate(hook_input)

    assert not decision.allowed
    assert decision.reason is not None
    assert decision.reason.endswith("agent-7")


@pytest.mark.parametrize("name", ["settings.json", "settings.local.json"])
def test_guard_user_settings_from_subagent_blocks(
    repository: Path,
    hook_file: Path,
    tmp_path: Path,
    name: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    stdin = build_payload(repository, str(home / ".claude" / name), agent_id="agent-1")

    exit_code = main(stdin, FakeGitTracker(), hook_file, home)

    assert exit_code == 2
    assert "user-level Claude Code settings file" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["settings.json", "settings.local.json"])
def test_guard_user_settings_from_lead_allows(
    repository: Path, hook_file: Path, tmp_path: Path, name: str
) -> None:
    home = tmp_path / "home"
    stdin = build_payload(repository, str(home / ".claude" / name))

    exit_code = main(stdin, FakeGitTracker(), hook_file, home)

    assert exit_code == 0


def test_guard_user_settings_via_dot_segments_and_case_from_subagent_blocks(
    repository: Path, hook_file: Path, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    disguised = home / "other" / ".." / ".claude" / "Settings.Local.JSON"
    stdin = build_payload(repository, str(disguised), agent_id="agent-1")

    exit_code = main(stdin, FakeGitTracker(), hook_file, home)

    assert exit_code == 2


def test_guard_user_settings_via_symlink_from_subagent_blocks(
    repository: Path, hook_file: Path, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text("{}")
    (tmp_path / "alias.json").symlink_to(home / ".claude" / "settings.json")
    stdin = build_payload(repository, str(tmp_path / "alias.json"), agent_id="agent-1")

    exit_code = main(stdin, FakeGitTracker(), hook_file, home)

    assert exit_code == 2


@pytest.mark.parametrize(
    "relative", [".claude/agents/x.md", ".claude/CLAUDE.md", "settings.json"]
)
def test_guard_other_home_files_from_subagent_allow(
    repository: Path, hook_file: Path, tmp_path: Path, relative: str
) -> None:
    home = tmp_path / "home"
    stdin = build_payload(repository, str(home / relative), agent_id="agent-1")

    exit_code = main(stdin, FakeGitTracker(), hook_file, home)

    assert exit_code == 0


def test_guard_user_settings_default_home_comes_from_environment(
    repository: Path,
    hook_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    stdin = build_payload(
        repository, str(home / ".claude" / "settings.json"), agent_id="agent-1"
    )

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 2


def test_guard_notebook_edit_of_protected_path_blocks(
    repository: Path, hook_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stdin = build_payload(
        repository, "LICENSE", tool_name="NotebookEdit", path_field="notebook_path"
    )

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 2
    assert "LICENSE is maintainer-only" in capsys.readouterr().err


def test_guard_notebook_edit_of_ordinary_notebook_allows(
    repository: Path, hook_file: Path
) -> None:
    stdin = build_payload(
        repository,
        "notebooks/analysis.ipynb",
        tool_name="NotebookEdit",
        path_field="notebook_path",
    )

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 0


def test_guard_protected_path_hidden_in_second_field_blocks(
    repository: Path, hook_file: Path
) -> None:
    body = {
        "cwd": str(repository),
        "tool_input": {"file_path": "README.md", "notebook_path": "poetry.lock"},
    }

    exit_code = main(io.StringIO(json.dumps(body)), FakeGitTracker(), hook_file)

    assert exit_code == 2


def test_guard_absolute_protected_path_blocks(
    repository: Path, hook_file: Path
) -> None:
    stdin = build_payload(repository, str(repository / "poetry.lock"))

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 2


def test_guard_working_directory_outside_repository_absolute_protected_path_blocks(
    repository: Path, hook_file: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "pyproject.toml").write_text("")
    stdin = build_payload(
        repository, str(repository / "poetry.lock"), working_directory=outside
    )

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 2


def test_guard_working_directory_outside_repository_does_not_move_the_root(
    repository: Path, hook_file: Path, tmp_path: Path
) -> None:
    # A forged working directory pointing at another project must not make that
    # project's poetry.lock the protected one, nor unprotect ours.
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "pyproject.toml").write_text("")
    stdin = build_payload(repository, "poetry.lock", working_directory=outside)

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 0


def test_guard_missing_working_directory_absolute_protected_path_blocks(
    repository: Path, hook_file: Path
) -> None:
    stdin = build_payload(
        repository, str(repository / "poetry.lock"), is_working_directory_omitted=True
    )

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 2


def test_guard_missing_working_directory_relative_path_resolves_against_root_and_blocks(
    repository: Path, hook_file: Path
) -> None:
    stdin = build_payload(repository, "poetry.lock", is_working_directory_omitted=True)

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 2


def test_guard_relative_working_directory_is_anchored_at_root_and_blocks(
    repository: Path, hook_file: Path
) -> None:
    body = {"cwd": "src/yakhnama", "tool_input": {"file_path": "../../LICENSE"}}

    exit_code = main(io.StringIO(json.dumps(body)), FakeGitTracker(), hook_file)

    assert exit_code == 2


def test_guard_relative_path_from_subdirectory_working_directory_blocks(
    repository: Path, hook_file: Path
) -> None:
    subdirectory = repository / "src" / "yakhnama"
    subdirectory.mkdir(parents=True)
    stdin = build_payload(repository, "../../LICENSE", working_directory=subdirectory)

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 2


def test_guard_dot_segments_resolving_to_protected_path_blocks(
    repository: Path, hook_file: Path
) -> None:
    stdin = build_payload(repository, "src/../poetry.lock")

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 2


def test_guard_symlink_to_protected_path_blocks(
    repository: Path, hook_file: Path
) -> None:
    (repository / "poetry.lock").write_text("")
    (repository / "alias.txt").symlink_to(repository / "poetry.lock")
    stdin = build_payload(repository, "alias.txt")

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 2


def test_guard_path_outside_repository_allows(
    repository: Path, hook_file: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere" / "poetry.lock"
    stdin = build_payload(repository, str(outside))

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 0


def test_guard_ordinary_file_allows_silently(
    repository: Path, hook_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stdin = build_payload(repository, "src/yakhnama/main.py")

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 0
    assert capsys.readouterr().err == ""


def test_guard_missing_file_path_allows(repository: Path, hook_file: Path) -> None:
    stdin = build_payload(repository, None)

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 0


def test_guard_missing_tool_input_allows(hook_file: Path) -> None:
    stdin = io.StringIO(json.dumps({"tool_name": "Write"}))

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 0


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("", "not valid JSON"),
        ("{not json", "not valid JSON"),
        ("[1, 2]", "not an object"),
        ('{"tool_input": {"file_path": 3}}', "does not match"),
        ('{"tool_input": {"notebook_path": []}}', "does not match"),
        ('{"agent_id": {"nested": true}}', "does not match"),
    ],
)
def test_guard_malformed_input_blocks_fail_closed(
    hook_file: Path, raw: str, message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    stdin = io.StringIO(raw)

    exit_code = main(stdin, FakeGitTracker(), hook_file)

    assert exit_code == 2
    assert message in capsys.readouterr().err


def test_guard_unexpected_exception_blocks_fail_closed(
    repository: Path, hook_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stdin = build_payload(repository, "migrations/versions/0001_init.py")

    exit_code = main(stdin, ExplodingGitTracker(), hook_file)

    assert exit_code == 2
    assert capsys.readouterr().err == (
        "guard_protected_paths: blocked: guard failed "
        "(RuntimeError: index is corrupt)\n"
    )


def test_guard_hook_outside_a_repository_blocks_fail_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    orphan_hook = tmp_path / "copy" / ".claude" / "hooks" / "guard.py"
    stdin = build_payload(tmp_path, "README.md")

    exit_code = main(stdin, FakeGitTracker(), orphan_hook)

    assert exit_code == 2
    assert "has no pyproject.toml" in capsys.readouterr().err


def test_guard_extra_payload_fields_are_ignored(
    repository: Path, hook_file: Path
) -> None:
    body = {
        "cwd": str(repository),
        "permission_mode": "default",
        "tool_use_id": "tool-1",
        "tool_input": {"file_path": "README.md", "old_string": "a", "new_string": "b"},
    }

    exit_code = main(io.StringIO(json.dumps(body)), FakeGitTracker(), hook_file)

    assert exit_code == 0


def test_decision_factories_build_expected_values() -> None:
    allowed = Decision.allow()
    blocked = Decision.block("why")

    assert allowed == Decision(allowed=True, reason=None)
    assert blocked == Decision(allowed=False, reason="why")


def test_resolve_repository_root_with_marker_returns_grandparent_of_hooks(
    repository: Path, hook_file: Path
) -> None:
    root = resolve_repository_root(hook_file)

    assert root == repository


def test_resolve_repository_root_without_marker_raises(tmp_path: Path) -> None:
    orphan_hook = tmp_path / ".claude" / "hooks" / "guard.py"

    with pytest.raises(FileNotFoundError, match=r"has no pyproject\.toml"):
        resolve_repository_root(orphan_hook)


def test_resolve_repository_root_of_installed_hook_is_this_repository() -> None:
    root = resolve_repository_root(HOOK_PATH)

    assert (root / ".claude" / "hooks" / HOOK_PATH.name).is_file()


def test_guard_script_entry_point_exits_with_main_code(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The tmp repository lies outside the real one, so the real guard allows it
    # without consulting git.
    monkeypatch.setattr(sys, "stdin", build_payload(repository, "README.md"))

    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(HOOK_PATH), run_name="__main__")

    assert raised.value.code == 0


# --------------------------------------------------------------------------- #
# Property tests                                                              #
# --------------------------------------------------------------------------- #

PROPERTY_ROOT = Path(__file__).resolve().parents[3]
SEGMENT = st.one_of(
    st.sampled_from([".claude", "hooks", "settings.json", ".github", "migrations"]),
    st.text(
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-",
        min_size=1,
        max_size=12,
    ),
).filter(lambda segment: segment not in {".", ".."})
RELATIVE_PATH = st.lists(SEGMENT, min_size=1, max_size=4).map("/".join)


def is_protected(relative: str, *, is_subagent: bool) -> bool:
    """Oracle written independently of the hook: does a protected rule match?

    Args:
        relative: Repository-relative POSIX path.
        is_subagent: Whether the call comes from a subagent.

    Returns:
        True when the path falls under any rule of ``AGENTS.md`` §5.
    """
    folded = relative.casefold()
    name = folded.rsplit("/", 1)[-1]
    if name != ".env.example" and (name == ".env" or name.startswith(".env.")):
        return True
    if folded in {"poetry.lock", "license", ".github/codeowners"}:
        return True
    if folded.startswith("migrations/versions/"):
        return True
    lead_only = folded in {"agents.md", ".claude"} or folded.startswith(".claude/")
    return lead_only and is_subagent


@given(relative=RELATIVE_PATH, is_subagent=st.booleans())
def test_guard_arbitrary_path_matches_oracle(
    relative: str, *, is_subagent: bool
) -> None:
    # Every migration counts as tracked so the oracle alone decides the outcome.
    hook_input = HookInput(
        working_directory=str(PROPERTY_ROOT),
        tool_input=ToolInput(file_path=relative),
        agent_id="agent-1" if is_subagent else None,
    )
    tracker = FakeGitTracker(frozenset({relative}))

    decision = ProtectedPathPolicy(PROPERTY_ROOT, tracker).evaluate(hook_input)

    assert decision.allowed != is_protected(relative, is_subagent=is_subagent)


@given(suffix=SEGMENT.filter(lambda segment: segment.casefold() != "example"))
def test_guard_env_variant_property_blocks(suffix: str) -> None:
    hook_input = HookInput(tool_input=ToolInput(file_path=f".env.{suffix}"))

    decision = ProtectedPathPolicy(PROPERTY_ROOT, FakeGitTracker()).evaluate(hook_input)

    assert not decision.allowed
