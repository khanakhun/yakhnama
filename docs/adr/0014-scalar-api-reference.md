# 0014. Scalar API reference at /api/v1/docs

- Date: 2026-09-23
- Status: accepted
- Deciders: lead agent, maintainer

## Context and problem statement

FastAPI serves Swagger UI at `/docs` and ReDoc at `/redoc` by default, both loading
JavaScript from a CDN. The maintainer decided that Scalar replaces both at `/api/v1/docs`,
self-hosted and gated by `docs_enabled` (`docs/plans/phase-2.md` §2). The API sends
`Content-Security-Policy: default-src 'none'` on every response, which no interactive
documentation page can run under. The installed `scalar-fastapi` 1.9.0 renders a small HTML
page and loads the Scalar bundle from `scalar_js_url`; it ships **no** JavaScript of its own.

How is the API reference served, and what does the page's security policy allow?

## Decision drivers

- Maintainer decision: Scalar, one page, at `/api/v1/docs`, off in production.
- Security: the API's strict CSP must stay intact on every other response; the page must not
  load more third-party resources than needed.
- No network in this run: a bundle cannot be downloaded into the repository here.
- Privacy: no third-party telemetry from the page (`AGENTS.md` §5).

## Considered options

1. Scalar through `scalar-fastapi`, bundle URL configurable, CDN by default, page-specific
   CSP with a hash of the inline script (chosen)
2. Vendor the Scalar bundle into the repository and serve it as a static file
3. Keep FastAPI's Swagger UI and ReDoc

## Decision outcome

Chosen option: **1**, because it is the only option that delivers Scalar now without a
network download, while keeping the strict CSP everywhere else and making full self-hosting
a configuration change.

- `create_app` sets `docs_url=None` and `redoc_url=None`; Swagger UI and ReDoc are never
  served. When `docs_enabled` is true it serves `GET /api/v1/docs` (excluded from the OpenAPI
  document) and `GET /api/v1/openapi.json`; when false, neither exists (404).
- `platform/api_docs.py` renders the page with `scalar_js_url = docs_scalar_js_url`
  (default `https://cdn.jsdelivr.net/npm/@scalar/api-reference`), the favicon as an empty
  `data:` URL, Scalar's default web fonts off, Scalar telemetry off and developer tools off.
- The page's CSP is computed from the rendered HTML: `default-src 'none'; script-src
  <bundle URL> 'sha256-<hash of the inline configuration script>'; style-src
  'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self';
  base-uri 'none'; form-action 'none'; frame-ancestors 'none'`. Every other response keeps
  `default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`.
- `docs_scalar_js_url` must be `https` (or `http` on a loopback host). A deployment that
  wants no CDN hosts the bundle and points the setting at it.
- The production guard in `platform/settings.py` requires `docs_enabled = false`.

### Consequences

- Good, because the reference is one modern page and the API's CSP is untouched elsewhere.
- Good, because the inline script is allowed by hash, not by `'unsafe-inline'`, and the hash
  can never drift from the page it is computed from.
- Good, because moving to a self-hosted bundle is a configuration change, not a code change.
- Bad, because by default a developer's browser loads the bundle from jsDelivr, a third
  party, which sees the developer's IP address; the maintainer's "self-hosted" is not met
  until a bundle is hosted (see More information).
- Bad, because the default URL is unversioned: the CDN serves the latest bundle, without
  Subresource Integrity, so a compromised or broken release reaches developers directly.
- Bad, because `style-src 'unsafe-inline'` is needed (Scalar injects styles at run time).
- Bad, because whether the Scalar bundle needs more than this CSP allows (for example
  `'unsafe-eval'` or workers) could not be verified in a browser in this run.

## Pros and cons of the options

### Option 1, Scalar with a configurable bundle URL

- Good, because it satisfies the maintainer's choice of tool and path today.
- Bad, because full self-hosting depends on an operator step.

### Option 2, vendor the bundle

- Good, because nothing is loaded from a third party, and the bundle is pinned and
  reviewable.
- Bad, because it needs a download that this run may not make, adds a large generated
  asset to the repository and needs an update process.

### Option 3, keep Swagger UI and ReDoc

- Good, because nothing changes.
- Bad, because it contradicts the maintainer's decision and still loads CDN assets.

## More information

- Open question for `docs/open-questions.md`: host a pinned Scalar bundle (from the npm
  package `@scalar/api-reference`, a fixed version) as a static asset or on
  `yakhnama.org`, and add its Subresource Integrity hash; proposed default: keep the CDN for
  development only until then. Non-blocking, because production has no docs page.
- The CSP of the page must be checked in a real browser before the gate (lead, Phase 2 §5
  manual flow).
- `scalar-fastapi` 1.9.0, `get_scalar_api_reference`; ADR 0003 (FastAPI app factory).
