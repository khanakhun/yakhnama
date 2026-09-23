"""Bearer-token authentication against an OpenID Connect provider (ADR 0005, 0015).

``HttpJwksClient`` fetches and caches the provider's signing keys, ``TokenValidator``
checks a JWT access token and maps its claims to a ``Principal``, the only view of an
authenticated caller the rest of the code base sees. ``PrincipalResolutionMiddleware``
validates the token once per request so rate limiting and idempotency can key on the
principal, and the FastAPI dependencies ``current_principal`` and
``optional_principal`` hand the result to routes. Nothing outside this package reads
raw token claims.

Patterns: Adapter, Anti-Corruption Layer, Dependency Injection, Decorator.
"""
