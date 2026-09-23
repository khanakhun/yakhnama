"""Write the committed OpenAPI snapshot, ``tests/contract/openapi.json``.

``python -m yakhnama.platform.openapi_snapshot`` (``poe openapi-snapshot``) builds the
app from fixed test settings and writes its OpenAPI document with sorted keys, two
space indentation and a final newline, so the same code always produces the same
bytes and a diff shows only real API changes. The settings ignore the environment
and any ``.env`` file: a developer's local configuration must never leak into the
contract. The contract test in ``tests/contract`` compares the app with the file.

Patterns: Settings (the snapshot's fixed settings).
"""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from yakhnama.platform.settings import Settings

DEFAULT_OUTPUT: Final = Path("tests/contract/openapi.json")


class SnapshotSettings(Settings):
    """``Settings`` read only from constructor arguments.

    Implements: Settings (fixed values for the OpenAPI snapshot).
    """

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Use constructor arguments only, never the environment or ``.env``.

        Args:
            settings_cls: The settings class (unused).
            init_settings: Constructor arguments; the only source kept.
            env_settings: Environment variables (dropped).
            dotenv_settings: The ``.env`` file (dropped).
            file_secret_settings: Secret files (dropped).

        Returns:
            The sources to read, in priority order.
        """
        del settings_cls, env_settings, dotenv_settings, file_secret_settings
        return (init_settings,)


def snapshot_settings() -> Settings:
    """Return the fixed settings the snapshot is generated with.

    Returns:
        Test settings with the docs, and so the OpenAPI document, enabled.
    """
    return SnapshotSettings(environment="test", docs_enabled=True, log_format="console")


def render_openapi_snapshot() -> str:
    """Build the app from ``snapshot_settings`` and render its OpenAPI document.

    Returns:
        Deterministic JSON text ending in a newline.
    """
    # Imported here: yakhnama.main imports the platform, and the platform must not
    # import the application entry point at module level (import-linter contract).
    from yakhnama.main import create_app  # noqa: PLC0415  # reason: see comment

    document = create_app(snapshot_settings()).openapi()
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_openapi_snapshot(output: Path) -> None:
    """Write the snapshot to ``output``, creating its directory.

    Args:
        output: The file to write.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_openapi_snapshot(), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> None:
    """Command-line entry point.

    Args:
        argv: Arguments without the program name; ``sys.argv`` when ``None``.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args(argv)
    write_openapi_snapshot(arguments.output)


if __name__ == "__main__":
    main()
