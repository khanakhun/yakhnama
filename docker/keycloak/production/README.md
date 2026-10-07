# Keycloak in production

The production stack (`docker-compose.production.yml`, ADR 0023) runs Keycloak from the
image built by this directory's `Dockerfile`: the pinned upstream image with
PostgreSQL, the health endpoints and a local (single-node) cache built in, started with
`start --optimized`. Its realms live in their own database, `keycloak`, in the same
PostgreSQL cluster as Yakhnama's; `postgres-init/010-keycloak-database.sh` creates the
database and its owner when the data volume is first initialised.

The development realm, `docker/keycloak/yakhnama-realm.json`, must never be imported
into production as it is: it has four demo users with `-dev-only` passwords, a
development client that trades a password for a token (`yakhnama-dev-cli`), portal
redirect URIs on `localhost`, mail sent to Mailpit and `sslRequired: none`.

## 1. Derive the production realm

`python -m yakhnama.platform.keycloak_realm` turns the development realm into a
production one. It removes every user and the development client, registers the
portal's client `yakhnama-web` for exactly one origin (callback
`<origin>/auth/callback`, post-logout `<origin>/*`, web origin `<origin>`; Q212), sets
`sslRequired` to `external`, and replaces the mail settings with placeholders that
Keycloak fills from its environment when it imports the file (`KC_SMTP_*`; the password
never lands in the file). Roles, the user profile, the password policy, self-registration
with a verified email address, brute-force protection and the social providers (off
unless `KC_GOOGLE_ENABLED` or `KC_FACEBOOK_ENABLED` is `true`) are kept. It refuses an
origin that is not `https`, and refuses its own result if any `localhost`, loopback
address or development marker is left in it.

From a checkout with Poetry:

```bash
poetry run python -m yakhnama.platform.keycloak_realm \
  --portal-origin https://example.org \
  --output docker/keycloak/production/import/yakhnama-realm.production.json
```

Or with the production image only (the realm file is read from standard input):

```bash
mkdir -p docker/keycloak/production/import
docker run --rm -i yakhnama-api:local \
  python -m yakhnama.platform.keycloak_realm --portal-origin https://example.org --source - \
  < docker/keycloak/yakhnama-realm.json \
  > docker/keycloak/production/import/yakhnama-realm.production.json
```

`docker/keycloak/production/import/` is git-ignored: the file names the real domain. The
compose file mounts the directory named by `KEYCLOAK_REALM_IMPORT_DIR` (this one by
default) at `/opt/keycloak/data/import`, and `start --import-realm` imports it on the
first start. Keycloak skips a realm that already exists, so later edits to the file do
nothing; change a running realm in the admin console.

## 2. Fill in the mail and admin settings

In `.env.production` (from `production.env.example`):

- `KC_SMTP_HOST`, `KC_SMTP_PORT` (587 with STARTTLS), `KC_SMTP_FROM`, `KC_SMTP_USER`,
  `KC_SMTP_PASSWORD`: a transactional relay such as Brevo, Resend or Amazon SES, sending
  from `no-reply@` on the project's own domain with SPF, DKIM and DMARC in place (Q213).
  Self-registration needs a verified address, so sign-up does not work until mail does.
  After the import the values sit in Keycloak's database; you may remove them from the
  env file, and change them later under *Realm settings → Email*.
- `KEYCLOAK_ADMIN_USER` and `KEYCLOAK_ADMIN_PASSWORD`: a temporary admin of the `master`
  realm, created on the first start only.

## 3. Start and secure the admin account

The admin console is served on the loopback port only (`KC_HOSTNAME_ADMIN`), and the
reverse proxy publishes `/realms/` and `/resources/` only, so the console is never on
the public name. Reach it through an SSH tunnel on the same port number:

```bash
ssh -L 8180:127.0.0.1:8180 <server>      # KEYCLOAK_HOST_PORT
# then open http://127.0.0.1:8180/admin/ in your browser
```

1. Sign in as the bootstrap admin, create a permanent admin user in the `master` realm
   with a strong password and an OTP, give it the `admin` role, sign in as that user and
   delete the bootstrap admin.
2. In the `yakhnama` realm, check *Realm settings → Email → Test connection*.
3. Leave *Identity providers → google / facebook* off until the project owns the apps
   (Q214, Q215); `docs/architecture/auth.md`, "Sign-up and social sign-in", has the steps.

## 4. Create the first Yakhnama administrator

Roles come from the realm, and the backend copies them **once**, the first time a person
signs in (`docs/architecture/auth.md`, "First-sight mirroring"). So grant the role before
that first sign-in:

1. *Users → Add user* in the `yakhnama` realm (or let the person sign up and verify their
   address, without signing in to the portal yet).
2. *Role mapping → Assign role*: `admin` and `moderator` (the realm's default role adds
   `citizen`).
3. The person signs in to the portal. `GET /api/v1/me` then lists the roles.

A role added later in Keycloak is not picked up by an existing user; an admin changes it
inside Yakhnama instead (Q50).

## Brute-force protection and other settings to review

The derived realm keeps the development realm's brute-force protection: temporary
lockout after 5 failures, waits growing by a minute up to 15 minutes, counters reset
after 12 hours. Review before launch:

- *Realm settings → Sessions and Tokens*: the access token lives 15 minutes; the portal
  refreshes it.
- *Realm settings → Security defenses*: headers are on by default; keep them.
- `sslRequired: external` lets plain http through only from private addresses, which is
  how the API and the admin tunnel reach Keycloak inside the host.
- Keycloak's pages have no Urdu yet (Q216).

## Existing database volume

`010-keycloak-database.sh` runs only when PostgreSQL initialises an empty volume. On a
volume that already exists, create the database once by hand, with the values from
`.env.production`:

```bash
docker compose -f docker-compose.production.yml --env-file .env.production \
  exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

```sql
CREATE ROLE keycloak LOGIN PASSWORD '<KEYCLOAK_DB_PASSWORD>';
CREATE DATABASE keycloak OWNER keycloak;
REVOKE ALL ON DATABASE keycloak FROM PUBLIC;
```
