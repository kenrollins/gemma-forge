# Deferred work — debt and opportunities

A curated registry of things we know about but aren't addressing right now.
Keep small: aim for fewer than 20 active entries. Every entry has a
**pain signal** — the symptom that will tell us it's time to revisit —
so nothing here gets forgotten until it shows up as an error someone
has to explain in a pull request post-mortem.

Two categories:

- **Debt** — known-broken or known-incomplete. Something is wrong today.
- **Opportunities** — identified improvements we've sequenced later. Nothing
  is wrong, but we've explicitly decided not to do it yet.

Separate from both: **skill stubs**, which are scaffolded-but-inactive skill
directories. Not debt — they're placeholders waiting for their first real use.

Review cadence: glance at this file at the start of every run-planning
conversation. Add an entry at the end of any work session where we agreed
to defer something. If a pain signal fires, the relevant entry gets
promoted out of this file into active work.

---

## Debt — known-broken or known-incomplete

### DEF-01 — Clutch adaptive concurrency is dead code after init

- **What**: [`gemma_forge/harness/clutch.py`](../gemma_forge/harness/clutch.py)
  implements `Clutch.recommend_workers()` and `Clutch.select_batch()`. Both are
  tested ([`tests/test_memory_and_clutch.py`](../tests/test_memory_and_clutch.py))
  and smoke-tested ([`tools/smoke_memory_e2e.py`](../tools/smoke_memory_e2e.py)).
  In [`ralph.py`](../gemma_forge/harness/ralph.py) the clutch is initialized,
  snapshotted once, and never consulted again. Every run is fully serial
  despite a working adaptive-concurrency controller sitting there.
- **Why deferred**: The UI has no way to represent N concurrent in-flight
  rules. Shipping clutch without a UI story hides the architectural win and
  breaks the "watch the edge AI work" demo narrative that's core to the
  project's purpose.
- **Revisit when**: The UI can represent N concurrent in-flight rules.
  The preferred pattern (captured below) is an **active-queue band**: a
  single "now processing" region that expands from 1 card (serial) into a
  row of N cards (parallel) as clutch recommendations change, with a
  clutch meter above the band showing worker count + category + prior
  success rate. The UI widens and narrows with difficulty — adaptive
  concurrency becomes the visible subject rather than a hidden optimization.
