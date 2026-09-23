"""``polyfactory`` factories that build valid domain objects for tests.

Every factory produces objects that pass the domain validators, with timezone-aware
UTC datetimes and real UUIDv7 ids from a deterministic generator. Values come from one
seeded random source, so a test run is reproducible; tests that care about a value pass
it explicitly (``PlaceTestFactory.build(level=AdminLevel.VILLAGE)``) instead of relying
on what the factory happens to draw.

Patterns: Factory.
"""
