"""A container with the outbox and idempotency adapters swapped for Fakes.

Used by the container and worker tests to run the platform task handlers without a
database.
"""

import dataclasses

from tests.fakes.auth import InMemoryIdempotencyStore
from tests.unit.platform.events import FixedClock
from tests.unit.platform.outbox_store import InMemoryOutboxStore
from yakhnama.platform.container import Container, build_container
from yakhnama.platform.outbox.relay import OutboxRelay
from yakhnama.platform.settings import Settings


def build_faked_container(
    settings: Settings, store: InMemoryOutboxStore, clock: FixedClock
) -> Container:
    """Return the production container with in-memory outbox and idempotency stores.

    Args:
        settings: The settings to build from.
        store: The outbox store the relay uses.
        clock: The container's and the relay's clock.

    Returns:
        The container; its engine is never connected.
    """
    container = build_container(settings)
    relay = OutboxRelay(
        container.subscriber_registry,
        clock,
        store,
        max_attempts=settings.outbox_max_attempts,
        lease_seconds=settings.outbox_lease_seconds,
        subscriber_timeout_seconds=settings.outbox_subscriber_timeout_seconds,
    )
    return dataclasses.replace(
        container,
        clock=clock,
        outbox_store=store,
        outbox_relay=relay,
        idempotency_store=InMemoryIdempotencyStore(),
    )
