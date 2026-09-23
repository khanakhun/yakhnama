"""Errors raised by the events domain.

Every class derives from a ``yakhnama.shared_kernel.errors`` family, so the API maps it
to Problem Details by the family's ``code`` (``AGENTS.md`` §2.3). Messages carry only
identifiers, statuses and codes, never free text a moderator typed.

Patterns: Domain Error.
"""

from uuid import UUID

from yakhnama.shared_kernel.errors import (
    ConflictError,
    InvalidTransitionError,
    InvariantViolationError,
    NotFoundError,
    ValidationError,
)


class EventNotFoundError(NotFoundError):
    """No event has the given id.

    Implements: Domain Error.

    Attributes:
        event_id: The id that did not resolve.
    """

    def __init__(self, event_id: UUID) -> None:
        """Create the error.

        Args:
            event_id: The id that did not resolve.
        """
        super().__init__(
            f"event {event_id} does not exist", details={"event_id": str(event_id)}
        )
        self.event_id = event_id


class EventImmutableError(InvalidTransitionError):
    """The event is retracted or merged, and such an event accepts no change.

    Nothing verified is deleted: a retracted or merged event stays as it was, so the
    record of what was once published can always be reconstructed.

    Implements: Domain Error.

    Attributes:
        event_id: The event.
        status: Its final status.
        operation: What was attempted, for example ``"link_report"``.
    """

    def __init__(self, event_id: UUID, status: str, operation: str) -> None:
        """Create the error.

        Args:
            event_id: The event.
            status: Its final status.
            operation: What was attempted.
        """
        super().__init__(
            f"event {event_id} is {status}; {operation} is not allowed",
            details={
                "event_id": str(event_id),
                "status": status,
                "operation": operation,
            },
        )
        self.event_id = event_id
        self.status = status
        self.operation = operation


class InvalidEventStatusError(InvalidTransitionError):
    """The event's status does not allow the requested status change.

    Implements: Domain Error.

    Attributes:
        event_id: The event.
        status: Its current status.
        operation: What was attempted, for example ``"publish"``.
    """

    def __init__(self, event_id: UUID, status: str, operation: str) -> None:
        """Create the error.

        Args:
            event_id: The event.
            status: Its current status.
            operation: What was attempted.
        """
        super().__init__(
            f"event {event_id} is {status}; cannot {operation}",
            details={
                "event_id": str(event_id),
                "status": status,
                "operation": operation,
            },
        )
        self.event_id = event_id
        self.status = status
        self.operation = operation


class ReportAlreadyLinkedError(ConflictError):
    """The report is already linked to the event.

    Implements: Domain Error.

    Attributes:
        event_id: The event.
        report_id: The report.
    """

    def __init__(self, event_id: UUID, report_id: UUID) -> None:
        """Create the error.

        Args:
            event_id: The event.
            report_id: The report.
        """
        super().__init__(
            f"report {report_id} is already linked to event {event_id}",
            details={"event_id": str(event_id), "report_id": str(report_id)},
        )
        self.event_id = event_id
        self.report_id = report_id


class ReportNotLinkedError(NotFoundError):
    """The report is not linked to the event, so it cannot be unlinked.

    Implements: Domain Error.

    Attributes:
        event_id: The event.
        report_id: The report.
    """

    def __init__(self, event_id: UUID, report_id: UUID) -> None:
        """Create the error.

        Args:
            event_id: The event.
            report_id: The report.
        """
        super().__init__(
            f"report {report_id} is not linked to event {event_id}",
            details={"event_id": str(event_id), "report_id": str(report_id)},
        )
        self.event_id = event_id
        self.report_id = report_id


class InvalidRelationError(InvariantViolationError):
    """A relation between events would break the event graph's rules.

    Raised for a duplicate relation, a ``part_of`` cycle, or merging an event into
    itself.

    Implements: Domain Error.
    """


class AttributesMismatchError(ValidationError):
    """The attributes' schema does not belong to the event's hazard type.

    Implements: Domain Error.

    Attributes:
        hazard_code: The event's hazard type code.
        attributes_code: The ``hazard_type`` discriminator of the attributes.
    """

    def __init__(self, hazard_code: str, attributes_code: str) -> None:
        """Create the error.

        Args:
            hazard_code: The event's hazard type code.
            attributes_code: The discriminator of the attributes given.
        """
        super().__init__(
            f"attributes for {attributes_code!r} do not fit an event of hazard type "
            f"{hazard_code!r}",
            details={"hazard_code": hazard_code, "attributes_code": attributes_code},
        )
        self.hazard_code = hazard_code
        self.attributes_code = attributes_code
