"""Fakes for the reference-data seed: a file reader and the placeholder policies.

``FakeReferenceFileReader`` parses the real ``data/reference`` YAML files (reading
local fixture files is allowed in unit tests; nothing touches the network) or returns
models a test hands in. ``AllowAllPolicy`` and ``DenyAllPolicy`` moved to
``tests.fakes.identity`` in Phase 2; they are re-exported here for one phase so
existing imports keep working, and new code imports them from there.

Patterns: Fake.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from tests.fakes.identity import AllowAllPolicy, DenyAllPolicy
from yakhnama.modules.geography.public import PlaceReferenceFile
from yakhnama.modules.hazards.public import HazardTypeReferenceFile
from yakhnama.modules.impacts.public import ImpactMetricReferenceFile
from yakhnama.shared_kernel.errors import ValidationError

__all__ = [
    "HAZARD_TYPES_FILE",
    "IMPACT_METRICS_FILE",
    "PLACES_FILE",
    "REFERENCE_DIRECTORY",
    "AllowAllPolicy",
    "DenyAllPolicy",
    "FakeReferenceFileReader",
    "parse_reference_file",
    "read_reference_yaml",
]

REFERENCE_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "reference"
"""``data/reference`` at the repository root."""

HAZARD_TYPES_FILE = "hazard_types.yaml"
IMPACT_METRICS_FILE = "impact_metrics.yaml"
PLACES_FILE = "admin_hierarchy_gb.yaml"


def read_reference_yaml(name: str) -> object:
    """Parse one reference file with ``yaml.safe_load``.

    Args:
        name: File name inside ``data/reference``.

    Returns:
        The parsed, still unvalidated document.
    """
    return yaml.safe_load((REFERENCE_DIRECTORY / name).read_text(encoding="utf-8"))


def parse_reference_file[ModelT: BaseModel](model: type[ModelT], name: str) -> ModelT:
    """Parse and validate one reference file, as the real adapter must.

    Args:
        model: The reference file model.
        name: File name inside ``data/reference``.

    Returns:
        The validated model.

    Raises:
        ValidationError: The kernel error, if the document does not match
            ``model``; the ``ReferenceFileReader`` port promises no other error.
    """
    try:
        return model.model_validate(read_reference_yaml(name))
    except PydanticValidationError as error:
        message = f"reference file {name!r} is invalid"
        raise ValidationError(message, details={"file": name}) from error


class FakeReferenceFileReader:
    """``ReferenceFileReader`` over the repository's reference files or given models.

    Implements: Fake (of Adapter).

    Attributes:
        reads: Names of the files read, in call order.
    """

    def __init__(
        self,
        *,
        hazard_types: HazardTypeReferenceFile | None = None,
        impact_metrics: ImpactMetricReferenceFile | None = None,
        places: PlaceReferenceFile | None = None,
    ) -> None:
        """Create the reader.

        Args:
            hazard_types: Model to return instead of parsing the real file.
            impact_metrics: Model to return instead of parsing the real file.
            places: Model to return instead of parsing the real file.
        """
        self._hazard_types = hazard_types
        self._impact_metrics = impact_metrics
        self._places = places
        self.reads: list[str] = []

    def read_hazard_types(self) -> HazardTypeReferenceFile:
        """Return the hazard taxonomy reference file.

        Returns:
            The given model, or the parsed ``hazard_types.yaml``.
        """
        self.reads.append(HAZARD_TYPES_FILE)
        if self._hazard_types is not None:
            return self._hazard_types
        return parse_reference_file(HazardTypeReferenceFile, HAZARD_TYPES_FILE)

    def read_impact_metrics(self) -> ImpactMetricReferenceFile:
        """Return the impact metric reference file.

        Returns:
            The given model, or the parsed ``impact_metrics.yaml``.
        """
        self.reads.append(IMPACT_METRICS_FILE)
        if self._impact_metrics is not None:
            return self._impact_metrics
        return parse_reference_file(ImpactMetricReferenceFile, IMPACT_METRICS_FILE)

    def read_places(self) -> PlaceReferenceFile:
        """Return the place hierarchy reference file.

        Returns:
            The given model, or the parsed ``admin_hierarchy_gb.yaml``.
        """
        self.reads.append(PLACES_FILE)
        if self._places is not None:
            return self._places
        return parse_reference_file(PlaceReferenceFile, PLACES_FILE)
