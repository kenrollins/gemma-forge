# ADR-0016: Graphiti-on-Neo4j for Reflective memory; Postgres for Episodic/Semantic; SQLite retired

- **Status:** Accepted (amended 2026-04-16 — Postgres pivot to shared Supabase; per-skill schemas; Neo4j path moved to `/data/neo4j/gemma-forge/`)
- **Date:** 2026-04-15
- **Deciders:** Ken Rollins
- **Related:** [ADR-0012](0012-data-host-layout-convention.md), [ADR-0014](0014-triton-vllm-director-shared-host-service.md), [Journey 22](../journal/journey/22-context-graphs-and-the-memory-question.md), [Journey 26](../journal/journey/26-dreaming-and-real-databases.md)

## Context

Three completed runs against 270 DISA STIG rules produced a clear
empirical picture of how the cross-run memory system behaves:

- **Run 1** (no prior memory): 35% fix rate.
- **Run 2** (memory pipeline working): 58% fix rate, 59 wins vs 1 regression.
- **Run 3** (more memory, same architecture): 60% fix rate, 14 wins vs
  10 regressions. 26% more tokens, three extra hours, scattered
  regressions across categories.

Run 3 exposed two failure modes that the current storage layer cannot
address without structural change:

1. **Environment fidelity.** Lessons accumulated under one target-system
   state misdirect the Worker when the target is rebuilt to a clean
   baseline. The memory model has no notion of "what was true *when*
   this lesson was learned."
2. **Abstraction loss.** When the Reflector distills a successful
   approach into a lesson, operational detail is lost. The canonical
   example: Run 1 succeeded on `sudo_remove_nopasswd` by running
   `whoami` first, then preserving that entry. The distilled lesson
   said "preserve the agent's identity" — principle correct, procedure
   missing. Run 2 failed nine times before the skill prompt was
   updated to add the `whoami` step manually.

Separately, the current storage is SQLite with one database file per
skill (see [ADR-0012](0012-data-host-layout-convention.md) and the
per-skill DB rename recorded in the phase status). This was the right
early call — zero ops, embedded, fast enough. But:

- Two failure modes above are fundamentally graph-shaped (supersession,
  provenance, evolution). Forcing them through a relational-only schema
  is reinventing bi-temporal graph primitives that other projects have
  already solved correctly.
- Multi-skill growth is coming. Each new skill adds another SQLite
  file, another backup target, another migration path. One managed DB
  instance with per-skill isolation scales more cleanly.
- The dashboard + harness read/write pattern is about to get more
  concurrent once the Run Analyst chat interface lands. SQLite's WAL
  mode handles this but starts showing edges as the working set grows.

The broader memory-systems landscape as of April 2026 is also a
different place than it was in March:

