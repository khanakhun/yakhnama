# 0023. Production packaging and deployment topology

- Date: 2026-10-07
- Status: proposed
- Deciders: lead agent, maintainer

## Context and problem statement

The backend has run only from a developer's checkout (`poetry run uvicorn`, `poe
worker`, `poe scheduler`) against the development services of `docker-compose.yml`.
The first production target is one virtual server (16 GB of memory, 80 GB of disk) that
already serves another Next.js site behind a reverse proxy on the host (nginx or Caddy).
The web portal will run on the same host without Docker and call the API server-side.
Keycloak (Q5) and MinIO must be reachable from browsers: Keycloak for sign-in, MinIO for
presigned photo uploads and downloads. The production guard in `platform/settings.py`
already fixes much of the runtime (Redis rate limiting and task queue, ClamAV, https for
the issuer and the storage endpoint, JSON logs).

How is the backend packaged, which services run where, how do they reach each other,
and how are memory and logs kept bounded on a small shared server?

## Decision drivers

- One small, shared server: low idle memory, bounded logs, small images.
- The production guard is not weakened: https issuer and storage endpoint, no `noop`
  scanner, Redis-backed rate limits and queue.
- Least exposure: the API, the database, Redis and clamd are never public; secrets reach
  only the process that needs them.
- Reporter privacy (`AGENTS.md` §5): no statement, query string or client address in
  logs.
- Operable by one maintainer: plain Docker Compose, no orchestrator, the same image for
  every backend process, upgrades that migrate before the application restarts.
- Poetry stays the only package manager (`AGENTS.md` §4).

## Considered options

1. One image for every backend process and one Compose stack on the host beside the
   existing reverse proxy; the application reaches Keycloak and MinIO through their
   public https names, routed to the host proxy with `extra_hosts` (proposed)
2. The same, but the application reaches Keycloak and MinIO by their compose service
   names over plain http
3. Managed services (hosted PostgreSQL, S3 and identity provider) with only the API,
   worker and scheduler on the server
4. Separate images per process (API, worker, scheduler)

## Decision outcome

Proposed option: **one image, one Compose stack, public names through the host
proxy**, because it fits the server and the maintainer's tools, keeps every production
guard rule as it is, and needs no code change.

- **Image** (`Dockerfile`): multi-stage. The builder installs Poetry's `main` group from
  `poetry.lock` into `/opt/venv` and the project as a path dependency (its metadata
  gives the API its version); every dependency has a wheel, so no compiler. Unused
  weight is trimmed (third-party tests and headers, pyarrow's Flight libraries,
  botocore's non-S3 models) and an import check fails the build if a trim breaks
  something. The runtime stage is the pinned `python:3.13-slim` with the venv, `src/`,
  the migrations and the committed data the seed, the boundary load and the ingestion
  fixtures read; non-root user, no bytecode, no pip. The default command is uvicorn
  with `WEB_CONCURRENCY` workers, `--no-proxy-headers`, `--no-access-log`; the healthcheck
  is `/health/live`. The worker, the scheduler and the one-shot commands (`alembic
  upgrade head`, `python -m yakhnama.seed`, `python -m yakhnama.seed.boundaries`) are
  other commands of the same image.
- **Stack** (`docker-compose.production.yml`): `migrate` (one-shot; the application
  starts only after it succeeded), `api`, `worker`, `scheduler`, `postgres` (PostGIS,
  tuned for a 1 GB budget, warnings-only terse logging), `redis` (password, AOF every
  second, no snapshots, 128 MB `noeviction`), `keycloak` (own image built with
  `kc.sh build` for PostgreSQL, `start --optimized`, its own database and role in the
  same cluster, heap capped, admin console on loopback only), `minio` (CORS for the
  portal origin), `minio-init` (buckets, the upload lifecycle rule, and an application
  user limited to the two media buckets, so the API never holds the root key) and
  `clamav` (55 MB stream limit, no concurrent signature reload). Every service has a
  restart policy, a memory limit, a healthcheck and `json-file` logs capped at 3 × 10 MB;
  application containers run read-only with `no-new-privileges`. Ports bind to
  127.0.0.1; PostgreSQL, Redis and clamd publish none. Images are pinned by digest.
- **Configuration**: one env file (`production.env.example` → `.env.production`, git-
  ignored). Compose hands each container only its own variables: the application gets
  the `YAKHNAMA_*` names listed in the compose file and nothing else. A unit test keeps
  the example complete and proves that, filled in, it passes the production guard.
- **Reaching Keycloak and MinIO**: through `https://auth.*` and `https://files.*`, the
  names browsers use. The issuer must be the public one anyway (Keycloak writes
  `KC_HOSTNAME` into every token), and the backend has a single storage endpoint for its
  own calls and for the host inside presigned URLs (Q120), which must be the public one.
  `extra_hosts` maps both names to the host (`host-gateway`), so the calls go straight to
  the host proxy without a public DNS round trip.
