"""Unit tests for ``yakhnama.shared_kernel.clock``."""

from datetime import UTC, datetime

from yakhnama.shared_kernel.clock import Clock, SystemClock


def test_system_clock_now_returns_aware_utc_between_two_readings() -> None:
    clock: Clock = SystemClock()
    before = datetime.now(UTC)

    now = clock.now()
    after = datetime.now(UTC)

    assert now.tzinfo is UTC
    assert before <= now <= after
