---
id: journey-27-building-the-dream-pass
type: journey
title: "Building the Dream Pass: One Session, Four Bugs, and a Closed Loop"
date: 2026-04-16
tags: [L4-orchestration, L1-data-infrastructure, reflexion-loop, cross-run-learning, context-management, decision, refactor]
related:
  - journey/26-dreaming-and-real-databases
  - journey/25-run-3-learning-plateaus
  - journey/22-context-graphs-and-the-memory-question
  - architecture/01-reflexive-agent-harness-failure-modes
one_line: "In a single session, we built the memory architecture pivot end-to-end: Postgres replaced SQLite, Neo4j hosts the Reflective tier, and the dream pass scored 1,746 lessons with outcome-driven confidence. Four real bugs surfaced during progressive testing — every one caught before it could hit Run 4."
---

# Building the Dream Pass: One Session, Four Bugs, and a Closed Loop

## The story in one sentence

We went from "SQLite with weighted lessons" to "Graphiti-on-Neo4j plus shared Supabase Postgres with a working dream pass that scores 1,746 lessons by outcome-driven confidence" in a single session, and the progressive testing discipline caught four bugs that would have crashed Run 4 if we had shipped without it.

## Why this is its own entry

[Entry 26](26-dreaming-and-real-databases.md) was the decision: adopt Graphiti, pivot to real databases, build a dream pass as the distinctive contribution. This entry is the build. The decision was the right one. The build is where the decisions met the host — and where four assumptions died on contact with reality.

## What we built (Phases A through D)

### Phase A — Infrastructure

Two services, not one. The original plan called for a dedicated Postgres instance alongside Neo4j. During bring-up, we realized this violated our own "be a client, don't duplicate" principle — Supabase Postgres was already running on the host. One `gemma_forge` database inside the existing Supabase, with per-skill schemas (`stig`) and per-skill roles (`forge_stig`), gives the same blast-radius isolation at zero additional operational cost.

Neo4j 5.26 community went to `/data/neo4j/gemma-forge/` — service-typed root, project-scoped underneath, same shape as the VM state at `/data/vm/gemma-forge/`. Ports 7474 and 7687, both free, localhost-only.

### Phase B — Schema

Eight Postgres tables in the `stig` schema, mapped from the retired SQLite store but with proper types: TIMESTAMPTZ instead of float epochs, BIGSERIAL primary keys, JSONB for config and event data, a GIN index on the 52K-event `run_events` table for the future Run Analyst's SQL queries.

Graphiti's standard indices and constraints initialized in Neo4j. One discovery here: Neo4j Community Edition is single-database. The "per-skill named database" plan from ADR-0016 was wrong. We pivoted to Graphiti's native `group_id` partition — one Neo4j instance, one underlying database, per-skill isolation via a property on every node and edge. Graphiti already indexes `group_id` in its FULLTEXT and RANGE indices, so retrieval is scoped correctly with no extra work.

### Phase C — Migration and cutover

The migration tool (`tools/migrate_sqlite_to_postgres.py`) ingested:
- 5 SQLite runs + 13 JSONL run files → 18 runs total
- 743 work items, 1,826 attempts, 1,738 lessons
- 51,906 events via Postgres COPY (fast)

The Reflective seeder (`tools/seed_reflective_from_runs.py`) wrote the full historical state to Neo4j: 18 Run nodes, 259 Rule nodes, 1,826 Attempt nodes, 1,738 Lesson nodes, and five edge types (ON_RULE, IN_RUN, DERIVED_FROM, LEARNED_IN, APPLIES_TO). A spot query traced a `whoami`/sudoers lesson back through `DERIVED_FROM` to its originating attempt — the exact provenance chain the dream pass will walk.

The hard cutover replaced `SQLiteMemoryStore` with `PostgresMemoryStore` behind the same `MemoryStoreProtocol`. One new module (`gemma_forge/harness/db.py`) manages the process-wide connection pool. All 21 property tests were rewritten to use per-test Postgres temp schemas — each test creates `mst_<uuid>`, applies the production migration SQL, runs, and drops the schema on teardown. Production schema and test schema cannot drift because they use the same DDL files.

