"""Structural rules that ``import-linter`` cannot express (``AGENTS.md`` §4.2)."""

import ast
import importlib
import inspect
import re
import sys
import typing
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src" / "yakhnama"
HOOKS_ROOT = REPOSITORY_ROOT / ".claude" / "hooks"
SHARED_KERNEL = SOURCE_ROOT / "shared_kernel"
MODULES_ROOT = SOURCE_ROOT / "modules"
# Adapter classes are named after the port they implement plus this technology prefix
# (``SqlAlchemyPlaceRepository`` implements ``PlaceRepository``).
REPOSITORY_ADAPTER_PREFIX = "SqlAlchemy"
# Third-party packages the shared kernel may use; everything else outside the standard
# library is a framework dependency (AGENTS.md §2.1).
SHARED_KERNEL_ALLOWED_THIRD_PARTY = frozenset({"pydantic", "geojson_pydantic"})

# Mirrors the "Pattern" column of AGENTS.md §3, one name per pattern, plus "Settings"
# and "API Schema" (ADR 0011) and "Command", "Query", "DTO", "Domain Error" (ADR 0012).
# Change it only together with §3.
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


def _module_directories() -> list[Path]:
    return sorted(
        path
        for path in MODULES_ROOT.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    )


def _module_name(module_directory: Path) -> str:
    return module_directory.name


def _repository_adapters() -> list[tuple[str, type]]:
    adapters: list[tuple[str, type]] = []
    for module_directory in _module_directories():
        if not (module_directory / "infrastructure" / "repositories.py").is_file():
            continue
        name = module_directory.name
        repositories = importlib.import_module(
            f"yakhnama.modules.{name}.infrastructure.repositories"
        )
        adapters.extend(
            (name, member)
            for member_name, member in inspect.getmembers(repositories, inspect.isclass)
            if member.__module__ == repositories.__name__
            and member_name.startswith(REPOSITORY_ADAPTER_PREFIX)
            and member_name.endswith("Repository")
        )
    return adapters


def _adapter_id(adapter: tuple[str, type]) -> str:
    return f"{adapter[0]}.{adapter[1].__name__}"


def _port_offence(module_name: str, adapter: type) -> str | None:
    """Return why ``adapter`` does not implement its port, or ``None`` if it does."""
    ports = importlib.import_module(f"yakhnama.modules.{module_name}.application.ports")
    port_name = adapter.__name__.removeprefix(REPOSITORY_ADAPTER_PREFIX)
    port = getattr(ports, port_name, None)
    if port is None or not typing.is_protocol(port):
        return f"application/ports.py defines no Protocol named {port_name}"
    # Protocols are not runtime_checkable (ports stay plain), so compare the member
    # sets; mypy checks the signatures wherever the adapter is bound to the port.
    missing = sorted(
        member
        for member in typing.get_protocol_members(port)
        if not hasattr(adapter, member)
    )
    return f"{adapter.__name__} lacks {missing} of {port_name}" if missing else None


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


def test_modules_root_contains_modules() -> None:
    modules = _module_directories()

    assert modules


@pytest.mark.parametrize("module_directory", _module_directories(), ids=_module_name)
def test_every_module_has_a_public_facade(module_directory: Path) -> None:
    facade = module_directory / "public.py"

    docstring = ast.get_docstring(_parse(facade)) if facade.is_file() else None

    assert docstring, f"{_relative(module_directory)} needs public.py with a docstring"


def test_repository_adapters_are_discovered() -> None:
    modules_with_repositories = [
        directory
        for directory in _module_directories()
        if (directory / "infrastructure" / "repositories.py").is_file()
    ]

    adapters = _repository_adapters()

    # A module that has no persistence yet (a fresh skeleton) contributes nothing.
    assert len(adapters) >= len(modules_with_repositories)
    assert modules_with_repositories, "at least one module must have repositories"


@pytest.mark.parametrize("adapter", _repository_adapters(), ids=_adapter_id)
def test_every_repository_class_implements_a_port(adapter: tuple[str, type]) -> None:
    module_name, adapter_class = adapter

    offence = _port_offence(module_name, adapter_class)

    assert offence is None


def test_port_offence_class_without_matching_port_is_reported() -> None:
    class SqlAlchemyNothingRepository:
        """A repository adapter with no port.

        Implements: Fake (of Repository).
        """

    offence = _port_offence("geography", SqlAlchemyNothingRepository)

    assert offence == "application/ports.py defines no Protocol named NothingRepository"


def test_port_offence_class_missing_port_members_is_reported() -> None:
    class SqlAlchemyPlaceRepository:
        """A repository adapter implementing none of its port.

        Implements: Fake (of Repository).
        """

    offence = _port_offence("geography", SqlAlchemyPlaceRepository)

    assert offence is not None
    assert offence.startswith("SqlAlchemyPlaceRepository lacks [")
