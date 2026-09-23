"""The ``media`` bounded context.

Media assets: object keys, hashes, EXIF facts and moderation status.
Binaries live in object storage, never in the database.

Patterns: Facade (see ``public.py``).
"""