### Phase D — The dream pass

`gemma_forge/dream/pass_.py` reads a completed run's outcomes from Postgres, computes a confidence signal per category, and updates both Neo4j (source of truth) and Postgres `lessons_current` (fast read-side projection). The confidence signal maps `[0% success, 100% success]` to `[-1.0, +1.0]`. Applied to Run 3's data:

| Category | Success Rate | Confidence Signal |
|---|---|---|
| service-config | 100% | +1.00 |
| authentication | 96% | +0.91 |
| kernel | 92% | +0.85 |
| audit | 33% | -0.35 |
| banner | 0% | -1.00 |

The harness's `load_lessons` and `load_all_lessons` now rank by `weight × (confidence + 1) / 2`, so a high-frequency lesson from a low-outcome category is suppressed relative to a moderate-frequency lesson from a high-outcome category. Before this change, a weight-1.0 audit lesson would outrank a weight-0.5 authentication lesson; after, the authentication lesson's +0.91 confidence gives it a higher composite score.

This closes the loop. The dream pass scores lessons → the harness loads scored lessons → the next run benefits → the next dream pass refines.

## The four bugs

Every one of these would have crashed Run 4 if we had shipped the code without the progressive testing discipline we followed. None was caught by the unit tests. All four required running the code against real infrastructure.

### Bug 1: Supavisor pooler requires tenant registration

The Supabase pooler (Supavisor) on port 5432 routes connections by a `user.tenant_id` naming convention. Our `forge_admin` and `forge_stig` roles aren't registered tenants. Every psycopg connection through the pooler failed with "Tenant or user not found."

**Fix:** Connect directly to the `supabase-db` container via its Docker network IP, bypassing the pooler entirely. The migration tool, the harness, and the dream pass all use this path. Documented in the `.env` and the ADR amendment.

### Bug 2: GRANT role TO CURRENT_USER crashes Supabase Postgres

The standard Postgres pattern for transferring database ownership — `GRANT <target_role> TO CURRENT_USER` — triggered a Postgres backend crash on the Supabase instance. The server auto-recovered via WAL (no data lost), but the bootstrap script's connection was severed.

Supabase's `postgres` role isn't an unrestricted superuser. The crash is likely an event-trigger interaction or a permissions-guard bug in their fork. We could have filed a Supabase issue, but the operational fix was simpler: leave objects owned by postgres, grant privileges explicitly instead of transferring ownership. All bootstrap scripts now follow this pattern.

### Bug 3: 1,926 banned patterns silently dropped

The legacy SQLite store persisted banned patterns as pseudo-attempts with `item_id='_global_ban'`. The migration's FK-existence filter correctly rejected these rows (no matching `work_items` entry) — but silently, without flagging that it had dropped 1,926 important cross-run records. The new schema's FK on `attempts(run_id, item_id)` → `work_items` would also have rejected Run 4's end-of-run ban writes, crashing `ralph.py` at the finish line.

**Fix:** Dedicated `bans` table (`migrations/stig/0002_bans_table.sql`). `PostgresMemoryStore.save_attempt` detects the `_global_ban` sentinel and routes to `bans` instead of `attempts`. `load_global_bans` reads from `bans` UNION the legacy `attempts.banned_pattern` column. The migration tool now backfills bans on a fresh `--reset` run. The 1,926 legacy rows deduped to 1,228 distinct patterns.

This bug was caught by the end-to-end smoke test (`tools/smoke_memory_e2e.py`) before any live run was attempted.

### Bug 4: Skill-name-to-schema mapping mismatch

The harness was normalizing `stig-rhel9` (the skill name) to `stig_rhel9` (replacing hyphens with underscores) and using that as the Postgres schema name. But the actual schema is `stig` and the role is `forge_stig`. The first live harness invocation failed immediately with "role forge_stig_rhel9 does not exist."

