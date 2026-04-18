---
id: journey-26-dreaming-and-real-databases
type: journey
title: "The Little Engine That Could Needs Real Databases (and a Nap)"
date: 2026-04-15
tags: [L4-orchestration, reflexion-loop, cross-run-learning, context-management, decision]
related:
  - journey/22-context-graphs-and-the-memory-question
  - journey/25-run-3-learning-plateaus
  - architecture/01-reflexive-agent-harness-failure-modes
one_line: "After three runs and a diminishing-returns plateau, an hour-long discussion turned into two decisions: adopt the 2026 memory-systems frontier (Graphiti-on-Neo4j, with a dream pass on top) and retire SQLite in favor of Postgres. The honest discovery — we are catching up to a frontier that crystallized in the last six months, not charting new ground — made the architecture sharper, not smaller."
---

# The Little Engine That Could Needs Real Databases (and a Nap)

Run 3 plateaued at 60% fix rate with a 1.4-to-1 win-to-regression ratio — barely better than noise. The regressions were scattered across categories, the signature of stale lessons misdirecting the Worker on problems it would otherwise handle cleanly. The memory system couldn't tell the difference between "this is always true" and "this was true last time."

An hour of coffee-and-contemplation ended with the clearest architectural pivot since the interface extraction: the memory system is getting real databases, the Reflective tier is adopting Graphiti (a 2026-era bi-temporal graph store that already solved the provenance problem), and the distinctive contribution — the thing that makes this project different from anyone else's agent-memory work — is the dream pass built on top, not the memory primitives underneath.

The uncomfortable part of the conversation was realizing the project was not as far ahead of the frontier as I'd hoped. The vocabulary of "dreaming" for memory consolidation, the bi-temporal graph pattern, the sleep-inspired consolidation papers — all had crystallized in the last six months, in public, with code. I was catching up. That admission made the architecture sharper, not smaller.

## The starting position

Three runs in, the picture looked like this:

- Run 1 → Run 2: 35% → 58%. The memory pipeline landed and the fix
  rate jumped 23 points.
- Run 2 → Run 3: 58% → 60%. Two points, at the cost of 26% more
  tokens and three more hours. Ten regressions against fourteen wins.

The regressions were scattered across categories, which is the
signature of stale lessons misdirecting the Worker on problems it
would otherwise handle cleanly. We had named this ahead of Run 3 in
the whitepaper — "the memory system cannot distinguish between 'this
is always true' and 'this was true last time'" — and Run 3 confirmed
it empirically.

We also had a clean example of the second failure mode. In Run 1 the
Worker solved `sudo_remove_nopasswd` by running `whoami` first and
preserving that specific entry. The Reflector distilled this into a
lesson: "preserve the agent's identity when modifying access
controls." Correct principle, missing procedure. In Run 2 the Worker
understood it needed to preserve "the agent" but had no way to know
which user that was, and failed nine times before we patched the
skill prompt. We named this one too: abstraction loss.

Two named failure modes, no architectural home for either.

## The contemplation

The coffee question was not "which optimization next." It was "what
is my goal here, actually." The three goals on the table:

1. Optimize Gemma 4 at the edge — largely done.
2. Build the best harness we can — highest leverage, most of the
   differentiation lives here.
3. Learn what actually works — continuous, captured in the journal.

Given (2) is where the leverage is, the real question was whether to
extend the harness or prove it generalizes. Both are defensible. The
conversation went to extension first because Run 3 had named the gap
loudly.

## The research pass, and the part I had to admit

I started with a half-remembered sense that "dreaming" was a useful
metaphor and that the project might be ahead of the frontier. A
focused search pass killed both assumptions.

- **Dreaming is a named industry concept now.** OpenClaw has an
  explicit "Dreaming" system with Light/REM/Deep sleep stages and
  promotion thresholds. Claude Code reportedly ships an unannounced
  "Auto Dream" feature. A DEV post is literally titled *"AI Agent
  Memory in 2026: Auto Dream, Context Files, and What Actually
  Works."* The vocabulary has standardized.
- **Academic backing is real.** "Learning to Forget: Sleep-Inspired
  Memory Consolidation for LLMs" (arxiv 2603.14517, March 2026).
  SleepGate. An openreview paper titled *"Language Models Need
  Sleep."* This is not a metaphor anymore.
