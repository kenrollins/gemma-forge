#!/bin/bash
#
# Per-skill bootstrap inside the gemma_forge database. Creates:
#
#   - a schema named after the skill (e.g., `stig`)
#   - a role `forge_<skill>` scoped to that schema, with no cross-skill
#     privileges
#   - default privileges so future tables/sequences created in that
#     schema are readable/writable by the scoped role
#
# Role passwords are generated, written to .env, and reusable across
# re-runs (subsequent runs update the password and refresh grants).
#
# Schema DDL (tables, indexes) is applied separately by
# tools/migrate_sqlite_to_postgres.py — see docs/drafts/memory-refactor-plan.md.
#
# Per ADR-0016.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

usage() {
  cat <<EOF
Usage: $0 --skill <skill_name>

Creates the <skill_name> schema and forge_<skill_name> role inside
the gemma_forge database, using the forge_admin credentials stored
in .env by bootstrap_database.sh.

Example: $0 --skill stig
EOF
  exit 1
}

SKILL=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skill) SKILL="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown arg: $1" >&2; usage ;;
  esac
done

if [[ -z "${SKILL}" ]]; then usage; fi

# Validate skill name: lowercase letters, digits, underscores only.
if [[ ! "${SKILL}" =~ ^[a-z][a-z0-9_]*$ ]]; then
  echo "bootstrap_skill: skill name must match [a-z][a-z0-9_]*" >&2
  exit 1
fi

if [[ ! -f "${REPO_ROOT}/.env" ]]; then
  echo "bootstrap_skill: .env not found; run tools/bootstrap_database.sh first" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source "${REPO_ROOT}/.env"
set +a

if [[ -z "${PG_FORGE_ADMIN_PASSWORD:-}" ]]; then
  echo "bootstrap_skill: PG_FORGE_ADMIN_PASSWORD not set in .env; run bootstrap_database.sh first" >&2
  exit 1
fi

PG_ADMIN_ENV="${PG_ADMIN_ENV:-/data/docker/supabase/.env}"
# We need the superuser for ALTER DEFAULT PRIVILEGES on behalf of other
# roles; most ops below can run as forge_admin but it's cleaner to stay
# as superuser for idempotency.
set -a
# shellcheck disable=SC1090
source "${PG_ADMIN_ENV}"
set +a

SUPABASE_DB_CONTAINER="${SUPABASE_DB_CONTAINER:-supabase-db}"
GEMMA_DB="${PG_DATABASE:-gemma_forge}"

ROLE="forge_${SKILL}"
SCHEMA="${SKILL}"

# Generate the skill role password and persist to .env (variable named
# PG_FORGE_<SKILL_UPPER>_PASSWORD).
SKILL_UPPER="$(echo "${SKILL}" | tr '[:lower:]' '[:upper:]')"
PW_VAR="PG_FORGE_${SKILL_UPPER}_PASSWORD"
ROLE_VAR="PG_FORGE_${SKILL_UPPER}_ROLE"

existing_pw="${!PW_VAR:-}"
if [[ -z "${existing_pw}" || "${existing_pw}" == "SET_BY_BOOTSTRAP_SKILL" ]]; then
  NEW_PW="$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 40)"
  # Remove placeholder/stale lines, then append fresh.
  tmp="$(mktemp)"
  grep -vE "^(${ROLE_VAR}|${PW_VAR})=" "${REPO_ROOT}/.env" > "${tmp}" || true
  {
    cat "${tmp}"
    echo ""
    echo "# Skill role for '${SKILL}' schema, created/refreshed $(date -u +%Y-%m-%dT%H:%M:%SZ)."
    echo "${ROLE_VAR}=${ROLE}"
    echo "${PW_VAR}=${NEW_PW}"
  } > "${REPO_ROOT}/.env"
  rm -f "${tmp}"
  chmod 600 "${REPO_ROOT}/.env"
  echo "bootstrap_skill: wrote ${ROLE_VAR} and ${PW_VAR} to .env"
  SKILL_PW="${NEW_PW}"
else
  SKILL_PW="${existing_pw}"
fi

echo "bootstrap_skill: creating schema '${SCHEMA}' and role '${ROLE}' in ${GEMMA_DB}..."

docker exec -i \
  -e PGPASSWORD="${POSTGRES_PASSWORD}" \
  "${SUPABASE_DB_CONTAINER}" \
  psql -v ON_ERROR_STOP=1 \
       -v role="${ROLE}" \
       -v role_pw="${SKILL_PW}" \
       -v schema="${SCHEMA}" \
       -U "${POSTGRES_USER}" -d "${GEMMA_DB}" <<'SQL'
-- Create role if missing; refresh password if present.
SELECT format('CREATE ROLE %I WITH LOGIN PASSWORD %L', :'role', :'role_pw')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'role', :'role_pw')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec

-- Schema owned by the superuser. On Supabase, reassigning ownership
-- to forge_admin requires role membership and the workaround crashed
-- the backend during initial bring-up — we leave ownership with
-- postgres and grant the skill role scoped privileges instead.
SELECT format('CREATE SCHEMA IF NOT EXISTS %I', :'schema') \gexec

-- Scope the skill role: USAGE on its schema, nothing else.
SELECT format('GRANT USAGE ON SCHEMA %I TO %I', :'schema', :'role') \gexec

-- CRUD on all current and future tables / sequences in the schema.
SELECT format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO %I',
              :'schema', :'role') \gexec
SELECT format('GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA %I TO %I',
              :'schema', :'role') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
              :'schema', :'role') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %I',
              :'schema', :'role') \gexec

-- forge_admin needs CREATE on the schema so the migration tool
-- (running as forge_admin) can create tables inside it.
SELECT format('GRANT USAGE, CREATE ON SCHEMA %I TO forge_admin', :'schema') \gexec

-- Explicitly revoke PUBLIC on the schema so cross-skill access stays zero by default.
SELECT format('REVOKE ALL ON SCHEMA %I FROM PUBLIC', :'schema') \gexec

-- Set default search_path so the skill role auto-scopes to its own schema.
SELECT format('ALTER ROLE %I SET search_path TO %I', :'role', :'schema') \gexec
SQL

echo "bootstrap_skill: done."
echo "  Schema : ${SCHEMA}"
echo "  Role   : ${ROLE}"
echo "  Password stored in .env as ${PW_VAR}"
echo ""
echo "Connection string for the harness (session-mode pooler):"
echo "  postgresql://${ROLE}:<password>@${PG_HOST:-127.0.0.1}:${PG_PORT:-5432}/${GEMMA_DB}"
