# 0009. Object storage for media and rasters

- Date: 2026-09-23
- Status: accepted
- Deciders: lead agent, maintainer

## Context and problem statement

Reports carry photos and videos taken in the field; research use needs rasters such as
satellite scenes, glacier and lake extents, and hazard maps. Photos from phones carry EXIF
metadata, including GPS coordinates and device details, which can identify the reporter or
the exact location of a family's home. Files are large, uploads happen over weak mobile
networks, and the same image is often submitted more than once.

Where are binary files stored, and how are privacy, deduplication and raster discovery
handled?

## Decision drivers

- Privacy: public copies must never expose EXIF or other embedded personal data; originals
  must be retrievable only by authorised roles.
- Provenance: originals are kept unchanged as evidence and can be verified by hash.
- Offline mobile clients: large uploads must not stream through the API process and must be
  retryable.
- Research consumers: rasters must be readable remotely and discoverable by space and time
  with a standard metadata model.
- Keep PostgreSQL small and fast to back up ([0002](0002-postgresql-with-postgis.md)).
- Small team and portability: no vendor lock-in; the same API in development and
  production.

## Considered options

1. S3-compatible object storage with metadata in PostgreSQL (chosen)
2. `bytea` columns in PostgreSQL
3. Local filesystem on the API host
4. A dedicated media service

## Decision outcome

Chosen option: **S3-compatible object storage**, with MinIO in development and any
S3-API-compatible service in production, because it keeps binaries out of the database and
lets clients upload directly.

- Binaries are never stored in PostgreSQL. The database stores metadata only: object key,
  content type, size, SHA-256 digest, owner, source and access class.
- Every upload keeps a **private original**, unchanged, readable only by authorised roles.
  Public access is served from a **derived copy with EXIF and other embedded metadata
  stripped**, produced by a background job ([0008](0008-taskiq-behind-a-task-queue-port.md)).
  An original is never made public.
- Files are content-addressed by **SHA-256**: identical content is stored once and linked to
  each report that referenced it.
- Clients upload with **presigned URLs** issued by the API after authorisation, with size and
  content-type limits. Presigned access is short-lived.
- Rasters are stored as **Cloud-Optimised GeoTIFFs** and indexed in PostgreSQL with metadata
  aligned to **STAC** (item, asset, bbox, datetime, footprint geometry), so they can be
  searched spatially and exposed through a STAC-compatible API later.
- The storage adapter is the only code that knows the S3 API. The client library, bucket
  layout, upload size limits, virus or content scanning and the production provider are
  decided in the phases that build media and rasters.

### Consequences

- Good, because database backups stay small and fast, and object storage scales
  independently.
- Good, because stripping EXIF from public copies while keeping originals private protects
  reporters and still preserves evidence for moderators.
- Good, because SHA-256 digests both deduplicate and let anyone verify that a file is the one
  that was submitted.
- Good, because COGs can be read by range requests without downloading whole scenes, and
  STAC alignment makes rasters discoverable by standard tools.
- Good, because any S3-compatible provider works, including self-hosted ones.
- Bad, because database and object store cannot share a transaction; an upload can exist with
  no metadata row, or a row can point to a missing object, so we need reconciliation and
  orphan-cleanup jobs.
- Bad, because presigned uploads bypass the API, so content must be validated after upload
  (type sniffing, size, image decoding) before it is used or derived.
- Bad, because content-addressed dedup means one object can back several reports; deleting
  or retracting one report must not delete the shared object, which requires reference
  tracking.
- Bad, because a SHA-256 of the original is a stable fingerprint; publishing it could let
  someone confirm that a specific private photo was submitted, so digests of private
  originals are not public.
- Bad, because MinIO is licensed AGPL-3.0; that is acceptable for an unmodified development
  service, but production operators should use a provider whose terms suit them.

## Pros and cons of the options

### S3-compatible object storage

- Good, because it is a de-facto standard API with many providers and direct-upload support.
- Bad, because it is another stateful service and breaks single-transaction consistency.

### `bytea` in PostgreSQL

- Good, because it is transactional with the metadata.
- Bad, because it bloats the database, backups and replication, and streams every byte
  through the API.

### Local filesystem

- Good, because it is the simplest option.
- Bad, because it ties files to one host, does not support presigned direct uploads, and
  complicates backups and scaling to more than one API instance.

### Dedicated media service

- Good, because it could encapsulate transformations and access control.
- Bad, because it is a second service for a small team to build and run, contradicting the
  modular monolith ([0001](0001-modular-monolith-with-hexagonal-layers.md)).

## More information

- STAC specification, stacspec.org; Cloud Optimized GeoTIFF, cogeo.org.
- `AGENTS.md` §5 (no personal data in public fields or logs), §7 (STAC, COG).