- **Graphiti / Zep** (Apache 2, runs on Neo4j) is production-grade with
  bi-temporal validity intervals, provenance to source episodes, and
  hybrid retrieval at 300ms P95 ([arxiv 2501.13956](https://arxiv.org/abs/2501.13956)).
- **A-MEM** (NeurIPS 2025, [arxiv 2502.12110](https://arxiv.org/abs/2502.12110))
  implements Zettelkasten-style memory evolution where new memories
  trigger updates to related existing memories.
- **OpenClaw Dreaming** ships a three-stage consolidation cycle with
  explicit promotion gates.
- **Claude Code Auto Dream** and other production systems demonstrate
  that between-session memory consolidation is a shipping feature, not
  a research artifact.
- **Sleep-inspired consolidation papers** ([arxiv 2603.14517](https://arxiv.org/html/2603.14517v1),
  SleepGate) establish the research thread on separating consolidation
  from online inference.

The "sovereign edge" principle is **no cloud, no phone-home, no
external dependencies** — it is not **no infrastructure**. The
XR7620 is a real production edge server with Docker-hosted production
workloads, Supabase (Postgres), Qdrant, ClickHouse, MinIO, and Redis
already running. One more Neo4j instance and a Postgres instance
scoped to GemmaForge do not violate sovereignty; they match it.

## Decision

Adopt a three-part memory storage architecture:

1. **Reflective tier: Graphiti-on-Neo4j.** Bi-temporal graph memory
   for cross-run historical state. One Neo4j instance, one named
   database per skill. Graphiti provides the storage, temporal logic,
   and hybrid retrieval. GemmaForge builds the **dream pass** — the
   between-runs consolidation job that performs outcome-driven credit
   assignment, supersession, abstraction-loss recovery, environment
   tagging, and semantic linking — as a layer on top.
2. **Episodic + Semantic + run history: Postgres.** Single instance,
   per-skill databases (`gemma_forge_stig`, future skills get their
   own). Postgres replaces all current SQLite usage. JSONL event logs
   are ingested into Postgres for queryable history.
3. **SQLite: retired.** Current per-skill `*.db` files are migrated
   to Postgres via a one-shot tool kept in `tools/`.

Host layout:

```
/data/neo4j/       — Reflective tier (Graphiti on Neo4j)
/data/postgres/    — Episodic, Semantic projection, run history
/data/triton/      — inference (existing)
/data/vm/          — target VMs (existing)
```

The distinctive contribution of GemmaForge on the memory side is
**outcome-driven credit assignment applied to agentic infrastructure
operations**, not the memory primitives. The primitives are adopted
from the current frontier.

## Alternatives considered

- **Stay on SQLite, add a graph-shaped schema on top.** Pragmatic,
  low-risk, preserves continuity. Rejected: reimplementing bi-temporal
  edge validity and recursive path queries in SQLite is expensive
  yak-shaving that produces an inferior version of what Graphiti
  already does. Hits a ceiling within months.

- **Kuzu instead of Neo4j for the Reflective tier.** Embedded graph
  DB, single-file, Apache 2, Cypher-compatible. Real option. Rejected
  for two reasons: (1) Neo4j familiarity is higher; (2) Graphiti is
  Neo4j-native, so adopting it wholesale requires Neo4j regardless.
  Kuzu's killer feature — embedded deployment — is not a real
  advantage on the XR7620, which has the capacity to run a real DB
  service without strain.

- **Build our own bi-temporal graph memory directly on Neo4j,
  without Graphiti.** Defensible on "learn some shit" grounds, and
  would give us full control over the schema. Rejected: weeks of
  primitive-level work that does not differentiate the project.
  The distinctive contribution is the dream pass and credit
  assignment, not the edge-validity implementation. Time spent
  rebuilding bi-temporal logic is time not spent on what nobody else
  has.

- **Adopt Graphiti AND Zep's memory layer service together.** Zep is
  commercial-hosted-or-self-hosted. Evaluated briefly; Graphiti alone
  (which is the OSS core) is sufficient for our needs. Taking on the
  full Zep stack adds surface area without adding capability we need.

- **MariaDB/MySQL instead of Postgres.** Works. Rejected: Postgres's
  JSONB, pgvector extension, and general SQL richness fit the mix
  (relational attempt tracking + JSON event data + future embedding
  storage) better. No compelling reason to pick MariaDB over Postgres
  for this specific workload.

- **DuckDB instead of Postgres.** Great for analytics; wrong shape for
  transactional concurrent-writer workloads. Considered and dismissed
  quickly.

## Consequences

### Positive

- Bi-temporal memory with provenance becomes a solved problem
  (Graphiti), freeing engineering effort for the dream pass and
  credit assignment — the parts that are actually novel.
- Two named failure modes (environment fidelity, abstraction loss)
  now have clean architectural homes rather than being wallpapered
  over in skill prompts.
- Multi-skill isolation is cleaner: per-skill databases in Postgres,
  per-skill named databases in Neo4j. No per-skill files to manage.
- Whitepaper Section 4.4 gains a stronger narrative beat: "we stood
  on the 2026 frontier and built the layer above it" instead of
  "we built our own graph memory."
- Future dashboard work (Run Analyst chat, cross-run comparison,
  decision provenance traces) becomes easier when the data lives in
  a real query engine.
- Sovereign-edge thesis is strengthened, not weakened — "real
  databases on the edge server" matches how Federal customers
  actually run hardware.

### Negative / accepted trade-offs

- Two new managed services on the host (Neo4j, Postgres). Backup,
  upgrade, and operational load increases, though neither is novel
  to Ken or to the XR7620.
- SQLite's operational simplicity is gone. A fresh clone of the repo
  now has real infrastructure prerequisites.
- Migration work: one-shot SQLite → Postgres tool, Graphiti schema
  definition, dream-pass implementation. Estimated "a few days of
  real work before Run 4."
- Adopting Graphiti is a dependency on another project's roadmap and
  design choices. Mitigation: Apache 2, self-hosted, fully auditable.
- Dashboard visualization gains graph rendering complexity (Dream
  tab showing supersession events, provenance chains).
- [ADR-0012](0012-data-host-layout-convention.md) is extended (not
  superseded) to include `/data/neo4j/` and `/data/postgres/` as
  standard GemmaForge host services.
- Re-opens the database decision recorded in
  [Journey 22](../journal/journey/22-context-graphs-and-the-memory-question.md).
  That decision (SQLite) was correct for its time and is replaced,
  not invalidated, by the scale and shape changes since.

## Amendment — 2026-04-16 (during Phase A bring-up)

Two changes landed during Phase A bring-up, both small-scope and
fully consistent with the original decision's intent. Recorded here
rather than as a superseding ADR because neither reverses a prior
call; they refine where the services live.

### Amendment 1: Postgres pivot to shared Supabase, per-skill schemas

The original decision called for a **GemmaForge-scoped Postgres
instance** at `/data/postgres/`, with **one database per skill**
(`gemma_forge_stig`, etc.). During Phase A implementation, two
points were raised and accepted:

1. A Supabase Postgres is already running on the reference host
   (`supabase-db` container, Postgres 15.8.1, with `pgvector` 0.8.0
   and `pgcrypto` both available). The existing-services inventory
   memory ("be a client, don't duplicate") specifically applies.
   Duplicating Postgres was a violation of that principle the
   original ADR text did not catch.
2. Separate databases per skill in someone else's Postgres instance
   enlarges the visible footprint and obstructs cross-skill queries
   the dashboard and Run Analyst will eventually want. Per-skill
   **schemas inside one database** give the same blast-radius
   properties (via scoped roles with `USAGE` limited to their own
   schema) without those costs.

Revised shape:

- **Database:** one `gemma_forge` database inside the existing
  Supabase Postgres.
- **Schemas:** one per skill (`stig` first, future skills add their
  own via `tools/bootstrap_skill.sh`).
- **Roles:** `forge_admin` owns the database (bootstrap + migration
  use only); `forge_<skill>` has `USAGE` on its schema and CRUD on
  current + future tables via `ALTER DEFAULT PRIVILEGES`. No
  cross-skill grants by default; `PUBLIC` revoked on every skill
  schema at creation.
- **Connection path:** the harness connects through Supabase's
  session-mode pooler at `127.0.0.1:5432` as `forge_<skill>`. The
  superuser credentials (from `/data/docker/supabase/.env`) are
  only read by the bootstrap scripts.
- **Version pinning, restart independence, backup separation:** all
  ride Supabase. The trade-offs were weighed and accepted; the
  logical-isolation gains from a dedicated instance did not justify
  duplicating a working service.

### Amendment 2: Neo4j path moves to `/data/neo4j/gemma-forge/`

The original decision placed Neo4j data at `/data/neo4j/` (i.e.,
claiming the service root). That works in the short term but makes
GemmaForge a bad host citizen: a second demo on this XR7620 that
also wants Neo4j has to share this instance or install elsewhere.

Revised path: `/data/neo4j/gemma-forge/`. Service-typed top-level,
project-scoped underneath — the same shape as `/data/vm/gemma-forge/`.
A future demo gets `/data/neo4j/<their-project>/` with its own port.

This does not alter [ADR-0012](0012-data-host-layout-convention.md)'s
convention; it clarifies that `/data/<service>/` is appropriate for
**shared host services** (e.g., `/data/triton/`), while
`/data/<service>/<project>/` is the right shape for **project-scoped
infrastructure** on a shared host. This distinction should be pulled
into a future ADR-0012 revision when the pattern is exercised by the
second project.

### Amendment 3: Neo4j per-skill isolation via `group_id`, not named databases (2026-04-16, Phase B)

The original ADR text and the first amendment described per-skill
isolation as **one Neo4j named database per skill**. Phase B exposed
a constraint we missed: **Neo4j Community Edition supports exactly
one user database (`neo4j`) plus the `system` database**. Multiple
named user databases are an Enterprise-only feature.

Switching to Enterprise is incompatible with the open-source posture
the project depends on. The right fit is Graphiti's native
`group_id` partitioning: every node and edge carries a `group_id`
attribute, every retrieval scopes to that group, and the `Community`
/ `Entity` / `Episodic` indexes Graphiti creates already include
`group_id` for fast filtering. One Neo4j instance, one underlying
database, logical per-skill partition.

Concretely:

- Episodes are added with `group_id="stig"`. A future skill uses
  `group_id="<skill>"` and never sees the stig data unless we
  explicitly cross-query.
- `Skill` marker nodes (one per skill) are written by
  `tools/graphiti_init.py` so the partition is visible from a Cypher
  shell without the application running.
- Cross-skill queries (e.g., dashboard "total runs across all
  skills") are still possible by omitting the `group_id` filter, and
  remain auditable by the same query pattern that filters them.

This is the same conclusion the relational side reached in
Amendment 1 (one database, per-skill schemas with scoped roles):
**logical per-skill isolation inside one shared instance**. The
two stores now align on the same isolation model.

### What these amendments do not change

- The dream pass, credit assignment, supersession, abstraction-loss
  recovery, environment tagging, and semantic linking — all still
  the distinctive contribution, unchanged.
- Graphiti-on-Neo4j as the Reflective substrate — unchanged.
- SQLite retirement — unchanged (still happens in Phase C migration).
- The three-tier memory model — unchanged.
- Sovereignty posture — arguably strengthened: one fewer redundant
  service, one more demonstration of client-of-existing-infrastructure
  discipline.

## References

- [Graphiti GitHub](https://github.com/getzep/graphiti) — Apache 2, Neo4j-backed bi-temporal graph memory.
- [Zep: A Temporal Knowledge Graph Architecture for Agent Memory (arxiv 2501.13956)](https://arxiv.org/abs/2501.13956).
- [A-MEM: Agentic Memory for LLM Agents (arxiv 2502.12110, NeurIPS 2025)](https://arxiv.org/abs/2502.12110).
- [A-MEM reference implementation](https://github.com/agiresearch/a-mem).
- [Learning to Forget: Sleep-Inspired Memory Consolidation for LLMs (arxiv 2603.14517)](https://arxiv.org/html/2603.14517v1).
- [Graph-based Agent Memory: Taxonomy, Techniques, Applications (arxiv 2602.05665)](https://arxiv.org/html/2602.05665v1).
- [Governing Evolving Memory: SSGM Framework (arxiv 2603.11768)](https://arxiv.org/html/2603.11768v1).
- [Memory in the Age of AI Agents: A Survey (paper list)](https://github.com/Shichun-Liu/Agent-Memory-Paper-List).
- [Bi-temporal KG for LLM Agent Memory: 92% LongMemEval](https://explore.n1n.ai/blog/building-bitemporal-knowledge-graph-llm-agent-memory-longmemeval-2026-04-11).
- [OpenClaw Dreaming Guide](https://dev.to/czmilo/openclaw-dreaming-guide-2026-background-memory-consolidation-for-ai-agents-585e).
- [AI Agent Memory in 2026: Auto Dream, Context Files (DEV)](https://dev.to/max_quimby/ai-agent-memory-in-2026-auto-dream-context-files-and-what-actually-works-39m8).
- [Graphiti: Should the Knowledge Flywheel Use It? (Codex Blog)](https://codex.danielvaughan.com/2026/03/30/graphiti-agent-memory-store/).
