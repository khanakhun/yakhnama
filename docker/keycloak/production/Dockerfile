# syntax=docker/dockerfile:1.7
#
# Keycloak for production (ADR 0023): the upstream image with its build-time options
# baked in, so the container starts with `start --optimized` and skips the
# re-augmentation a plain `start` does on every boot (tens of seconds and a few
# hundred MB of transient memory). Build-time options cannot change at run time; a
# change here means rebuilding the image. Runtime options (database URL and
# credentials, hostname, proxy headers, heap) come from docker-compose.production.yml.
#
# Pinned by digest like docker-compose.yml's Keycloak; update both together.

ARG KEYCLOAK_IMAGE=quay.io/keycloak/keycloak:26.2@sha256:4883630ef9db14031cde3e60700c9a9a8eaf1b5c24db1589d6a2d43de38ba2a9

FROM ${KEYCLOAK_IMAGE} AS builder

# postgres: the realm lives in its own database of the shared PostgreSQL cluster.
# health: /health/ready on the management port (9000), for the compose healthcheck.
# cache=local: one node, so no cluster discovery and no distributed caches.
ENV KC_DB=postgres \
    KC_HEALTH_ENABLED=true \
    KC_METRICS_ENABLED=false \
    KC_CACHE=local

RUN /opt/keycloak/bin/kc.sh build

FROM ${KEYCLOAK_IMAGE}

COPY --from=builder /opt/keycloak/ /opt/keycloak/

ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
CMD ["start", "--optimized"]
