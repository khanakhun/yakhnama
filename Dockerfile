# syntax=docker/dockerfile:1.7
#
# Production image of the Yakhnama backend (ADR 0023). One image runs every process:
#
#   API        (default)  uvicorn yakhnama.main:create_app --factory ...
#   worker                taskiq worker yakhnama.platform.tasks.worker:broker
#   scheduler             taskiq scheduler yakhnama.platform.tasks.worker:scheduler
#   one-shot              alembic upgrade head | python -m yakhnama.seed |
#                         python -m yakhnama.seed.boundaries
#
# docker-compose.production.yml sets the command of each service. The image holds the
# runtime dependencies only (Poetry's `main` group), the application source, the
# migrations and the committed data the seed and the boundary load read. It holds no
# tests, documentation, development tools or configuration: every setting comes from
# YAKHNAMA_* environment variables at run time.
#
# The base image is pinned by digest, like the images in the compose files; update the
# tag and the digest together.

ARG PYTHON_IMAGE=python:3.13-slim@sha256:bf44cdfcb76cd3b41e879bc058fc37ec5872002ccfde7fcb765e218cde0cd79c

# --------------------------------------------------------------------------- #
# Builder: resolve the locked runtime dependencies into /opt/venv             #
# --------------------------------------------------------------------------- #
FROM ${PYTHON_IMAGE} AS builder

# Poetry is the only package manager (AGENTS.md §4). It lives in its own virtualenv
# and installs into /opt/venv, the environment the runtime stage copies; every
# dependency in poetry.lock ships a manylinux wheel for CPython 3.13 (asyncpg,
# shapely, pyarrow, pillow, cryptography, uvloop), so no compiler is needed.
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_CACHE_DIR=/tmp/poetry-cache \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN python -m venv /opt/poetry \
    && /opt/poetry/bin/python -m pip install "poetry==2.5.1" \
    && python -m venv --without-pip /opt/venv

WORKDIR /app

# Dependencies first, so a source change does not reinstall them.
COPY pyproject.toml poetry.lock README.md LICENSE ./
RUN /opt/poetry/bin/poetry install --only main --no-root \
    && rm -rf /tmp/poetry-cache

# The project itself, installed as a path dependency of the venv: importlib.metadata
# needs its dist-info (the API reports version("yakhnama")), and the .pth file Poetry
# writes points at /app/src, where the runtime stage puts the same source.
COPY src ./src
RUN /opt/poetry/bin/poetry install --only main \
    && rm -rf /tmp/poetry-cache

# Trim what no code path imports:
# - third-party test suites, C headers and Cython sources;
# - pyarrow's Flight libraries (31 MB): only `import pyarrow.flight` loads them, and the
#   GeoParquet exporter uses pyarrow.parquet (Substrait stays: the core links it);
# - botocore's API models of every AWS service but S3 (aiobotocore loads a model only
#   for the client it creates; the backend creates S3 clients only).
# The import check below fails the build if a dependency update starts needing any of it.
ARG SITE_PACKAGES=/opt/venv/lib/python3.13/site-packages
RUN find /opt/venv -depth -type d \( -name tests -o -name __pycache__ \) -exec rm -rf {} + \
    && find /opt/venv \( -name '*.pyx' -o -name '*.pxd' \) -delete \
    && rm -rf "${SITE_PACKAGES}/pyarrow/include" \
              "${SITE_PACKAGES}"/pyarrow/libarrow_flight.so* \
              "${SITE_PACKAGES}"/pyarrow/_flight.*.so \
    && find "${SITE_PACKAGES}/botocore/data" -mindepth 1 -maxdepth 1 -type d ! -name s3 \
         -exec rm -rf {} + \
    && python -c "import pyarrow, pyarrow.parquet, pyarrow.compute, shapely, PIL.Image, \
asyncpg, aiobotocore.session; \
import botocore.session as s; s.get_session().get_service_model('s3'); \
import yakhnama.main, yakhnama.platform.tasks.worker" \
    && find /opt/venv -depth -type d -name __pycache__ -exec rm -rf {} +

# --------------------------------------------------------------------------- #
# Runtime                                                                     #
# --------------------------------------------------------------------------- #
FROM ${PYTHON_IMAGE} AS runtime

# WEB_CONCURRENCY is uvicorn's own default for --workers.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    WEB_CONCURRENCY=2

RUN groupadd --system --gid 10001 yakhnama \
    && useradd --system --uid 10001 --gid yakhnama --home-dir /app \
       --no-create-home --shell /usr/sbin/nologin yakhnama

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
# Read relative to the working directory, matching the settings defaults:
# reference_data_dir, boundary_source_file and ingestion_fixtures_dir.
COPY data/reference ./data/reference
COPY data/boundaries ./data/boundaries
COPY data/fixtures/ingestion ./data/fixtures/ingestion

# The only path the application writes: the boundary archive cache
# (boundary_cache_dir); the compose file mounts a volume here.
RUN mkdir -p /app/.cache/boundaries && chown yakhnama:yakhnama /app/.cache/boundaries

USER yakhnama:yakhnama

EXPOSE 8000

# Liveness only: a database outage must not mark the API container unhealthy and
# restart it (platform/health.py); /health/ready reports the database separately.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=4)"]

# --no-proxy-headers: only the portal's server reaches the API, over loopback, and it
# sends no X-Forwarded-For (it would carry the visitor's own value). Trusting the header
# would let a caller choose the address it is rate-limited on (ADR 0017, ADR 0023).
# --no-access-log: uvicorn's access line carries the client address and the full query
# string; platform/logging.py already silences it, this skips formatting it at all.
CMD ["uvicorn", "yakhnama.main:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--no-proxy-headers", "--no-access-log", "--no-server-header"]