- **Graphiti is the serious contender for the graph side.** Apache 2,
  production-grade on Neo4j, bi-temporal edges with `t_valid` and
  `t_invalid`, full provenance from every fact to its source episode,
  hybrid retrieval (semantic + BM25 + graph traversal) at 300ms P95.
  It answers *"Why does the agent believe X?"* natively. Paper:
  [arxiv 2501.13956](https://arxiv.org/abs/2501.13956).
- **A-MEM is NeurIPS 2025.** Zettelkasten-style memory evolution
  where new memories trigger updates to existing memories' attributes.
  Code released.
- **SSGM (arxiv 2603.11768, March 2026)** is already writing about
  *governing* evolving memory. We are in "risks of unbounded memory
  evolution" territory, not "should we have memory" territory.

The honest admission: gemma-forge is not charting new ground on the
memory primitives. The frontier crystallized in the last six months,
and we are catching up to it. That is a more honest story than the
alternative — and, crucially, a better one. Because the primitives
are not where the distinctive contribution is.

## What IS distinctive

What none of those projects has is our specific combination:

1. **Deterministic outcome signal.** Conversational memory systems
   have to infer whether a remembered fact was useful. We have a
   STIG scanner that returns a binary pass/fail per rule per attempt.
   That means we can do **outcome-driven credit assignment** on
   lessons in a way conversational memory literally cannot. Graphiti
   has bi-temporal validity; it does not have ground-truth outcomes
   to attribute lesson usefulness to.
2. **Skill-authored domain, AI-assisted.** A-MEM is a library.
   OpenClaw is a product. Neither treats domain knowledge as a
   first-class authored artifact separate from the memory system.
   Our skill is a contract — AI-assisted authoring, human-curated
   ownership, stable interface. The memory system serves the skill,
   not the other way around.
3. **Edge-local, no cloud dependency.** Graphiti runs on Neo4j. That
   runs on the XR7620 with no phone-home. The sovereignty posture is
   preserved because "no cloud" is not the same as "no infrastructure."

That triplet is the distinctive contribution. Not the graph, not the
bi-temporal edges, not the evolution pass — the *combination*,
applied to agentic operation on real infrastructure with a
deterministic evaluator.

## The database pivot

The conversation then turned to storage. The original plan for the
/dream skill was still rooted in SQLite — one file per skill, same
shape as today. I pushed on this: if we are making the pivot for
Reflective (graph), would we not want to do it for the relational
tiers too? In for a penny, in for a pound.

That was right, for reasons worth spelling out:

- Two skills are coming. Per-skill SQLite files scale badly — more
  files to back up, more migrations, more special cases. One Postgres
  instance with per-skill databases is a cleaner model.
- The dashboard is about to want cross-run queries. The Run Analyst
  chat interface will want SQL. Postgres is a better fit than
  wrestling with SQLite's concurrency model while the harness writes
  and the dashboard reads.
- Once one real database is already running on the box (Neo4j), the
  marginal cost of Postgres is small. Operational familiarity is
  already there via Supabase.
- The event logs (11,841 events in Run 3) want a queryable home
  anyway. JSONB in Postgres is the natural landing spot.

The "sovereign edge" principle is **no cloud, no phone-home, no
external dependencies** — it is not **no infrastructure**. The
XR7620 hosts Docker workloads with Supabase, Qdrant, ClickHouse,
MinIO, and Redis already. Adding Neo4j and a Postgres instance
scoped to gemma-forge is not a violation of sovereignty; it is the
sovereignty posture, accurately stated.

## The decision, in three parts

1. **Adopt Graphiti-on-Neo4j for the Reflective tier.** One Neo4j
   instance at `/data/neo4j/`, one named database per skill. Graphiti
   owns bi-temporal storage, provenance, and retrieval. We build the
   dream pass on top.
2. **Move to Postgres for Episodic, Semantic projection, and run
   history.** One instance at `/data/postgres/`, one database per
   skill. JSONL event logs ingested as JSONB rows.
3. **Retire SQLite.** Current per-skill DB files migrated via a
   one-shot tool in `tools/`. No dual-write period; the cutover is
   clean because Postgres is mature enough.

Full architectural rationale and alternatives considered are in
[ADR-0016](../../adr/0016-graphiti-neo4j-postgres-memory-stack.md).

## What the dream pass actually does

The dream pass runs between runs, against the last run's event log
and the Reflective graph:

1. **Ingest** the run as Graphiti episodes. Every Architect / Worker
   / Auditor / Reflector turn becomes an episode.
2. **Attribute outcomes.** Walk each lesson that was loaded into a
   Worker prompt; match against the final state of the rule it fired
   on. Update a `confidence` attribute (outcome-driven) that is
   *separate from* `weight` (frequency). This closes the gap where
   "lesson quality ≠ lesson frequency."
3. **Detect supersession.** When a Reflector note identifies a prior
   approach as wrong and proposes a replacement, write a
   `SUPERSEDED_BY` edge with validity intervals. Nothing is deleted.
4. **Recover abstraction loss.** When a lesson fires on an escalation
   with Reflector output matching "unclear procedure" / "missing step"
   patterns, fetch the originating attempt via `DERIVED_FROM`,
   re-hydrate the lesson with the concrete step, write a new version
   of the lesson, supersede the old. The `whoami` step would have
   been recovered this way.
5. **Tag environment dependency.** Lessons derived from attempts in
   VM baseline X get `LEARNED_IN X`. When a new run uses baseline Y,
   X-tagged lessons carry a confidence penalty until they fire
   successfully under Y.
6. **Link semantically.** A-MEM-style: new lessons get links to
   existing lessons in the same category. The Reflective graph
   becomes navigable.
7. **Write a dream report.** Markdown: N lessons re-weighted, M
   superseded, K abstraction-loss repairs, L environment-tagged.
   Surfaced in the dashboard Memory tab. Becomes raw material for
   future journey entries when something notable happens.

## What we are not doing

- **Not adopting Zep's commercial memory layer service.** Graphiti
  alone (the OSS core) is sufficient.
- **Not adopting AutoAgent-style meta-optimization.** Tempting, but
  it would eat the Ralph-plus-skill narrative. The skill is the
  primitive.
- **Not inventing parallel vocabulary.** "Dream pass," "bi-temporal,"
  "supersession," "provenance" — we use the terms the field is
  settling on.
- **Not reimplementing bi-temporal edge logic.** Graphiti already
  solved it. Weeks of primitive-level work that does not differentiate
  the project.

## Why this is a journey entry and not just an ADR

Two reasons. First, the database decision re-opens
[Journey 22](22-context-graphs-and-the-memory-question.md), which
landed on SQLite specifically because of the adaptive concurrency
clutch — a clutch that is *still* not wired in, three days later.
Entry 22 was right for its time, and it is important to record why
the decision is being replaced now. Scale and shape changed: three
runs of real data, two named failure modes, two skills coming, a
Run Analyst chat interface pending. The constraints are different.

Second, the honest-charting-new-ground moment belongs in the journey,
not the ADR. ADRs record decisions. Journey entries record how the
story actually moved — including the moments where you thought you
were ahead of the frontier and discovered you were catching up. That
discovery made the architecture sharper, not smaller.

## The plan from here

1. Stand up Postgres as a host service at `/data/postgres/`, with
   schemas for attempts, lessons, events.
2. Write the SQLite → Postgres migration tool. One-shot, kept in
   `tools/` for future skills.
3. Switch the harness reads and writes to Postgres.
4. Stand up Neo4j at `/data/neo4j/`. Install Graphiti.
5. Define the Reflective-tier schema (entities, edges, attributes).
6. Build the dream pass script.
7. Run 4 against the new stack.
8. Write the dream-pass-results entry when the data comes in.

Detailed task breakdown and estimated sequencing live in
`docs/drafts/memory-refactor-plan.md`.

## The larger point

Three runs in, the picture has clarified:

- The harness pattern works. 35% → 60% across three runs on the same
  hardware and model is real improvement.
- Cross-run memory is load-bearing. Most of the improvement came
  from the memory architecture, not from skill changes.
- Memory quality matters as much as memory quantity. The next gains
  are architectural, not additive.
- The industry figured out bi-temporal graph memory in the last six
  months. The right move is to adopt what works and build the
  distinctive part on top.

The distinctive part is outcome-driven credit assignment on agentic
infrastructure operations. That is where the dream pass lives. That
is where the next entry will be written from.

---

## Related

- [`journey/22`](22-context-graphs-and-the-memory-question.md) — the original SQLite decision, now being replaced.
- [`journey/25`](25-run-3-learning-plateaus.md) — the Run 3 data that made the pivot necessary.
- [`adr/0016`](../../adr/0016-graphiti-neo4j-postgres-memory-stack.md) — the architectural decision record.
- Research context: [Zep (arxiv 2501.13956)](https://arxiv.org/abs/2501.13956), [A-MEM (arxiv 2502.12110)](https://arxiv.org/abs/2502.12110), [Sleep-Inspired Consolidation (arxiv 2603.14517)](https://arxiv.org/html/2603.14517v1), [Graphiti](https://github.com/getzep/graphiti), [OpenClaw Dreaming](https://dev.to/czmilo/openclaw-dreaming-guide-2026-background-memory-consolidation-for-ai-agents-585e), [SSGM (arxiv 2603.11768)](https://arxiv.org/html/2603.11768v1).