- **Design notes needed before wiring**:
  - Architect turn semantics: does Architect pick N rules per turn, or pick
    one-at-a-time as workers free up?
  - Cross-worker reflection: do same-run tips land with a lag for in-flight
    workers, or do reflections from finishing workers re-inject?
  - Reengagement: per-rule trigger (today's shape) or composite view of
    all in-flight rules?
- **Pain signal**: Every run's JSONL has a `clutch_initialized` event with
  a `recommended_workers` value above 1 that never takes effect. If
  throughput plateaus and we can't explain why, DEF-01 is the answer.
- **Context**: [journey/22](journal/journey/22-context-graphs-and-the-memory-question.md)
  (the clutch is what motivated SQLite/Postgres), this conversation.

### DEF-02 — Architect ignores prompt-level ordering guidance

- **What**: The Architect's STIG prompt contains the literal instruction
  `"IMPORTANT: Process audit_rules_immutable LAST within audit rules."`
  The Architect read it and ignored it in every run since it was added.
- **Why deferred**: We fixed the STIG-specific instance via the
  skill-declared ordering-constraint mechanism (Run 6 work). The general
  *pattern* — "prompt guidance is not enforcement" — still persists wherever
  we rely on prompt text for behavioral constraints the Architect might
  skip.
- **Revisit when**: Any future skill needs rule ordering, sequencing, or
  preconditions that aren't already covered by the `ordering_constraints`
  manifest schema. Candidates we'll see: CVE-response ("apply kernel
  patches last, they require reboot"), network-reconfig ("test in staging
  before production interface"), crypto-recovery ("rotate keys before
  updating configs that reference them").
- **Pain signal**: A skill ships with ordering requirements in its Architect
  prompt only, then runs surface cascading failures that mirror the
  audit-immutable pattern — a "capstone" rule gets picked early and
  invalidates later work.
- **Context**: [journey/25](journal/journey/25-run-3-learning-plateaus.md) (first
  flagging), this conversation (the mechanism that closes the first instance).

### DEF-03 — Dream pass uses category-level credit assignment

- **What**: [`gemma_forge/dream/pass_.py`](../gemma_forge/dream/pass_.py)
  computes fix rate per category and applies `signal × 0.3` as a confidence
  nudge to *every* lesson in that category. A bad lesson in a high-success
  category gets boosted by its neighbors; a good lesson in a struggling
  category gets dinged unfairly.
- **Why deferred**: V2's `tip_retrievals` table already records per-tip
  outcome evidence. The right fix is to migrate dream-pass credit assignment
  to per-tip (same shape as eviction). That's a structural rewrite that
  pairs naturally with DEF-04 (merging dream pass + eviction into one
  consolidation step).
- **Revisit when**: DEF-04 happens, or when we see per-tip utility data
  that contradicts the confidence score on the same lesson (dreams saying
  "high confidence" while retrievals say "never helps").
- **Pain signal**: A lesson carries high `confidence` from the dream pass
  but its `tip_retrievals` rows show consistently zero `outcome_value`.
  Cross-check query: lessons where `confidence > 0.5 AND avg_utility < 0.1`.
- **Context**: [journey/28](journal/journey/28-run-4-and-the-coarseness-problem.md),
  this conversation.

---

## Opportunities — identified but sequenced later

### DEF-04 — Merge dream pass and eviction into one consolidation step

- **What**: Dream pass (V1) adds confidence to lessons. Eviction (V2)
  retires low-utility tips. Xu et al. (arxiv 2505.16067) treats them as a
  single post-run consolidation. They operate on different tables today
  (`stig.lessons_current` vs `stig.tips`) for historical reasons — lessons
  predate tips.
- **Why deferred**: The two systems work independently right now. Merging
  them requires also deciding whether lessons survive as a concept or
  collapse into tips. That's a V3 architecture call, not a patch.
- **Revisit when**: DEF-03 becomes painful enough that fixing it alone
  feels wasteful, or when we hit the "which table is source of truth?"
  confusion more than once in retrieval code.
- **Pain signal**: A change to confidence/utility policy requires touching
  both `dream/pass_.py` and `memory/eviction.py` with parallel edits — a
  DRY violation indicating the concepts want to be one thing.

### DEF-05 — Neo4j Tip/Rule node mirror (V2 plan G4)

- **What**: V2 plan proposed mirroring `stig.tips` into Neo4j as `Tip` nodes
  with `HELPED` edges to `Rule` nodes, enabling graph-shaped queries.
  Deferred at [`v2-architecture-plan.md:939`](drafts/v2-architecture-plan.md#L939).
- **Why deferred**: Postgres `tip_retrievals` is sufficient for hit-rate
  computation and the eviction policy. Neo4j adds nothing until we want
  graph-shaped queries we can't express in SQL.
- **Revisit when**: We want "tips that help rules similar to X via shared
  failure patterns" or other multi-hop queries that are awkward in SQL.
- **Pain signal**: A retrieval or analytics query is expressed in SQL via
  three CTEs and a self-join, and would be a single Cypher MATCH.

### DEF-06 — A-MEM semantic tip linking

- **What**: Cross-link semantically similar tips so retrieval can expand
  outward from a seed tip rather than only querying by rule prefix. From
  V2 plan at [`v2-architecture-plan.md:462`](drafts/v2-architecture-plan.md#L462).
- **Why deferred**: V2 retrieval uses lexical-prefix similarity + category
  + hit-rate. That's enough to test the thesis. Embedding-based links add
  complexity and a cold-start problem.
- **Revisit when**: We see rules that share a failure mechanism but not a
  name prefix being served unhelpful tips (V2 misses because prefix
  similarity is low even though mechanism similarity is high).
- **Pain signal**: A known-good tip for rule `foo_X` never gets retrieved
  for rule `bar_Y` despite them failing for the same reason — tip
  similarity score sits near zero because rule prefixes differ.

### DEF-07 — AgeMem-style RL memory policy

- **What**: Train a memory retention/retrieval policy via RL, as proposed
  in the AgeMem paper. Explicitly V3 in
  [`v2-architecture-plan.md:692`](drafts/v2-architecture-plan.md#L692).
- **Why deferred**: V2's hand-designed composite score (base + category
  bonus + hit-rate × 0.5 + source prior) is still uncalibrated. Learning
  a policy on top of uncalibrated features is premature.
- **Revisit when**: The V2 composite's coefficients are empirically
  defensible across multiple runs and we have a labeled dataset of
  helpful-vs-unhelpful retrievals.
- **Pain signal**: We tune composite coefficients twice without a
  principled basis and keep finding that different runs want different
  weights.

---

## Skill stubs — scaffolded, awaiting first real use

These are placeholder skill directories with `skill.yaml` pointing at
STIG's tool names (`run_stig_scan`, `apply_fix`, `check_health`) with
`TODO: replace` comments. They're not broken — the harness ignores them
because they're not selected. They're here so that when we activate one,
we know it needs tool implementations, not just a schema swap.

- [`skills/log-triage/`](../skills/log-triage/) — needs `pull_logs`, `search_logs`, `run_query`, `validate_finding`
- [`skills/network-reconfig/`](../skills/network-reconfig/) — needs `diagnose_network` + siblings
- [`skills/crypto-recovery/`](../skills/crypto-recovery/) — needs `diagnose_crypto` + siblings
- [`skills/cve-response/`](../skills/cve-response/) — needs `scan_cves` + siblings
- [`skills/service-recovery/`](../skills/service-recovery/) — needs `diagnose_services` + siblings
- [`skills/rotate-ssh-keys/`](../skills/rotate-ssh-keys/) — scaffolded, full tool list TBD

Activating any of them is a phase of work in its own right; pick the
one that advances the "skill-agnostic harness" narrative most (CVE
response is the current favorite because reboot-required cascades test
DEF-02 in a fresh context).
