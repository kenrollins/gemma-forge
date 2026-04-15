# Memory Tier — Postgres + Neo4j

Per [ADR-0016](../../docs/adr/0016-graphiti-neo4j-postgres-memory-stack.md)
and the refactor plan in `docs/drafts/memory-refactor-plan.md`.

This directory hosts the bring-up assets for the GemmaForge memory
stack: a Postgres instance (Episodic, Semantic projection, run event
history) and a Neo4j instance with Graphiti (Reflective tier,
bi-temporal graph memory).

Both services are defined in the root [`docker-compose.yml`](../../docker-compose.yml)
under the `memory` profile.

## Host port assignments

Ports were chosen to avoid collisions with other services already
running on the reference XR7620 host (`ss -tlnp` was consulted —
see Journey 26 for the rationale).

| Service        | Host port     | Container port | Notes                                           |
| -------------- | ------------- | -------------- | ----------------------------------------------- |
| Postgres       | `5433`        | `5432`         | Host `5432` is occupied (Supabase stack)        |
| Neo4j HTTP UI  | `7474`        | `7474`         | Default, free on this host                      |
| Neo4j Bolt     | `7687`        | `7687`         | Default, free on this host                      |

All port bindings are **loopback-only** (`127.0.0.1`). These services
are never exposed on `0.0.0.0`. Remote access, if needed, goes
through the host's Traefik or an SSH tunnel.

If ports change later, update:
- `docker-compose.yml` service port mappings
- `.env` (the `*_HOST_PORT` variables)
- This table
- The refactor plan

## Data locations

Following the `/data/<service>/` convention
([ADR-0012](../../docs/adr/0012-data-host-layout-convention.md)):

```
/data/postgres/
  ├── data/         — PGDATA (Postgres cluster state)
  ├── backups/      — pg_dump output, rotation handled externally
  └── logs/         — optional, Postgres logs to stderr by default

/data/neo4j/
  ├── data/         — Neo4j databases (one dir per named database)
  ├── logs/
  ├── plugins/      — APOC + Graphiti jars if needed
  └── import/       — bulk load staging
```

These directories are **not** committed to the repo and must exist on
the host before first bring-up. See "Host preparation" below.

## Host preparation (one-time)

These commands run on the XR7620 host, not inside any container.
They require root or sudo.

```bash
# Create data directories
sudo mkdir -p /data/postgres/{data,backups,logs}
sudo mkdir -p /data/neo4j/{data,logs,plugins,import}

# Postgres container runs as UID 999 (postgres)
sudo chown -R 999:999 /data/postgres

# Neo4j container runs as UID 7474 by default (neo4j)
sudo chown -R 7474:7474 /data/neo4j
```

## Credentials

Secrets live in the repo root `.env` file, which is gitignored.
Bootstrap from the template:

```bash
cp .env.example .env
# edit .env — set POSTGRES_PASSWORD and NEO4J_PASSWORD to strong values
```

See `.env.example` at repo root for the full set of variables.

## Bring-up

From the repo root:

```bash
docker compose --profile memory up -d
```

Verify:

```bash
# Postgres
docker compose exec postgres psql -U forge -d postgres -c '\l'

# Neo4j (web UI)
curl -s http://127.0.0.1:7474 | head -1
```

## Initial schema / database setup

The Postgres container auto-runs scripts in `./postgres-init/` on
first boot (empty data dir). The init script creates the
`gemma_forge_stig` database and base roles. Schema DDL is applied
separately by the migration tool in `tools/migrate_sqlite_to_postgres.py`
(Phase C of the refactor plan) so schema changes live under version
control without requiring container rebuilds.

The Neo4j named database for the STIG skill is created by
`tools/graphiti_init.py --skill stig` (Phase C).

## Teardown (destructive)

```bash
# Stop and remove containers only (data preserved)
docker compose --profile memory down

# Destroy data too (DANGEROUS — requires re-migration from SQLite backup)
sudo rm -rf /data/postgres/data /data/neo4j/data
```

The SQLite backup at `memory/stig-rhel9.db` is retained for 30 days
after the initial cutover as a safety net.
