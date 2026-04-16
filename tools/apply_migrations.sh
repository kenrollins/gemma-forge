#!/bin/bash
#
# Apply ordered SQL migrations from migrations/<skill>/ against the
# gemma_forge database. Connects through the Supabase pooler as
# forge_admin (which has CREATE on the database and CREATE on the
# per-skill schema).
#
# Idempotent: re-running is safe — every migration uses IF NOT EXISTS,
# and migrations_applied tracks what's been run for clarity. Migrations
# are applied in lexical order; name them NNNN_*.sql.
#
# Per ADR-0016, Phase B of the refactor plan.
#
# Usage: ./tools/apply_migrations.sh --skill stig

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

usage() {
  echo "Usage: $0 --skill <skill_name>" >&2
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

if [[ ! -f "${REPO_ROOT}/.env" ]]; then
  echo "apply_migrations: .env not found; run tools/bootstrap_database.sh first" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source "${REPO_ROOT}/.env"
set +a

if [[ -z "${PG_FORGE_ADMIN_PASSWORD:-}" ]]; then
  echo "apply_migrations: PG_FORGE_ADMIN_PASSWORD missing from .env" >&2
  exit 1
fi

GEMMA_DB="${PG_DATABASE:-gemma_forge}"
SUPABASE_DB_CONTAINER="${SUPABASE_DB_CONTAINER:-supabase-db}"
MIG_DIR="${REPO_ROOT}/migrations/${SKILL}"

if [[ ! -d "${MIG_DIR}" ]]; then
  echo "apply_migrations: no migrations directory at ${MIG_DIR}" >&2
  exit 1
fi

shopt -s nullglob
files=( "${MIG_DIR}"/*.sql )
if [[ ${#files[@]} -eq 0 ]]; then
  echo "apply_migrations: no .sql files in ${MIG_DIR}" >&2
  exit 0
fi

echo "apply_migrations: running ${#files[@]} migration(s) from ${MIG_DIR} as forge_admin..."

for f in "${files[@]}"; do
  name="$(basename "${f}" .sql)"
  echo "  → ${name}"
  # Stream the file into psql via stdin so the docker exec doesn't need
  # to bind-mount the migrations directory.
  # -h 127.0.0.1 forces TCP so password auth fires (Unix-socket connections
  # inside this container default to peer auth, which only matches the
  # 'postgres' OS user).
  docker exec -i \
    -e PGPASSWORD="${PG_FORGE_ADMIN_PASSWORD}" \
    "${SUPABASE_DB_CONTAINER}" \
    psql -v ON_ERROR_STOP=1 \
         -h 127.0.0.1 \
         -U forge_admin \
         -d "${GEMMA_DB}" \
         < "${f}"
done

echo "apply_migrations: done. Applied:"
docker exec -i \
  -e PGPASSWORD="${PG_FORGE_ADMIN_PASSWORD}" \
  "${SUPABASE_DB_CONTAINER}" \
  psql -t -A -F'  ' -h 127.0.0.1 -U forge_admin -d "${GEMMA_DB}" \
       -c "SELECT name, applied_at FROM ${SKILL}.migrations_applied ORDER BY name;"
