"""Source adapter reading a dataset's bytes from a local fixtures directory.

No live network calls are allowed in this run, so the reference source reads a
committed file. The file is located by a per-adapter mapping from dataset code to a
path relative to one configured directory, never by ``Dataset.homepage_url``: that
field is an ``http``/``https`` URL meant for readers of the catalog, and letting
catalog data choose a file path would turn a catalog edit into a file read.

Path traversal is refused twice: lexically when the mapping is built (no absolute
paths, no ``..``, no empty parts), and again when a file is read, after resolving
symbolic links, so a link inside the directory cannot point outside it.

Patterns: Adapter, Anti-Corruption Layer.
"""

import asyncio
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Final

from yakhnama.modules.ingestion.application.ports import RawPayload
from yakhnama.modules.ingestion.domain.entities import Dataset, DatasetVersion
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import NotFoundError, ValidationError

CSV_MEDIA_TYPE: Final = "text/csv"
"""Media type of every payload this adapter returns."""

MAX_LOCAL_FILE_BYTES: Final = 10 * 1024 * 1024
"""Largest fixture file read (**proposed** operational default): a fixture is small by
definition, and the cap keeps a misconfigured path from loading a huge file."""


def _require_relative(relative_path: str) -> PurePosixPath:
    # Paths are written with "/" in configuration on every OS; PurePosixPath keeps
    # the check independent of the platform the worker runs on.
    candidate = PurePosixPath(relative_path)
    if (
        not relative_path
        or "\\" in relative_path
        or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in relative_path.split("/"))
    ):
        message = "a fixture path must be a plain relative path inside the directory"
        raise ValidationError(message, details={"field": "relative_path"})
    return candidate


class FixturePathResolver:
    """Maps dataset codes to files inside one fixtures directory.

    It turns a dataset code into a checked local path, so catalog data never
    names a file.

    Implements: Anti-Corruption Layer.

    Attributes:
        root: The fixtures directory.
    """

    def __init__(self, root: Path, files: Mapping[str, str]) -> None:
        """Create the resolver.

        Args:
            root: The directory every file must be inside.
            files: Relative path (``/``-separated) by dataset code.

        Raises:
            ValidationError: If a path is absolute, empty, contains ``.`` or ``..``
                parts or a backslash.
        """
        self.root = root
        self._files: Mapping[str, PurePosixPath] = MappingProxyType(
            {code: _require_relative(path) for code, path in files.items()}
        )

    @property
    def dataset_codes(self) -> tuple[str, ...]:
        """Return the dataset codes that have a file, sorted."""
        return tuple(sorted(self._files))

    def resolve(self, dataset_code: str) -> Path:
        """Return the checked absolute path of ``dataset_code``'s file.

        Args:
            dataset_code: The dataset being fetched.

        Returns:
            The resolved path, inside the resolved ``root``.

        Raises:
            NotFoundError: If no file is mapped for the dataset.
            ValidationError: If the resolved path leaves ``root`` (for example
                through a symbolic link).
        """
        relative = self._files.get(dataset_code)
        if relative is None:
            message = "no fixture file is configured for this dataset"
            raise NotFoundError(message, details={"dataset_code": dataset_code})
        root = self.root.resolve()
        path = root.joinpath(*relative.parts).resolve()
        if not path.is_relative_to(root):
            message = "the fixture path leaves the fixtures directory"
            raise ValidationError(message, details={"dataset_code": dataset_code})
        return path


def _read_capped(path: Path, max_bytes: int) -> bytes:
    with path.open("rb") as handle:
        # Reading one byte past the cap tells "exactly at the cap" from "over it"
        # without trusting a size reported before the read.
        content = handle.read(max_bytes + 1)
    if len(content) > max_bytes:
        message = "the fixture file is larger than the configured limit"
        raise ValidationError(message, details={"max_bytes": max_bytes})
    return content


class LocalCsvSourceAdapter:
    """Reads a dataset version's CSV from the fixtures directory.

    Implements: Adapter (port ``SourceAdapter``).

    Attributes:
        path_resolver: Where each dataset's file is.
    """

    def __init__(
        self,
        name: str,
        path_resolver: FixturePathResolver,
        *,
        clock: Clock,
        max_bytes: int = MAX_LOCAL_FILE_BYTES,
    ) -> None:
        """Create the adapter.

        Args:
            name: The registry name, an ``AdapterName``.
            path_resolver: Maps the dataset code to its file.
            clock: Source of ``retrieved_at``.
            max_bytes: Largest file read.
        """
        self._name = name
        self.path_resolver = path_resolver
        self._clock = clock
        self._max_bytes = max_bytes

    @property
    def name(self) -> str:
        """Return the adapter's registry name."""
        return self._name

    async def fetch(self, dataset: Dataset, version: DatasetVersion) -> RawPayload:
        """Read the dataset's file and fingerprint it.

        The same file serves every version: a fixture is ``static``, and the
        pipeline compares the bytes with ``version.input_checksum``, so a changed
        file fails the run instead of being stored under an old version.

        Args:
            dataset: The dataset; its ``code`` selects the file.
            version: The version; unused beyond the interface.

        Returns:
            The bytes as ``text/csv`` with their SHA-256.

        Raises:
            NotFoundError: If no file is mapped for the dataset.
            ValidationError: If the path leaves the directory or the file is over
                the limit.
            OSError: If the file cannot be read.
        """
        path = self.path_resolver.resolve(dataset.code)
        # File reads block; a worker's event loop must keep serving other tasks.
        content = await asyncio.to_thread(_read_capped, path, self._max_bytes)
        return RawPayload.of(
            content, media_type=CSV_MEDIA_TYPE, retrieved_at=self._clock.now()
        )
