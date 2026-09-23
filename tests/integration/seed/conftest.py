"""Fixtures for the seed integration tests: the migrated schema of the module tests.

The fixtures are re-exported from ``tests/integration/modules/conftest.py`` rather
than copied, so both packages upgrade and downgrade the session database the same
way. Registered here, the package-scoped ``migrated_schema`` is set up and torn down
once for this package, leaving an empty database for the packages after it.
"""

from tests.integration.modules.conftest import (
    engine,
    migrated_schema,
    session_factory,
)

__all__ = ["engine", "migrated_schema", "session_factory"]
