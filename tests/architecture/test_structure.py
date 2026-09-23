"""Structural rules that ``import-linter`` cannot express (``AGENTS.md`` §4.2)."""

import ast
import re
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src" / "yakhnama"
HOOKS_ROOT = REPOSITORY_ROOT / ".claude" / "hooks"
SHARED_KERNEL = SOURCE_ROOT / "shared_kernel"
# Third-party packages the shared kernel may use; everything else outside the standard
# library is a framework dependency (AGENTS.md §2.1).
SHARED_KERNEL_ALLOWED_THIRD_PARTY = frozenset({"pydantic", "geojson_pydantic"})

# Mirrors the "Pattern" column of AGENTS.md §3, one name per pattern, plus "Settings"
# and "API Schema" proposed in docs/adr/0011. Change it only together with §3.
CATALOG_PATTERNS = frozenset(
    {
        "Repository",
        "Unit of Work",
        "Command Handler",
        "Query Service",
        "Composition Root",
        "Dependency Injection",
        "Facade",
        "Value Object",
        "Entity",
        "Aggregate Root",
        "Strategy",
        "Registry",
        "State",
        "Chain of Responsibility",
        "Factory",
        "Specification",
        "Domain Events",
        "Transactional Outbox",
        "Observer",
        "Adapter",
        "Anti-Corruption Layer",
        "Template Method",
        "Policy",
        "Decorator",
        "Fake",
        "Settings",
        "API Schema",
        "Command",
        "Query",
        "DTO",
        "Domain Error",
    }
)
_IMPLEMENTS_LINE = re.compile(r"Implements:(?P<declaration>[^\n]*)")
# Qualifiers such as "(port side)" refine a pattern without naming another one, the
# same way the catalog itself writes "Repository (Protocol port, SQLAlchemy adapter)".
_QUALIFIER = re.compile(r"\([^)]*\)")
# "," separates several patterns; "+" and "/" are the catalog's own combinators
# ("Strategy + Registry", "Entity / Aggregate Root").
_PATTERN_SEPARATOR = re.compile(r"[,+/]")


def _python_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _relative(path: Path) -> str:
    return path.relative_to(SOURCE_ROOT.parent).as_posix()


def _from_repository_root(path: Path) -> str:
    return path.relative_to(REPOSITORY_ROOT).as_posix()


def _pattern_checked_files() -> list[Path]:
    return [*_python_files(SOURCE_ROOT), *sorted(HOOKS_ROOT.glob("*.py"))]


def _declared_patterns(docstring: str) -> list[str] | None:
    """Return the patterns after ``Implements:``, or ``None`` if there is no line.

    The declaration ends at the first full stop, so an explanation may follow it on
    the same line.
    """
    match = _IMPLEMENTS_LINE.search(docstring)
    if match is None:
        return None
    declaration = _QUALIFIER.sub("", match["declaration"]).split(".", maxsplit=1)[0]
    return [
        pattern.strip()
        for pattern in _PATTERN_SEPARATOR.split(declaration)
        if pattern.strip()
    ]


def _pattern_offence(docstring: str) -> str | None:
    patterns = _declared_patterns(docstring)
    if patterns is None:
        return "no 'Implements:' line"
    if not patterns:
        return "empty 'Implements:' line"
    unknown = [pattern for pattern in patterns if pattern not in CATALOG_PATTERNS]
    return f"not in the catalog: {unknown}" if unknown else None


def _package_directories() -> list[Path]:
    return sorted(
        path
        for path in [SOURCE_ROOT, *SOURCE_ROOT.rglob("*")]
        if path.is_dir()
        and path.name != "__pycache__"
        and not path.name.startswith(".")
    )


def _imported_modules(tree: ast.Module) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return modules


def _is_allowed_in_shared_kernel(module: str) -> bool:
    top_level = module.split(".", maxsplit=1)[0]
    return (
        module == "yakhnama.shared_kernel"
        or module.startswith("yakhnama.shared_kernel.")
        or top_level in sys.stdlib_module_names
        or top_level in SHARED_KERNEL_ALLOWED_THIRD_PARTY
    )


