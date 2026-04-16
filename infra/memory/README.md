# Memory Tier — Neo4j + shared Supabase Postgres

Per [ADR-0016](../../docs/adr/0016-graphiti-neo4j-postgres-memory-stack.md)
and the refactor plan in `docs/drafts/memory-refactor-plan.md`.

The GemmaForge memory stack has two halves:

- **Reflective tier (Neo4j + Graphiti)** — new service on this host,
  project-scoped at `/data/neo4j/gemma-forge/`, brought up by this
  compose project's `memory` profile.
- **Relational tiers (Episodic, Semantic projection, run event history)** —
  stored in a `gemma_forge` database **inside the existing Supabase
  Postgres** on this host. Not a new service. Created once by the
  bootstrap scripts in `tools/`.

This matches the existing-services principle ("be a client, don't
duplicate") and matches the host's conventions: `/data/<service>/` for
shared host infrastructure, `/data/<service>/<project>/` for
project-scoped state, `/data/code/<project>/` for source.

## Host port assignments

Ports were chosen to avoid collisions with other services already
running on the reference XR7620 host (`ss -tlnp` was consulted; see
Journey 26 for the reasoning).

| Component        | Host port     | Container/process            | Notes                                    |
| ---------------- | ------------- | ---------------------------- | ---------------------------------------- |
| Supabase pooler  | `5432`        | `supabase-pooler` (existing) | Session mode. GemmaForge connects here.  |
| Neo4j HTTP UI    | `7474`        | `neo4j` (new, this project)  | Default, free                            |
| Neo4j Bolt       | `7687`        | `neo4j` (new, this project)  | Default, free                            |

All GemmaForge-scoped port bindings are **loopback-only**
(`127.0.0.1`). Supabase's own bindings are whatever Supabase
configured (typically `0.0.0.0:5432` behind Traefik on the XR7620).

## Data locations

```
/data/neo4j/gemma-forge/
  ├── data/         — Neo4j databases (one dir per named database; stig first)
  ├── logs/
  ├── plugins/      — APOC (bundled) + any Graphiti extras
  └── import/       — bulk load staging
```

No GemmaForge-owned Postgres data directory: our relational state
lives inside the Supabase-managed PGDATA at whatever path that stack
maps. The `gemma_forge` database is a logical peer of Supabase's own
`postgres` database inside the same cluster.

A second demo on this host that wants its own Neo4j gets
`/data/neo4j/<their-project>/` with a different port.

## Host preparation (one-time)

Creates the Neo4j data directories with the right ownership. Postgres
needs no host-level prep; the bootstrap scripts run inside the
Supabase container.

```bash
sudo mkdir -p /data/neo4j/gemma-forge/{data,logs,plugins,import}
# Neo4j container runs as UID 7474:7474
sudo chown -R 7474:7474 /data/neo4j/gemma-forge
```

## Credentials

All secrets live in the repo root `.env` file (gitignored, mode 600).
Neo4j password is generated at scaffold time. Postgres role passwords
are generated during bootstrap and written back to `.env`.

```
NEO4J_PASSWORD=...                    # written at scaffold time
PG_FORGE_ADMIN_PASSWORD=...           # written by bootstrap_database.sh
PG_FORGE_STIG_PASSWORD=...            # written by bootstrap_skill.sh
```

See `.env.example` for the public template.

## Bring-up sequence

From the repo root, in order:

```bash
# 1. Bootstrap the gemma_forge database inside the existing Supabase Postgres.
#    Creates the database and the forge_admin role (password auto-generated
#    and written to .env). Idempotent — safe to re-run.
./tools/bootstrap_database.sh

# 2. Bootstrap the first skill schema + scoped role.
#    Creates schema `stig` and role `forge_stig` inside gemma_forge,
#    with default privileges such that future tables are CRUD-able by
#    forge_stig only. Password auto-generated and written to .env.
./tools/bootstrap_skill.sh --skill stig

# 3. Start Neo4j.
docker compose --profile memory up -d neo4j

# 4. Verify.
docker compose ps neo4j
curl -s http://127.0.0.1:7474 | head -1

# 5. (Later) migrate the existing SQLite data to the new stores.
#    Implemented in Phase C of the refactor plan.
```

## Schema application

The bootstrap scripts create the database, schema, and role — not the
tables. Schema DDL (episodic tables, semantic projection tables,
event ingestion tables) is applied by the Phase C migration tool
`tools/migrate_sqlite_to_postgres.py`, so schema changes live under
version control without requiring re-running bootstrap.

Neo4j's per-skill partitioning is implemented via Graphiti's native
`group_id` mechanism — Neo4j Community Edition is single-database,
so we run one instance with the default `neo4j` database and isolate
skills by `group_id` on every node and edge. `tools/graphiti_init.py
--skill stig` (Phase B) creates the Graphiti indices and writes a
`Skill` marker node so the partition is visible from a Cypher shell.
Future skills run the same script with their own `--skill` value.
See ADR-0016 amendment 3 for the rationale.

## Teardown

Neo4j (destructive):

```bash
docker compose --profile memory down neo4j     # container only
sudo rm -rf /data/neo4j/gemma-forge/data       # data too (requires Phase C re-run)
```

Postgres: drop the database if you want a full reset. The forge_admin
role stays usable for the next bring-up.

```bash
docker exec -i supabase-db psql -U postgres -d postgres \
  -c "DROP DATABASE IF EXISTS gemma_forge;"
```

The SQLite backup at `memory/stig-rhel9.db` is retained for 30 days
after the initial cutover as a safety net.
