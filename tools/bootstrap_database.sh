#!/bin/bash
#
# One-time bootstrap of the GemmaForge relational state inside the
# existing Supabase Postgres on this host. Creates:
#
#   - the `gemma_forge` database
#   - the `forge_admin` role (owner of the database; used only by
#     bootstrap_skill.sh and future migrations)
#   - `pgcrypto` and `vector` extensions inside gemma_forge
#
# Per-skill schemas + roles are handled by bootstrap_skill.sh, which
# is the script you run for each new skill.
#
# Idempotent: safe to re-run. Role and database creation use
# DO-blocks with IF NOT EXISTS semantics.
#
# Per ADR-0016. See docs/adr/0016-graphiti-neo4j-postgres-memory-stack.md.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Load GemmaForge .env to find the admin env location and pick up any
# overrides. .env is optional when running for the first time.
if [[ -f "${REPO_ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.env"
  set +a
fi

# Where Supabase's own .env lives. Defaults to the reference XR7620 path.
PG_ADMIN_ENV="${PG_ADMIN_ENV:-/data/docker/supabase/.env}"

if [[ ! -f "${PG_ADMIN_ENV}" ]]; then
  echo "bootstrap_database: cannot find Supabase admin env at ${PG_ADMIN_ENV}" >&2
  echo "Set PG_ADMIN_ENV in .env or export it before running." >&2
  exit 1
fi

# Read Supabase superuser credentials. The sourced file sets
# POSTGRES_USER, POSTGRES_PASSWORD, and POSTGRES_DB (which we ignore —
# we're creating our own database).
set -a
# shellcheck disable=SC1090
source "${PG_ADMIN_ENV}"
set +a

if [[ -z "${POSTGRES_USER:-}" || -z "${POSTGRES_PASSWORD:-}" ]]; then
  echo "bootstrap_database: Supabase admin env missing POSTGRES_USER/POSTGRES_PASSWORD" >&2
  exit 1
fi

# Name of the Supabase db container. Override if your local Supabase
# uses a different service name.
SUPABASE_DB_CONTAINER="${SUPABASE_DB_CONTAINER:-supabase-db}"

if ! docker ps --format '{{.Names}}' | grep -qx "${SUPABASE_DB_CONTAINER}"; then
  echo "bootstrap_database: container '${SUPABASE_DB_CONTAINER}' is not running" >&2
  exit 1
fi

GEMMA_DB="${PG_DATABASE:-gemma_forge}"
ADMIN_ROLE="forge_admin"

# Generate the admin role password if not already stored in .env. We
# persist it back to .env so bootstrap_skill.sh can reuse it.
if [[ -z "${PG_FORGE_ADMIN_PASSWORD:-}" || "${PG_FORGE_ADMIN_PASSWORD}" == "SET_BY_BOOTSTRAP_DATABASE" ]]; then
  PG_FORGE_ADMIN_PASSWORD="$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 40)"
  # Append to .env, creating the .env file if missing. Mode 600.
  {
    echo ""
    echo "# Admin role for the gemma_forge database. Created by"
    echo "# tools/bootstrap_database.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)."
    echo "PG_FORGE_ADMIN_ROLE=${ADMIN_ROLE}"
    echo "PG_FORGE_ADMIN_PASSWORD=${PG_FORGE_ADMIN_PASSWORD}"
  } >> "${REPO_ROOT}/.env"
  chmod 600 "${REPO_ROOT}/.env"
  echo "bootstrap_database: wrote forge_admin credentials to ${REPO_ROOT}/.env"
fi

echo "bootstrap_database: creating role '${ADMIN_ROLE}' and database '${GEMMA_DB}' in Supabase Postgres..."

# NOTE on Supabase compatibility: Supabase's `postgres` role is not a true
# unrestricted superuser. In particular, reassigning database ownership
# (`CREATE DATABASE ... OWNER <role>` or `ALTER DATABASE ... OWNER TO <role>`)
# requires role membership. The natural fix `GRANT <role> TO CURRENT_USER`
# caused a backend crash during our initial bring-up (auto-recovered via
# WAL, no data lost — see Journey 26 log). We therefore keep the database
# owned by postgres and give forge_admin the privileges it needs via GRANT
# instead. This yields the same operational outcome with no scary edges.

docker exec -i \
  -e PGPASSWORD="${POSTGRES_PASSWORD}" \
  "${SUPABASE_DB_CONTAINER}" \
  psql -v ON_ERROR_STOP=1 \
       -v admin_role="${ADMIN_ROLE}" \
       -v admin_pw="${PG_FORGE_ADMIN_PASSWORD}" \
       -v gemma_db="${GEMMA_DB}" \
       -U "${POSTGRES_USER}" -d postgres <<'SQL'
-- Create admin role if missing; update password if present.
SELECT format('CREATE ROLE %I WITH LOGIN CREATEDB PASSWORD %L',
              :'admin_role', :'admin_pw')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'admin_role') \gexec
SELECT format('ALTER ROLE %I WITH LOGIN CREATEDB PASSWORD %L',
              :'admin_role', :'admin_pw')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'admin_role') \gexec

-- Create the gemma_forge database owned by the current superuser. The
-- admin role gets privileges via GRANT below, not via ownership.
SELECT format('CREATE DATABASE %I', :'gemma_db')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'gemma_db') \gexec

-- Grant admin role enough to create schemas, connect, and use temp tables.
SELECT format('GRANT CREATE, CONNECT, TEMPORARY ON DATABASE %I TO %I',
              :'gemma_db', :'admin_role') \gexec
SQL

# Install extensions inside the gemma_forge database (as superuser —
# extensions owned by postgres is fine).
docker exec -i \
  -e PGPASSWORD="${POSTGRES_PASSWORD}" \
  "${SUPABASE_DB_CONTAINER}" \
  psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER}" -d "${GEMMA_DB}" <<'SQL'
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
SQL

echo "bootstrap_database: done."
echo "  Database : ${GEMMA_DB}"
echo "  Admin role: ${ADMIN_ROLE} (password stored in .env)"
echo ""
echo "Next: run tools/bootstrap_skill.sh --skill stig"
