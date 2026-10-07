#!/bin/sh
# Creates Keycloak's database and its owner in the shared PostgreSQL cluster
# (docker-compose.production.yml, ADR 0023). The postgres image runs this once, when
# the data volume is initialised; on an existing volume, run the same statements by
# hand (docker/keycloak/production/README.md, "Existing database volume").
#
# The role owns only its own database and is not a superuser; PUBLIC loses CONNECT on
# both databases, so Keycloak's role cannot open Yakhnama's and the other way round.
# psql quotes the values itself (:"name" for identifiers, :'value' for literals), so a
# password with any character is safe.
set -eu

: "${KEYCLOAK_DB_NAME:?KEYCLOAK_DB_NAME must be set}"
: "${KEYCLOAK_DB_USER:?KEYCLOAK_DB_USER must be set}"
: "${KEYCLOAK_DB_PASSWORD:?KEYCLOAK_DB_PASSWORD must be set}"

psql --set=ON_ERROR_STOP=1 --no-psqlrc \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --set=kc_db="$KEYCLOAK_DB_NAME" \
    --set=kc_user="$KEYCLOAK_DB_USER" \
    --set=kc_password="$KEYCLOAK_DB_PASSWORD" \
    --set=app_db="$POSTGRES_DB" <<'SQL'
CREATE ROLE :"kc_user" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'kc_password';
CREATE DATABASE :"kc_db" OWNER :"kc_user";
REVOKE ALL ON DATABASE :"kc_db" FROM PUBLIC;
REVOKE ALL ON DATABASE :"app_db" FROM PUBLIC;
SQL