- **Client addresses**: the API is reached only by the portal, which deliberately sends
  no `X-Forwarded-For` (the visitor would choose it). Uvicorn ignores forwarded headers,
  so every anonymous request shares the portal's rate-limit bucket; the example raises
  the anonymous limit accordingly until Q260 is decided.
- **Keycloak realm**: `python -m yakhnama.platform.keycloak_realm` derives the
  production realm from the development export (no users, no development client, one
  portal origin, `sslRequired: external`, SMTP from placeholders) and refuses a result
  with any loopback or development value left; Keycloak imports it once on first start.

**What is needed to move this ADR to `accepted`:**

1. The maintainer confirms the hosting topology: one server, Docker Compose, Keycloak
   and MinIO self-hosted on it (Q261, Q5).
2. Keycloak's database in the shared PostgreSQL cluster (Q262), and the ClamAV memory
   budget (Q263), are accepted.
3. A backup and restore procedure exists and has been tested once (Q264).
4. The anonymous rate limit behind the portal is decided (Q260).

### Consequences

- Good, because one image (419 MB, 143 MB compressed) serves four roles, and the whole
  stack idles at about 2.6 GiB, inside 7 GiB of limits on a 16 GB server.
- Good, because the production guard is untouched: the issuer and the storage endpoint
  are https, and the smoke test exercised sign-in, presigned upload and server-side
  storage through TLS.
- Good, because logs cannot fill the disk and carry no statements or client addresses.
- Good, because upgrades are `build` then `up -d`: migrations always run before the new
  application code serves.
- Bad, because the application's calls to Keycloak and MinIO pass through the host
  proxy and its TLS, which costs a little latency and makes the proxy a dependency of the
  backend, not only of browsers.
- Bad, because the host proxy must listen on the Docker bridge and the host firewall
  must let the Docker networks reach port 443.
- Bad, because every anonymous visitor shares one rate-limit bucket until Q260 is
  solved.
- Bad, because one server is a single point of failure, and backups are not yet
  automated (Q264).

## Pros and cons of the options

### One image, one Compose stack, public names through the host proxy (proposed)

- Good, because it needs no code change and keeps every production rule.
- Good, because the presigned URLs carry the public host by construction.
- Bad, because the backend depends on the host proxy and on valid public certificates.

### Compose service names over plain http

- Good, because traffic stays inside the Docker network.
- Bad, because the production guard refuses an http issuer and storage endpoint, and
  loosening it needs its own decision.
- Bad, because presigned URLs would carry `http://minio:9000`, unusable by browsers,
  until a separate public presign endpoint exists (Q120).
- Bad, because Keycloak issues tokens for its public name anyway, so the issuer could not
  be the internal one.

### Managed services

- Good, because backups, upgrades and availability become the provider's.
- Bad, because it costs money every month and the server already has the memory.
- Bad, because a hosted identity provider changes the operational details of Q5.

### Separate images per process

- Good, because each image could drop what its process does not import.
- Bad, because the processes share almost every dependency; three images triple build
  and transfer time for little saving, and versions could drift between them.

## More information

- `docs/architecture/deployment.md`: install and upgrade commands, reverse-proxy
  snippets for nginx and Caddy, measured memory, backups.
- `docker/keycloak/production/README.md`: the production realm and the first
  administrator.
- ADR 0009 (object storage), ADR 0017 (rate limiting), ADR 0020 (guest submissions),
  open questions Q5, Q116, Q120, Q212, Q213, Q260 to Q265.
