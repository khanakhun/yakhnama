"""Errors raised by the authentication adapter itself.

``AuthenticationError`` (HTTP 401) lives in the shared kernel because routes and
policies raise it too; the error here is specific to the adapter talking to the
identity provider.

Patterns: Domain Error (proposed in ADR 0012).
"""

from typing import ClassVar

from yakhnama.shared_kernel.errors import YakhnamaError


class IdentityProviderUnavailableError(YakhnamaError):
    """The identity provider's discovery document or keys could not be fetched.

    A token cannot be checked without keys, but the token is not at fault, so this is
    HTTP 503 rather than 401: a client should retry later, not ask the user to log in
    again.

    Implements: Domain Error (proposed in ADR 0012).

    Attributes:
        code: ``"identity_provider_unavailable"``.
    """

    code: ClassVar[str] = "identity_provider_unavailable"
