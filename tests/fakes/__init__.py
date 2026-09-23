"""In-memory fakes of the shared-kernel ports, shared by every test suite.

Fakes are real, deterministic implementations of our ports, never mocks
(``AGENTS.md`` §5). Module-specific port fakes (repositories, query services) live in
``tests/fakes/<module>.py`` next to these and are written by the task that defines the
port.

Patterns: Fake.
"""
