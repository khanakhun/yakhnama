"""Claude Code PreToolUse hook that blocks edits to protected repository paths.

The hook reads the tool call Claude Code is about to make from stdin, resolves the
target file against the repository root and evaluates a set of composable deny rules.
Exit code 2 blocks the call and feeds the one-line reason on stderr back to the model;
exit code 0 allows it. Every failure fails closed (exit 2): malformed input, a missing
repository marker and any unexpected exception, because Claude Code lets a call through
on every exit code other than 2 and a guard that passes when it cannot decide is a
weakened check. ``.claude/settings.json`` adds ``|| exit 2`` for the failures that
happen before Python runs (``cd`` or ``poetry`` failing).

The repository root is the directory two levels above this file, never the payload's
``cwd``: the payload is written by the session being guarded and must not choose the
root the rules are evaluated against. ``cwd`` only anchors a relative ``file_path``.

Protected paths (``AGENTS.md`` §5):

* ``.env`` and ``.env.*`` anywhere in the repository, except ``.env.example``. The
  example file holds no secrets; it documents the expected variables and must stay
  editable.
* ``poetry.lock``: changes only through ``poetry add`` / ``poetry remove``.
* ``LICENSE`` and ``.github/CODEOWNERS``: maintainer-only.
* Files under ``migrations/versions/`` that git already tracks: committed migrations
  are history; corrections are new migrations.
* ``AGENTS.md`` and everything under ``.claude/`` when the call comes from a subagent.
  Claude Code adds ``agent_id`` to the hook input only inside a subagent, which makes
  it the lead-versus-subagent signal. ``.claude/`` holds the guard itself (hooks,
  ``settings.json``), ``settings.local.json`` (which outranks project settings and can
  set ``disableAllHooks``) and the agent definitions, so a subagent must not be able to
  weaken any of it.
* The user-level ``~/.claude/settings.json`` and ``~/.claude/settings.local.json`` when
  the call comes from a subagent. They lie outside the repository but apply to every
  project, so this rule runs on the absolute path before the outside-repository skip.

Residual risk: the hook sees only ``Edit``, ``Write``, ``MultiEdit`` and
``NotebookEdit``. A ``Bash`` command (``sed -i``, ``>`` redirection, ``git checkout``)
can still write any file; parsing shell to close that gap would be unreliable. The
backstops are the ``permissions.deny`` list in ``.claude/settings.json``, the gitleaks
pre-commit hook, CI and ``.github/CODEOWNERS`` review.

Patterns: Policy
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import IO, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

HOOK_NAME = "guard_protected_paths"
HOOK_FILE = Path(__file__)
EXIT_ALLOW = 0
EXIT_BLOCK = 2
REPOSITORY_MARKER = "pyproject.toml"
GIT_TIMEOUT_SECONDS = 10
GIT_UNTRACKED_STATUS = 1
USER_SETTINGS_NAMES = ("settings.json", "settings.local.json")


class ToolInput(BaseModel):
    """The subset of a tool call's arguments the guard needs.

    Implements: Value Object

    Attributes:
        file_path: Target file of ``Edit``, ``Write`` or ``MultiEdit``; absolute or
            relative to the session working directory.
        notebook_path: Target notebook of ``NotebookEdit``; same resolution rules.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    file_path: str | None = None
    notebook_path: str | None = None

    @property
    def target_paths(self) -> tuple[str, ...]:
        """Every non-empty path the call targets.

        Both fields are checked when both are present, so a payload cannot hide a
        protected path in the field the guard would otherwise ignore.

        Returns:
            The given paths, ``file_path`` first.
        """
        return tuple(path for path in (self.file_path, self.notebook_path) if path)