**Fix:** Extract the skill family from the first segment of the skill name (`stig-rhel9` → `stig`). The variant (`rhel9`) is a qualifier, not part of the schema namespace.

This bug was caught by the live 5-rule integration test against real vLLM + real VM — the only testing phase that exercises the full `ralph.py` CLI path.

## What the progressive testing discipline looked like

Five test layers, each catching bugs the previous couldn't:

1. **Unit tests (21 property tests):** per-test temp Postgres schemas. Caught basic CRUD bugs in the `PostgresMemoryStore` implementation. Fast, repeatable, zero infrastructure dependency beyond Postgres.
2. **End-to-end smoke test (`smoke_memory_e2e.py`):** exercises every method ralph.py uses, in a scratch schema. Caught Bug 3 (silent ban drop) because it writes a banned pattern via `save_attempt` and then reads it back via `load_global_bans`.
3. **Production data read test:** reads the real `stig` schema to verify migrated data is queryable by the production code path. Caught the lesson-loading and category-stats shapes.
4. **Live 5-rule integration test:** actually runs ralph.py against vLLM + the VM with a tight budget (5 rules, 5 min/rule). Caught Bug 4 (skill-name mapping) because it's the only layer that exercises the CLI argument path + skill loader + schema name derivation together.
5. **Dream pass execution:** ran against Run 3's real outcomes. Validated that confidence scores land in both Neo4j and Postgres and that the composite ranking changes lesson ordering in the expected direction.

The total testing time across all five layers was about 25 minutes. The time saved by not shipping four crash-causing bugs into a 16-hour overnight run is incalculable.

## What this means for the thesis

The thesis of the project is that the agentic harness shapes outcomes as much as the model, and that cross-run memory lets the same model improve across runs. Entry 26 identified the gap: memory was accumulating but not curating. The dream pass is the curation mechanism.

Before: lessons ranked by frequency. High-frequency lessons from low-outcome categories (audit, with 778 lessons and 33% success) dominated the prompt, misdirecting the Worker on problems it would otherwise handle cleanly.

After: lessons ranked by frequency × outcome-driven confidence. Service-config lessons (100% success) outrank audit lessons (33% success) even when audit has more accumulated weight. The Worker's prompt now carries lessons that empirically helped, not just lessons that appeared often.

Run 4 will be the first run that starts with dream-informed lesson ranking. Whether that translates to a measurable fix-rate improvement over Run 3's 60% is an empirical question we'll answer tomorrow. The architecture is in place. The loop is closed. The data will tell us whether the dream pass was worth building.

## Looking forward

Run 4 tonight is the validation point. If the dream-informed lessons produce a fix-rate improvement (even modest — the whitepaper framing is honest about diminishing returns), the dream pass earns its place in the architecture. If it doesn't, we learn something about the gap between category-level credit and per-rule credit, and V2 of the dream pass gets scoped accordingly.

Beyond Run 4:
- **V2 dream pass:** supersession detection (Reflector text analysis), abstraction-loss recovery (re-hydrate from source attempt traces), A-MEM-style semantic linking. All deferred to V2 because they require LLM calls.
- **Dashboard Memory tab:** the dream report is currently markdown; rendering it in the dashboard is the next UI work.
- **Prompt logging:** the harness should log which lesson IDs were loaded into each rule's Worker prompt, enabling per-rule credit assignment in V2.
- **Second skill:** the dream pass is harness-level, not skill-level. Adding a second skill now validates that the dream architecture transfers.

---

## Related

- [`journey/26`](26-dreaming-and-real-databases.md) — the decision to pivot; honest assessment of catching up to the frontier.
- [`journey/25`](25-run-3-learning-plateaus.md) — the Run 3 data that made the pivot necessary.
- [`journey/22`](22-context-graphs-and-the-memory-question.md) — the earlier SQLite decision, now replaced.
- [`adr/0016`](../../adr/0016-graphiti-neo4j-postgres-memory-stack.md) — architectural decision with three implementation amendments.
