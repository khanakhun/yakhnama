"""Unit tests for ``LocalCsvSourceAdapter`` and ``FixturePathResolver``.

The adapter reads committed fixture files; the symbolic-link test writes only inside
pytest's ``tmp_path``. No network is touched.
"""

from pathlib import Path

import pytest

from tests.fakes.clock import FrozenClock
from tests.unit.modules.ingestion.application.support import NOW, make_version
from tests.unit.modules.ingestion.infrastructure.adapters.support import (
    FIXTURES_DIR,
    SAMPLE_FILE,
    make_fixture_dataset,
    sha256_of,
)
from yakhnama.modules.ingestion.infrastructure.adapters.local_csv import (
    CSV_MEDIA_TYPE,
    FixturePathResolver,
    LocalCsvSourceAdapter,
)
from yakhnama.modules.ingestion.infrastructure.adapters.reference import (
    TEMPERATURE_SAMPLE_DATASET,
)
from yakhnama.shared_kernel.errors import NotFoundError, ValidationError

SAMPLE_FILES = {TEMPERATURE_SAMPLE_DATASET: SAMPLE_FILE}


def make_adapter(
    root: Path = FIXTURES_DIR,
    files: dict[str, str] | None = None,
    *,
    max_bytes: int = 1024 * 1024,
) -> LocalCsvSourceAdapter:
    return LocalCsvSourceAdapter(
        "local_csv_test",
        FixturePathResolver(root, SAMPLE_FILES if files is None else files),
        clock=FrozenClock(NOW),
        max_bytes=max_bytes,
    )


async def test_local_csv_fetch_of_mapped_dataset_returns_bytes_with_sha256() -> None:
    adapter = make_adapter()
    dataset = make_fixture_dataset()
    version = make_version(dataset, b"")

    payload = await adapter.fetch(dataset, version)

    assert payload.content == (FIXTURES_DIR / SAMPLE_FILE).read_bytes()
    assert payload.checksum == sha256_of(FIXTURES_DIR / SAMPLE_FILE)
    assert payload.media_type == CSV_MEDIA_TYPE
    assert payload.retrieved_at == NOW


def test_local_csv_name_is_the_configured_registry_name() -> None:
    adapter = make_adapter()

    result = adapter.name

    assert result == "local_csv_test"


async def test_local_csv_fetch_of_unmapped_dataset_raises_not_found() -> None:
    adapter = make_adapter(files={})
    dataset = make_fixture_dataset()

    with pytest.raises(NotFoundError):
        await adapter.fetch(dataset, make_version(dataset, b""))


async def test_local_csv_fetch_of_file_over_limit_raises_validation_error() -> None:
    adapter = make_adapter(max_bytes=64)
    dataset = make_fixture_dataset()

    with pytest.raises(ValidationError) as caught:
        await adapter.fetch(dataset, make_version(dataset, b""))

    assert caught.value.details == {"max_bytes": 64}


async def test_local_csv_fetch_of_file_exactly_at_limit_returns_it(
    tmp_path: Path,
) -> None:
    (tmp_path / "exact.csv").write_bytes(b"a" * 8)
    adapter = make_adapter(
        tmp_path, {TEMPERATURE_SAMPLE_DATASET: "exact.csv"}, max_bytes=8
    )
    dataset = make_fixture_dataset()

    payload = await adapter.fetch(dataset, make_version(dataset, b""))

    assert payload.content == b"a" * 8


@pytest.mark.parametrize(
    "relative_path",
    [
        "../temperature_sample.csv",
        "../../etc/passwd",
        "/etc/passwd",
        "nested/../../escape.csv",
        "./temperature_sample.csv",
        "nested//file.csv",
        "nested\\..\\escape.csv",
        "",
    ],
)
def test_fixture_path_resolver_with_traversal_path_raises_validation_error(
    relative_path: str,
) -> None:
    with pytest.raises(ValidationError) as caught:
        FixturePathResolver(FIXTURES_DIR, {TEMPERATURE_SAMPLE_DATASET: relative_path})

    assert caught.value.details == {"field": "relative_path"}


def test_fixture_path_resolver_with_symlink_out_of_root_raises_validation_error(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fixtures"
    root.mkdir()
    outside = tmp_path / "secret.csv"
    outside.write_text("secret\n", encoding="utf-8")
    (root / "link.csv").symlink_to(outside)
    resolver = FixturePathResolver(root, {TEMPERATURE_SAMPLE_DATASET: "link.csv"})

    with pytest.raises(ValidationError) as caught:
        resolver.resolve(TEMPERATURE_SAMPLE_DATASET)

    assert caught.value.details == {"dataset_code": TEMPERATURE_SAMPLE_DATASET}


def test_fixture_path_resolver_with_nested_path_resolves_inside_root(
    tmp_path: Path,
) -> None:
    resolver = FixturePathResolver(tmp_path, {TEMPERATURE_SAMPLE_DATASET: "a/b.csv"})

    result = resolver.resolve(TEMPERATURE_SAMPLE_DATASET)

    assert result == tmp_path.resolve() / "a" / "b.csv"


def test_fixture_path_resolver_dataset_codes_are_sorted() -> None:
    resolver = FixturePathResolver(FIXTURES_DIR, {"b.two": "b.csv", "a.one": "a.csv"})

    result = resolver.dataset_codes

    assert result == ("a.one", "b.two")