class HookInput(BaseModel):
    """The PreToolUse payload Claude Code writes to the hook's stdin.

    Implements: Value Object

    Attributes:
        working_directory: Working directory of the session when the hook fired;
            the payload key is ``cwd``.
        tool_name: Name of the tool about to run.
        tool_input: Arguments of the tool call.
        agent_id: Present only when the call is made inside a subagent.
        agent_type: Subagent name, present together with ``agent_id``.
    """

    model_config = ConfigDict(
        frozen=True, extra="ignore", validate_by_alias=True, validate_by_name=True
    )

    working_directory: str | None = Field(default=None, alias="cwd")
    tool_name: str | None = None
    tool_input: ToolInput = ToolInput()
    agent_id: str | None = None
    agent_type: str | None = None

    @property
    def is_subagent(self) -> bool:
        """Whether the tool call originates from a subagent.

        Returns:
            True when ``agent_id`` is present in the payload.
        """
        return self.agent_id is not None


class Decision(BaseModel):
    """Outcome of evaluating the guard against one tool call.

    Implements: Value Object

    Attributes:
        allowed: Whether the tool call may proceed.
        reason: Why the call was blocked; None when allowed.
    """

    model_config = ConfigDict(frozen=True)

    allowed: bool
    reason: str | None = None

    @classmethod
    def allow(cls) -> Decision:
        """Build an allowing decision.

        Returns:
            A decision with ``allowed`` set and no reason.
        """
        return cls(allowed=True)

    @classmethod
    def block(cls, reason: str) -> Decision:
        """Build a blocking decision.

        Args:
            reason: One-line explanation shown to the model.

        Returns:
            A decision with ``allowed`` unset and the given reason.
        """
        return cls(allowed=False, reason=reason)


