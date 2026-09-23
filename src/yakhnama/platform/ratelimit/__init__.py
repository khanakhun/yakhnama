"""Per-client rate limiting behind a port (ADR 0017).

``RateLimiter`` is the port; ``InMemoryRateLimiter`` (token bucket, one process) and
``RedisRateLimiter`` (fixed window shared by every process) are its adapters, and
``RateLimitMiddleware`` applies them per principal or per client IP.

Patterns: Adapter, Decorator, DTO.
"""