def test_source_root_resolved_from_test_file_is_existing_directory() -> None:
    is_directory = SOURCE_ROOT.is_dir()

    assert is_directory


def test_hooks_root_resolved_from_test_file_contains_python_files() -> None:
    hook_files = sorted(HOOKS_ROOT.glob("*.py"))

    assert hook_files


@pytest.mark.parametrize("package", _package_directories(), ids=_relative)
def test_package_has_init_with_module_docstring(package: Path) -> None:
    init = package / "__init__.py"

    docstring = ast.get_docstring(_parse(init)) if init.is_file() else None

    assert docstring, f"{_relative(package)} needs __init__.py with a docstring"


def test_every_class_docstring_declares_catalog_pattern() -> None:
    offenders = [
        f"{_from_repository_root(path)}:{node.lineno} {node.name}: {offence}"
        for path in _pattern_checked_files()
        for node in ast.walk(_parse(path))
        if isinstance(node, ast.ClassDef)
        and (offence := _pattern_offence(ast.get_docstring(node) or "")) is not None
    ]

    assert offenders == []


@pytest.mark.parametrize(
    ("docstring", "expected"),
    [
        ("Summary.\n\nImplements: Value Object", ["Value Object"]),
        ("Implements: Adapter (port side)", ["Adapter"]),
        ("Implements: Settings (pydantic-settings). Why it exists.", ["Settings"]),
        ("Implements: Facade, Decorator", ["Facade", "Decorator"]),
        ("Implements: Strategy + Registry", ["Strategy", "Registry"]),
        ("Implements: Entity / Aggregate Root", ["Entity", "Aggregate Root"]),
        ("Implements:", []),
        ("A class without a declaration.", None),
    ],
)
def test_declared_patterns_docstring_variants_are_parsed(
    docstring: str, expected: list[str] | None
) -> None:
    result = _declared_patterns(docstring)

    assert result == expected


@pytest.mark.parametrize(
    ("docstring", "expected"),
    [
        ("Implements: Repository", None),
        ("Implements: API Schema, Value Object", None),
        ("Implements: Helper (API response schema)", "not in the catalog: ['Helper']"),
        ("Implements: Facade, Singleton", "not in the catalog: ['Singleton']"),
        ("Implements: (qualifier only)", "empty 'Implements:' line"),
        ("No declaration here.", "no 'Implements:' line"),
    ],
)
def test_pattern_offence_docstring_variants_are_classified(
    docstring: str, expected: str | None
) -> None:
    result = _pattern_offence(docstring)

    assert result == expected


def test_create_app_is_defined_only_in_main() -> None:
    definitions = [
        _relative(path)
        for path in _python_files(SOURCE_ROOT)
        for node in ast.walk(_parse(path))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == "create_app"
    ]

    assert definitions == ["yakhnama/main.py"]


def test_fastapi_application_is_constructed_only_in_main() -> None:
    constructions = [
        _relative(path)
        for path in _python_files(SOURCE_ROOT)
        for node in ast.walk(_parse(path))
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "FastAPI")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "FastAPI")
        )
    ]

    assert constructions == ["yakhnama/main.py"]


def test_shared_kernel_imports_only_stdlib_pydantic_and_itself() -> None:
    offenders = [
        f"{_relative(path)} imports {module}"
        for path in _python_files(SHARED_KERNEL)
        for module in _imported_modules(_parse(path))
        if not _is_allowed_in_shared_kernel(module)
    ]

    assert offenders == []


@pytest.mark.parametrize(
    ("module", "is_allowed"),
    [
        ("datetime", True),
        ("pydantic.fields", True),
        ("yakhnama.shared_kernel.errors", True),
        ("yakhnama.platform.settings", False),
        ("fastapi", False),
        ("sqlalchemy.orm", False),
    ],
)
def test_shared_kernel_import_rule_classifies_modules(
    module: str, *, is_allowed: bool
) -> None:
    result = _is_allowed_in_shared_kernel(module)

    assert result is is_allowed