class TargetPath(BaseModel):
    """The file a tool call targets, located relative to the repository root.

    Implements: Value Object

    Attributes:
        repository_root: Absolute, resolved repository root.
        relative: Target path relative to the root, as POSIX parts.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    repository_root: Path
    relative: PurePosixPath

    @property
    def normalised(self) -> PurePosixPath:
        """Case-folded relative path.

        Case-insensitive file systems (macOS, Windows) would otherwise let
        ``Poetry.LOCK`` slip past a rule written for ``poetry.lock``.

        Returns:
            The relative path with every part case-folded.
        """
        return PurePosixPath(*(part.casefold() for part in self.relative.parts))


class GitTracker(Protocol):
    """Port answering whether git tracks a file.

    Implements: Adapter (port side)
    """

    def is_tracked(self, repository_root: Path, relative: PurePosixPath) -> bool:
        """Tell whether ``relative`` is tracked in the repository at the root.

        Args:
            repository_root: Absolute repository root.
            relative: Path relative to the root.

        Returns:
            True when git tracks the file.
        """
        ...


class GitCliAdapter:
    """GitTracker backed by ``git ls-files --error-unmatch``.

    Implements: Adapter
    """

    def is_tracked(self, repository_root: Path, relative: PurePosixPath) -> bool:
        """Ask git whether the file is in the index.

        Only exit status 1 (git ran and the path is not in the index) means
        "untracked". Everything else is "tracked": status 128 is a fatal error (not a
        repository, ``safe.directory`` refusal), and a missing git or a hung git
        (timeout) cannot prove the migration is new either. The guard fails closed
        rather than allowing a history edit.

        Args:
            repository_root: Absolute repository root, used as git's working directory.
            relative: Path relative to the root.

        Returns:
            True unless git positively reports the file as untracked.
        """
        try:
            completed = subprocess.run(  # noqa: S603  # reason: arguments are a fixed list plus a repository path, no shell
                ["git", "ls-files", "--error-unmatch", "--", relative.as_posix()],  # noqa: S607  # reason: git is resolved from PATH like every developer tool
                cwd=repository_root,
                capture_output=True,
                check=False,
                timeout=GIT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired):
            return True
        return completed.returncode != GIT_UNTRACKED_STATUS


class PathPolicy(Protocol):
    """One composable deny rule of the protected-path policy.

    Implements: Policy
    """

    def check(self, target: TargetPath, hook_input: HookInput) -> str | None:
        """Evaluate the rule.

        Args:
            target: The resolved target file.
            hook_input: The full hook payload.

        Returns:
            The block reason when the rule denies the call, otherwise None.
        """
        ...


class EnvFilePolicy:
    """Deny ``.env`` and ``.env.*`` files, except ``.env.example``.

    ``.env.example`` is documentation (variable names, no values) and must stay
    editable.

    Implements: Policy
    """

    EXEMPT_NAME = ".env.example"

    def check(self, target: TargetPath, hook_input: HookInput) -> str | None:
        """Evaluate the rule on the file name.

        Args:
            target: The resolved target file.
            hook_input: The full hook payload (unused).

        Returns:
            The block reason for environment files, otherwise None.
        """
        name = target.normalised.name
        if name == self.EXEMPT_NAME:
            return None
        if name == ".env" or name.startswith(".env."):
            return f"{target.relative} is an environment file and may hold secrets"
        return None


class FixedPathPolicy:
    """Deny one exact repository-relative path.

    Implements: Policy

    Attributes:
        path: The protected path, lower-case POSIX.
        reason: Why it is protected.
    """

    def __init__(self, path: str, reason: str) -> None:
        """Create the rule.

        Args:
            path: Repository-relative POSIX path.
            reason: Why the path is protected.
        """
        self.path = PurePosixPath(path.casefold())
        self.reason = reason

    def check(self, target: TargetPath, hook_input: HookInput) -> str | None:
        """Evaluate the rule.

        Args:
            target: The resolved target file.
            hook_input: The full hook payload (unused).

        Returns:
            The block reason when the target is the protected path, otherwise None.
        """
        if target.normalised == self.path:
            return f"{target.relative} {self.reason}"
        return None


class CommittedMigrationPolicy:
    """Deny edits to migration revisions that git already tracks.

    Implements: Policy

    Attributes:
        tracker: Port telling whether a file is tracked.
    """

    DIRECTORY = PurePosixPath("migrations/versions")

    def __init__(self, tracker: GitTracker) -> None:
        """Create the rule.

        Args:
            tracker: Port telling whether a file is tracked.
        """
        self.tracker = tracker

    def check(self, target: TargetPath, hook_input: HookInput) -> str | None:
        """Evaluate the rule.

        Args:
            target: The resolved target file.
            hook_input: The full hook payload (unused).

        Returns:
            The block reason for a tracked migration, otherwise None.
        """
        if not target.normalised.is_relative_to(self.DIRECTORY):
            return None
        if target.normalised == self.DIRECTORY:
            return None
        if self.tracker.is_tracked(target.repository_root, target.relative):
            return (
                f"{target.relative} is a committed migration; "
                "write a new revision instead"
            )
        return None


def describe_subagent(hook_input: HookInput) -> str:
    """Name the subagent behind a call for a block reason.

    Args:
        hook_input: The hook payload of a subagent call.

    Returns:
        The agent type, or the agent id when the type is absent.
    """
    return hook_input.agent_type or hook_input.agent_id or "a subagent"


class LeadOnlyPathPolicy:
    """Deny edits to one exact path when they come from a subagent.

    Implements: Policy

    Attributes:
        path: The lead-only path, lower-case POSIX.
    """

    def __init__(self, path: str) -> None:
        """Create the rule.

        Args:
            path: Repository-relative POSIX path.
        """
        self.path = PurePosixPath(path.casefold())

    def check(self, target: TargetPath, hook_input: HookInput) -> str | None:
        """Evaluate the rule.

        Args:
            target: The resolved target file.
            hook_input: The full hook payload, whose ``agent_id`` marks a subagent.

        Returns:
            The block reason for a subagent edit of the path, otherwise None.
        """
        if target.normalised == self.path and hook_input.is_subagent:
            agent = describe_subagent(hook_input)
            return f"{target.relative} is edited only by the lead session, not {agent}"
        return None


class LeadOnlyDirectoryPolicy:
    """Deny edits to anything inside a directory when they come from a subagent.

    Implements: Policy

    Attributes:
        directory: The lead-only directory, lower-case POSIX.
    """

    def __init__(self, directory: str) -> None:
        """Create the rule.

        Args:
            directory: Repository-relative POSIX directory.
        """
        self.directory = PurePosixPath(directory.casefold())

    def check(self, target: TargetPath, hook_input: HookInput) -> str | None:
        """Evaluate the rule.

        Args:
            target: The resolved target file.
            hook_input: The full hook payload, whose ``agent_id`` marks a subagent.

        Returns:
            The block reason for a subagent edit inside the directory (or of the
            directory entry itself), otherwise None.
        """
        if target.normalised.is_relative_to(self.directory) and hook_input.is_subagent:
            agent = describe_subagent(hook_input)
            return (
                f"{target.relative} is under {self.directory}/, which is edited only "
                f"by the lead session, not {agent}"
            )
        return None


class UserSettingsPolicy:
    """Deny subagent edits of the user-level Claude Code settings files.

    Implements: Policy

    Attributes:
        protected: The resolved, case-folded absolute paths of
            ``~/.claude/settings.json`` and ``~/.claude/settings.local.json``.
    """

    def __init__(self, home: Path) -> None:
        """Create the rule.

        Args:
            home: The user's home directory.
        """
        directory = (home / ".claude").resolve()
        self.protected = frozenset(
            (directory / name).resolve().as_posix().casefold()
            for name in USER_SETTINGS_NAMES
        )

    def check(self, absolute: Path, hook_input: HookInput) -> str | None:
        """Evaluate the rule on the resolved absolute target.

        The comparison is case-folded so a case-insensitive file system cannot be
        used to slip past it.

        Args:
            absolute: The resolved absolute target path.
            hook_input: The full hook payload, whose ``agent_id`` marks a subagent.

        Returns:
            The block reason for a subagent edit of a user settings file, otherwise
            None.
        """
        if not hook_input.is_subagent:
            return None
        if absolute.as_posix().casefold() not in self.protected:
            return None
        agent = describe_subagent(hook_input)
        return (
            f"{absolute} is a user-level Claude Code settings file, edited only by "
            f"the lead session, not {agent}"
        )


class ProtectedPathPolicy:
    """The composite of every protected-path rule; the first denial wins.

    Implements: Policy

    Attributes:
        repository_root: Absolute, resolved repository root the rules describe.
        rules: The repository deny rules, evaluated in order.
        user_settings_policy: The rule for user-level settings outside the
            repository, evaluated first.
    """

    def __init__(
        self,
        repository_root: Path,
        tracker: GitTracker | None = None,
        home: Path | None = None,
    ) -> None:
        """Assemble the rules of ``AGENTS.md`` §5.

        Args:
            repository_root: Absolute repository root the rules describe.
            tracker: Port for git tracking; defaults to the git CLI.
            home: The user's home directory; defaults to :meth:`Path.home`.
        """
        self.repository_root = repository_root.resolve()
        self.rules: tuple[PathPolicy, ...] = (
            EnvFilePolicy(),
            FixedPathPolicy(
                "poetry.lock", "changes only through poetry add / poetry remove"
            ),
            FixedPathPolicy("LICENSE", "is maintainer-only"),
            FixedPathPolicy(".github/CODEOWNERS", "is maintainer-only"),
            CommittedMigrationPolicy(tracker or GitCliAdapter()),
            LeadOnlyPathPolicy("AGENTS.md"),
            LeadOnlyDirectoryPolicy(".claude"),
        )
        self.user_settings_policy = UserSettingsPolicy(home or Path.home())

    def evaluate(self, hook_input: HookInput) -> Decision:
        """Decide whether the tool call may proceed.

        Args:
            hook_input: The parsed hook payload.

        Returns:
            An allowing decision when no file is targeted, or when no rule denies
            any of them (files outside the repository meet only the user-settings
            rule); otherwise a blocking decision for the first denial.
        """
        for raw_path in hook_input.tool_input.target_paths:
            absolute = resolve_target(
                raw_path, hook_input.working_directory, self.repository_root
            )
            reason = self.user_settings_policy.check(absolute, hook_input)
            if reason is not None:
                return Decision.block(reason)
            target = locate_target(absolute, self.repository_root)
            if target is None:
                continue
            for rule in self.rules:
                reason = rule.check(target, hook_input)
                if reason is not None:
                    return Decision.block(reason)
        return Decision.allow()


def resolve_repository_root(hook_file: Path) -> Path:
    """Derive the repository root from the location of the hook file.

    Args:
        hook_file: Path of this module, ``<root>/.claude/hooks/<name>.py``.

    Returns:
        The absolute, resolved repository root.

    Raises:
        FileNotFoundError: If the derived root holds no ``pyproject.toml``, which
            means the hook was copied or moved and cannot trust its own location.
    """
    repository_root = hook_file.resolve().parents[2]
    if not (repository_root / REPOSITORY_MARKER).is_file():
        message = f"{repository_root} has no {REPOSITORY_MARKER}; cannot locate root"
        raise FileNotFoundError(message)
    return repository_root


def resolve_target(
    raw_path: str, working_directory: str | None, repository_root: Path
) -> Path:
    """Resolve one tool-call path to an absolute path.

    A relative path is anchored at the payload's ``cwd`` when given (that is how
    Claude Code resolves it), otherwise at the repository root.

    Args:
        raw_path: ``file_path`` or ``notebook_path`` from the payload.
        working_directory: The payload's session working directory, if any.
        repository_root: Absolute, resolved repository root.

    Returns:
        The resolved absolute path, with symlinks and dot segments removed.
    """
    anchor = (
        repository_root / working_directory if working_directory else repository_root
    )
    return (anchor / raw_path).resolve()


def locate_target(absolute: Path, repository_root: Path) -> TargetPath | None:
    """Locate a resolved absolute path relative to the repository root.

    Args:
        absolute: The resolved absolute target path.
        repository_root: Absolute, resolved repository root.

    Returns:
        The target relative to the repository root, or None when the file lies
        outside the repository (the repository rules describe this repository only).
    """
    if not absolute.is_relative_to(repository_root):
        return None
    relative = PurePosixPath(absolute.relative_to(repository_root).as_posix())
    return TargetPath(repository_root=repository_root, relative=relative)


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


def main(
    stdin: IO[str] = sys.stdin,
    tracker: GitTracker | None = None,
    hook_file: Path = HOOK_FILE,
    home: Path | None = None,
) -> int:
    """Run the guard on one PreToolUse payload.

    Args:
        stdin: Stream carrying the hook payload.
        tracker: Port for git tracking; defaults to the git CLI.
        hook_file: Location the repository root is derived from; defaults to this
            module, injectable for tests.
        home: The user's home directory; defaults to :meth:`Path.home`.

    Returns:
        ``0`` to allow the tool call, ``2`` to block it (including on any failure).
    """
    try:
        repository_root = resolve_repository_root(hook_file)
        hook_input = parse_hook_input(stdin.read())
        policy = ProtectedPathPolicy(repository_root, tracker, home)
        decision = policy.evaluate(hook_input)
    except ValueError as error:
        sys.stderr.write(f"{HOOK_NAME}: blocked: {error}\n")
        return EXIT_BLOCK
    except Exception as error:  # noqa: BLE001  # reason: hook boundary must fail closed
        sys.stderr.write(
            f"{HOOK_NAME}: blocked: guard failed ({type(error).__name__}: {error})\n"
        )
        return EXIT_BLOCK
    if decision.allowed:
        return EXIT_ALLOW
    sys.stderr.write(f"{HOOK_NAME}: blocked: {decision.reason}\n")
    return EXIT_BLOCK


if __name__ == "__main__":
    sys.exit(main())
