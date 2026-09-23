"""The YAML adapter of the seed's ``ReferenceFileReader`` port.

Each reference file in ``data/reference`` is parsed with ``yaml.safe_load`` (never
``yaml.load``: the files are data, and a full loader would construct arbitrary Python
objects) and validated at once into the module's reference model, so the untyped
structure never leaves this module.

Every failure becomes the kernel ``ValidationError`` naming the file and the kind of
failure only. Neither YAML nor Pydantic error text is passed on, because both quote
the offending input, and the CLI logs error details.

Patterns: Adapter.
"""

from pathlib import Path
from typing import Final

import pydantic
import yaml
from pydantic import BaseModel

from yakhnama.modules.geography.public import PlaceReferenceFile
from yakhnama.modules.hazards.public import HazardTypeReferenceFile
from yakhnama.modules.impacts.public import ImpactMetricReferenceFile
from yakhnama.modules.ingestion.public import DatasetReferenceFile
from yakhnama.shared_kernel.errors import ValidationError

HAZARD_TYPES_FILE: Final = "hazard_types.yaml"
"""File name of the hazard taxonomy inside the reference directory."""

IMPACT_METRICS_FILE: Final = "impact_metrics.yaml"
"""File name of the impact metric registry inside the reference directory."""

PLACES_FILE: Final = "admin_hierarchy_gb.yaml"
"""File name of the place hierarchy inside the reference directory."""

DATASETS_FILE: Final = "datasets.yaml"
"""File name of the ingestion dataset catalog inside the reference directory."""


def _invalid_file(file_name: str, reason: str, **extra: object) -> ValidationError:
    message = f"reference file {file_name} could not be loaded: {reason}"
    return ValidationError(
        message, details={"file": file_name, "reason": reason, **extra}
    )


class YamlReferenceFileReader:
    """Reads the reference files from one directory and validates them.

    The directory is checked when a file is read, not when the reader is built, so
    wiring the seed never touches the file system.

    It answers both the ``ReferenceFileReader`` and the ``DatasetReferenceReader``
    ports.

    Implements: Adapter.
    """

    def __init__(self, directory: Path) -> None:
        """Create the reader.

        Args:
            directory: The directory holding the reference YAML files.
        """
        self._directory = directory

    @property
    def directory(self) -> Path:
        """Return the directory the files are read from."""
        return self._directory

    def read_hazard_types(self) -> HazardTypeReferenceFile:
        """Parse and validate ``hazard_types.yaml``.

        Returns:
            The validated hazard taxonomy file.

        Raises:
            ValidationError: If the file is missing, unreadable, not YAML or does
                not match ``HazardTypeReferenceFile``.
        """
        return self._load(HAZARD_TYPES_FILE, HazardTypeReferenceFile)

    def read_impact_metrics(self) -> ImpactMetricReferenceFile:
        """Parse and validate ``impact_metrics.yaml``.

        Returns:
            The validated impact metric file.

        Raises:
            ValidationError: If the file is missing, unreadable, not YAML or does
                not match ``ImpactMetricReferenceFile``.
        """
        return self._load(IMPACT_METRICS_FILE, ImpactMetricReferenceFile)

    def read_places(self) -> PlaceReferenceFile:
        """Parse and validate ``admin_hierarchy_gb.yaml``.

        Returns:
            The validated place hierarchy file.

        Raises:
            ValidationError: If the file is missing, unreadable, not YAML or does
                not match ``PlaceReferenceFile``.
        """
        return self._load(PLACES_FILE, PlaceReferenceFile)

    def read_datasets(self) -> DatasetReferenceFile:
        """Parse and validate ``datasets.yaml``.

        Returns:
            The validated dataset catalog file (its ``datasets:`` entries).

        Raises:
            ValidationError: If the file is missing, unreadable, not YAML or does
                not match ``DatasetReferenceFile``.
        """
        return self._load(DATASETS_FILE, DatasetReferenceFile)

    def _load[ModelT: BaseModel](self, file_name: str, model: type[ModelT]) -> ModelT:
        # Every re-raise uses ``from None``: a chained cause would carry the YAML or
        # Pydantic message, which quotes the file, into any logged traceback.
        path = self._directory / file_name
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise _invalid_file(file_name, "not_found") from None
        except (OSError, UnicodeDecodeError) as error:
            # The error type is enough to act on (a permission problem, a binary
            # file); the OS message may repeat the full path of the machine.
            raise _invalid_file(
                file_name, "unreadable", error_type=type(error).__name__
            ) from None
        try:
            document = yaml.safe_load(content)
        except yaml.MarkedYAMLError as error:
            # The line number locates the problem without quoting the text around it.
            line = error.problem_mark.line + 1 if error.problem_mark else None
            raise _invalid_file(file_name, "not_yaml", line=line) from None
        except yaml.YAMLError:
            raise _invalid_file(file_name, "not_yaml") from None
        try:
            return model.model_validate(document)
        except pydantic.ValidationError as error:
            # Only the count and the Pydantic error types: locations can be keys
            # taken from the file and messages can quote its values.
            error_types = tuple(sorted({item["type"] for item in error.errors()}))
            raise _invalid_file(
                file_name,
                "invalid",
                error_count=error.error_count(),
                error_types=error_types,
            ) from None
