# Contract tests

`openapi.json` is the committed HTTP contract; `test_openapi_snapshot.py` fails when the app drifts from it.
Regenerate it with `poetry run poe openapi-snapshot` and review the diff: removals and narrowed types are breaking changes and need an ADR.
