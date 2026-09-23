"""Loading of the reference YAML files for the data tests.

The files are read from disk once per call with ``yaml.safe_load``; nothing here
writes, and no network is touched, so the tests stay unit tests.
"""

from pathlib import Path

import yaml

REFERENCE_DIRECTORY = Path(__file__).resolve().parents[3] / "data" / "reference"
"""``data/reference`` at the repository root."""

REFERENCE_FILE_NAMES = (
    "hazard_types.yaml",
    "impact_metrics.yaml",
    "languages.yaml",
    "admin_hierarchy_gb.yaml",
)
"""Every reference file the tests cover."""


def load_reference(name: str) -> dict[str, object]:
    """Parse one reference file into its raw mapping.

    Args:
        name: File name inside ``data/reference``.

    Returns:
        The top-level mapping of the file.

    Raises:
        TypeError: If the file's top level is not a mapping.
    """
    text = (REFERENCE_DIRECTORY / name).read_text(encoding="utf-8")
    raw: object = yaml.safe_load(text)
    if not isinstance(raw, dict):
        message = f"{name}: the top level must be a mapping"
        raise TypeError(message)
    return {str(key): value for key, value in raw.items()}


def raw_entries(name: str) -> list[dict[str, object]]:
    """Return the raw ``entries`` of one reference file.

    Args:
        name: File name inside ``data/reference``.

    Returns:
        Each entry as a mapping, in file order.

    Raises:
        TypeError: If ``entries`` is not a list of mappings.
    """
    entries = load_reference(name).get("entries")
    if not isinstance(entries, list):
        message = f"{name}: 'entries' must be a list"
        raise TypeError(message)
    result: list[dict[str, object]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            message = f"{name}: every entry must be a mapping"
            raise TypeError(message)
        result.append({str(key): value for key, value in entry.items()})
    return result
