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

- **What**: [`gemma_forge/harness/clutch.py`](https://github.com/kenrollins/gemma-forge/blob/main/gemma_forge/harness/clutch.py)
  implements `Clutch.recommend_workers()` and `Clutch.select_batch()`. Both are
  tested ([`tests/test_memory_and_clutch.py`](https://github.com/kenrollins/gemma-forge/blob/main/tests/test_memory_and_clutch.py))
  and smoke-tested ([`tools/smoke_memory_e2e.py`](https://github.com/kenrollins/gemma-forge/blob/main/tools/smoke_memory_e2e.py)).
  In [`ralph.py`](https://github.com/kenrollins/gemma-forge/blob/main/gemma_forge/harness/ralph.py) the clutch is initialized,
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

- **Status**: STIG instance **closed 2026-04-18** (commit `16e7b43`) —
  `audit_rules_immutable` now filtered out of the candidate pool by the
  skill-declared `ordering_constraints` mechanism. General pattern
  remains open for future skills.
- **What**: The Architect's STIG prompt contains the literal instruction
  `"IMPORTANT: Process audit_rules_immutable LAST within audit rules."`
  The Architect read it and ignored it in every run since it was added.
- **Why deferred**: The general *pattern* — "prompt guidance is not
  enforcement" — still persists wherever we rely on prompt text for
  behavioral constraints the Architect might skip. Next instance will
  need its own skill-manifest declaration or a richer predicate.
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

- **What**: [`gemma_forge/dream/pass_.py`](https://github.com/kenrollins/gemma-forge/blob/main/gemma_forge/dream/pass_.py)
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
  Deferred at [`v2-architecture-plan.md:939`](https://github.com/kenrollins/gemma-forge/blob/main/docs/drafts/v2-architecture-plan.md#L939).
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
  V2 plan at [`v2-architecture-plan.md:462`](https://github.com/kenrollins/gemma-forge/blob/main/docs/drafts/v2-architecture-plan.md#L462).
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
  [`v2-architecture-plan.md:692`](https://github.com/kenrollins/gemma-forge/blob/main/docs/drafts/v2-architecture-plan.md#L692).
- **Why deferred**: V2's hand-designed composite score (base + category
  bonus + hit-rate × 0.5 + source prior) is still uncalibrated. Learning
  a policy on top of uncalibrated features is premature.
- **Revisit when**: The V2 composite's coefficients are empirically
  defensible across multiple runs and we have a labeled dataset of
  helpful-vs-unhelpful retrievals.
- **Pain signal**: We tune composite coefficients twice without a
  principled basis and keep finding that different runs want different
  weights.

### DEF-09 — Run Analyst chat interface in the dashboard

- **What**: A conversational UI on the dashboard where a user can ask
  questions about the current run (while it's in-flight) or about a
  past run being reviewed — "why did `audit_rules_immutable` fail
  again?", "which lessons fired on `dac_modification_*`?", "what did
  the Reflector say about the partition rule?" Postgres (via ADR-0016)
  is the intended substrate because the questions are inherently
  SQL-shaped.
- **Why deferred**: Flagged explicitly in the memory-architecture pivot
  as "pending," but the dashboard's higher-priority work (task graph,
  telemetry tiles, dream-report rendering) took precedence. The
  Postgres substrate it depends on is now live; the UI + LLM-to-SQL
  glue is the remaining work.
- **Revisit when**: A reviewer wants to interrogate a run and ends up
  grepping JSONL by hand, or when a live demo of the dashboard lacks
  the "ask it anything" moment that makes the telemetry legible to a
  non-author.
- **Pain signal**: We write one-off SQL against Postgres to answer
  ad-hoc "what happened in run X" questions that a demo viewer would
  plausibly ask. If the same query shape gets written twice by hand,
  DEF-09 should ship.
- **Context**: [journey/26](journal/journey/26-dreaming-and-real-databases.md#L138)
  ("the Run Analyst chat interface will want SQL"),
  [journey/26](journal/journey/26-dreaming-and-real-databases.md#L227)
  ("a Run Analyst chat interface pending. The constraints are
  different").

### DEF-10 — Render the dream-pass report in the dashboard Memory tab

- **What**: The dream pass writes a markdown report each run ("N
  lessons re-weighted, M superseded, K abstraction-loss repairs, L
  environment-tagged"). Today that report exists on disk; the
  dashboard's Memory tab doesn't render it. Anyone who wants to see
  what the dream pass did must open the file manually.
- **Why deferred**: V1 of the dream pass was scoped to produce the
  report; surfacing it in the UI was deferred to focus on the
  retrieval-path improvements that directly affect fix rate.
- **Revisit when**: We show the system to someone and the dream pass
  is invisible unless narrated. A dashboard that hides its best
  differentiator weakens the demo.
- **Pain signal**: The phrase "let me pull up the dream report" in a
  live walkthrough, followed by a terminal window. If we've ever done
  that in front of someone, the feature earned its slot.
- **Context**: [journey/27](journal/journey/27-building-the-dream-pass.md#L122)
  ("the dream report is currently markdown; rendering it in the
  dashboard is the next UI work").

### DEF-11 — Embedding-based plateau and lesson-similarity detection

- **What**: Two places use keyword-set overlap where embeddings would
  be tighter: the plateau detector in the Reflector
  ([architecture/01](journal/architecture/01-reflexive-agent-harness-failure-modes.md#L248))
  and the lesson-similarity check in the V3 fix pass
  ([journey/17](journal/journey/17-v3-fix-pass.md#L135)). A small local
  model (`sentence-transformers/all-MiniLM-L6-v2`, ~6 MB) would close
  the stem-collapse gap (`config` vs `configuration`) that keyword-sets
  miss.
- **Why deferred**: The keyword-set plateau detector performs at 76%
  accuracy — adequate for shipping. Embeddings add a dependency, a
  model download, and a cold-start path without a demonstrated failure
  in the simpler version.
- **Revisit when**: A plateau the detector misses because two
  semantically identical Reflector messages differ in surface form
  (`"config file"` vs `"configuration file"`), or a lesson-similarity
  check that fails for the same reason.
- **Pain signal**: A manual review says "this was obviously the same
  rule repeating" and the plateau detector disagreed because the
  keyword sets didn't overlap. Two instances is the threshold.
- **Note**: Distinct from DEF-06 (A-MEM semantic linking via
  tip-to-tip graph edges). DEF-11 is retrieval-time similarity on
  free text; DEF-06 is structural graph links between memory objects.
- **Context**: [architecture/01](journal/architecture/01-reflexive-agent-harness-failure-modes.md#L248),
  [journey/17](journal/journey/17-v3-fix-pass.md#L135).

### DEF-12 — `SKIP_UNTIL_DEPS` Architect re-engagement verdict

- **What**: The Architect's re-engagement vocabulary today is
  CONTINUE / PIVOT / ESCALATE. Several patterns want a fourth verdict:
  "this rule can be fixed, but *not yet* — other rules have to
  remediate first." `SKIP_UNTIL_DEPS` would let the Architect encode
  that without abusing ESCALATE (which implies "I'm stuck") or
  forcing premature attempts.
- **Why deferred**: The skill-declared ordering-constraint mechanism
  (Run 6 work, which closed DEF-02's first instance) handles the
  *known* cases. The fourth verdict is for *dynamically* discovered
  dependencies the skill manifest didn't anticipate. Until we see
  those patterns clearly in future skills, we can't name the verdict
  properly.
- **Revisit when**: CVE-response, network-reconfig, or crypto-recovery
  surface dynamic dependency patterns the static manifest can't
  capture — e.g., "this kernel patch needs a reboot, but a running
  job can't be interrupted; skip until the job finishes."
- **Pain signal**: Re-engagement logs show ESCALATE being used for
  "I'm not stuck, I'm just waiting on something" and the Reflector
  note makes the distinction explicit.
- **Context**: [journey/17](journal/journey/17-v3-fix-pass.md#L361)
  ("There's a case for adding a fourth verdict like `SKIP_UNTIL_DEPS`
  ... Deferred until the dependency patterns are clear enough to name
  properly").

### DEF-13 — V2 dream-pass features requiring LLM calls

- **What**: Three enhancements scoped for V2 of the dream pass, all
  requiring an LLM call to implement:
  1. **Supersession detection** — parse Reflector text to find "this
     prior approach is wrong; the replacement is X" and write a
     `SUPERSEDED_BY` edge with validity intervals (Graphiti's
     bi-temporal model supports this natively).
  2. **Abstraction-loss recovery** — when a lesson fires on an
     escalation and the Reflector says "unclear procedure" or
     "missing step," walk the `DERIVED_FROM` edge to the originating
     attempt and re-hydrate the lesson with the concrete step that
     was lost during summarization.
  3. **Semantic linking** — A-MEM-style cross-links between lessons
     in the same category so retrieval can expand outward from a
     seed lesson rather than only querying by rule prefix.
- **Why deferred**: All three need an LLM call per operation, which
  makes them expensive relative to V1's pure-SQL rewrite logic. V1
  shipped to test the loop; V2 adds semantic enrichment once per-rule
  credit (DEF-03 + DEF-08) is proven worth the spend.
- **Revisit when**: Per-rule credit assignment is working and we're
  looking for the next meaningful boost. Item 3 overlaps DEF-06 — if
  DEF-06 ships first, scope this entry down to items 1 and 2.
- **Pain signal**: The same lesson gets re-derived from scratch across
  runs because no `SUPERSEDED_BY` edge was written; OR a lesson that
  fires successfully on one rule never gets retrieved for a
  structurally identical rule with a different name.
- **Context**: [journey/27](journal/journey/27-building-the-dream-pass.md#L121)
  ("V2 dream pass: supersession detection ..., abstraction-loss
  recovery ..., A-MEM-style semantic linking. All deferred to V2
  because they require LLM calls.").

### DEF-15 — Dream-pass silent fallback picks wrong run on mismatched run_id

- **What**: When `run_dream_pass` is called with a `run_id` that has no
  `work_items` rows (e.g., passing the JSONL filename `20260417-154947`
  instead of the Postgres UUID `5444a199-cbc`), it silently falls back
  to "most recent run with outcomes" and dreams against that run's
  data. The caller sees the dream complete with a different run_id
  buried in the INFO log.
- **Why deferred**: The harness auto-consolidation path always passes
  the correct Postgres UUID (`mem_run_id`), so in production the
  fallback never fires. The footgun is limited to manual CLI use by
  someone who confuses the two id formats. Fixing it is a one-line
  change (raise instead of fall back) plus a clearer error message,
  but it's below the Run 6 cutline.
- **Revisit when**: Someone runs the CLI manually with the wrong id
  format again, or we add a second entry point that might route a
  JSONL id to the dream pass.
- **Pain signal**: `runs/dreams/dream-<run_id>.md` filename doesn't
  match the `run_id` the caller passed in.
- **Context**: Observed 2026-04-18 during Run-6 prep manual
  consolidation sweep. Fix: in `run_dream_pass`, if
  `compute_category_credits(run_id)` returns empty, raise
  `ValueError("run_id has no work_items; did you pass a JSONL id
  instead of a Postgres UUID?")` rather than scanning for a recent
  alternative.

### DEF-17 — Scanner-gap threshold is likely triggering too eagerly

- **What**: The `scanner_gap_detected` event fires when N distinct
  approaches hit `evaluator_gap` without the target being unhealthy.
  Current threshold is 3 distinct approaches. Run 6 logged **391
  scanner_gap events vs Run 5's 205** — a 91% increase while the
  run length only grew 33%. Given that scanner gaps trigger Architect
  reengagement (PIVOT jumped 120→327 in Run 6, a 173% increase), the
  threshold may be causing too-frequent pivots on rules that would
  have resolved in one or two more attempts.
- **Why deferred**: Tuning a harness-wide threshold against one
  skill's data is the same trap Track A runtime work was flagged
  for. Waiting on CVE run data to see whether the same pattern
  holds. If CVE also shows scanner-gap inflation, the threshold
  is genuinely too tight; if it shows differently, the tuning
  should be per-skill or per-category.
- **Revisit when**: First full CVE run provides a second data
  distribution, OR PIVOT reengagement rate continues to grow
  disproportionately to run length.
- **Pain signal**: Run N's `architect_reengaged` PIVOT count is
  more than 2× Run N-1's while wall time and rule count grew less
  than 1.5×. This indicates the loop is pivoting on rules that
  would have converged on their own.
- **Context**: Run 6 post-mortem (journey/34). Specific numbers:
  Run 5 (14.3h, 205 scanner_gaps, 120 PIVOTs) vs Run 6 (19.1h, 391
  scanner_gaps, 327 PIVOTs). The scanner_gap_threshold config key
  defaults to 3; candidates for tuning are 4 or 5.

### DEF-18 — Skill-dir aliases should live in SkillManifest, not hardcoded

- **What**: Three places in the codebase hardcode the mapping
  between skill schema names (short: `stig`, `cve`) and skill
  directory names (long: `stig-rhel9`, `cve-response`):
  `ralph.py`'s `_run_auto_consolidation` skill_dir_map, `eviction.py`'s
  `_skill_to_schema`, and `tools/evict_tips.py`'s
  `_skill_evaluator_metadata`. Each one grew its CVE entry
  independently today. A future skill (`cve-ubuntu`, `stig-rhel10`,
  etc.) would require three more edits.
- **Why deferred**: Cleanup refactor; not blocking any functionality.
  The `SkillManifest` pydantic model is the right place to declare
  both names — maybe a `schema_name` field that defaults to the
  first segment of the directory name. Registering the alias in one
  place and reading it from the three call sites is the target.
- **Revisit when**: A third skill lands (and requires three more
  hardcoded edits), or when any of the three mapping sites develops
  a real bug because they drifted apart.
- **Pain signal**: Someone adds a skill and the harness crashes
  with a confusing error message that points to the wrong directory.
  Or: grep finds four sites with the same mapping logic.
- **Context**: Noted in the commit message for the CVE eviction
  fix (commit `152842a`, 2026-04-19). Three hardcoded sites
  identified during the smoke-test-catches-bug debug today.

### DEF-19 — dac_modification fchown / fchownat regressed R5→R6

- **What**: Two rules in the `audit_rules_dac_modification_*` family
  regressed from first-try wins in Run 5 to escalations at attempt 5
  in Run 6. `audit_rules_dac_modification_fchown` and
  `audit_rules_dac_modification_fchownat`. The ordering constraint
  isn't at fault — `audit_rules_immutable` didn't run until t=19h
  in Run 6, so the kernel wasn't locked when these ran. Something
  about the prompt state or retrieved tips made these harder, not
  easier, between runs.
- **Why deferred**: Two rules out of 247, running against a
  functional ordering-constraint fix that helped 7 other
  dac_modification family members. The regression is small
  relative to the net gain, and diagnosing it requires comparing
  the V2 tip retrievals between R5 and R6 for those specific rules
  — non-trivial analysis that's worth doing but not Run-7 blocking.
- **Revisit when**: The same pair regresses in Run 7, or a
  similar pattern (R5-first-try → R6-escalate-mid-grind) emerges
  on another category.
- **Pain signal**: Cross-run first-try-loss analysis shows more
  than 5 rules per category moving backwards between consecutive
  runs. Single-rule regressions are noise; pattern-level regressions
  are signal.
- **Context**: Run 6 post-mortem (journey/34), cross-run analysis
  section. Retrievals logged in Run 6's JSONL; comparison against
  Run 5's prompt_assembled events would reveal what specific tips
  were surfaced to those two rules.

### DEF-16 — Cross-scale harness validation (larger-model follow-on project)

- **What**: This project is rooted in Gemma 4 31B. The harness interfaces
  are provider-agnostic — swapping to a 400B+ model via build.nvidia.com
  API or to larger local hardware (e.g., 2× A6000 on a separate host)
  is a config change, not a refactor. That makes a natural follow-on
  project: same harness + same memory architecture + same skills, run
  against progressively larger models. Measure which improvements are
  harness-contributed vs model-contributed. Explore whether memory's
  hit-rate signal still improves outcomes at frontier scale, or whether
  it was papering over 31B's context limits.
- **Why deferred**: This is a **separate project**, not a scope
  expansion. GemmaForge's distinctive positioning is "small edge model +
  harness at sovereign/airgap deployments." Mixing in larger-model
  experiments inside this project muddies that story. The follow-on
  project would start from this project's published baseline and
  explicitly ask different questions (cross-scale behavior, model
  scaling laws under fixed architecture, multi-tier routing patterns).
  Hardware for the follow-on exists (2× A6000 host + NVIDIA API
  access) but starting it now would derail the Gemma 4 thesis.
- **Revisit when**: GemmaForge publishes its whitepaper + the CVE
  skill is stable + the cross-run data corpus is large enough to be
  a defensible baseline. Then a new project charter: "Scale the
  harness — how does GemmaForge's architecture behave at 70B, 200B,
  400B+ model sizes?"
- **Pain signal**: Federal reviewers / practitioners asking "does
  this only work because 31B is the smallest viable model, or does
  the architecture generalize?" The cross-scale study is the answer
  that question needs, and it's better answered by a dedicated
  follow-on than by expanding GemmaForge's scope.
- **Context**: Discussed 2026-04-19 during the CVE pivot. Access to
  larger-model infrastructure surfaced; decision was to capture the
  opportunity as a future project rather than absorb into this one.

### DEF-20 — Different-model adversarial critic for Reflector output

- **What**: Run a second model (different family — Qwen, Llama, Mistral)
  as a one-pass adversarial critic of the Reflector's tip emissions.
  The critic reads the Reflector's proposed tip + mechanism and either
  confirms it or flags it as low-signal / wrong-direction /
  already-failed-before. One extra LLM call per reflection, not a
  full multi-round debate — the parse-time mechanism-field filter
  plus outcome-driven eviction already do most of the adversarial
  work; the critic catches the ~10% where a plausible mechanism is
  actually wrong.
- **Why deferred**: Requires different-model infrastructure we don't
  have on the XR7620. Gemma-vs-Gemma debate mode-collapses — both
  agents share priors, training data, and blind spots; literature
  (Du et al. ICML 2024) shows same-model debate provides a fraction
  of the lift different-model debate does. We'd spend 2× Reflector
  inference for marginal gain. When DEF-16's cross-scale hardware
  (2× A6000 host or build.nvidia.com API access) lands, routing
  Reflector output through a genuinely different model is the
  experiment worth running.
- **Revisit when**: DEF-16 hardware is active, AND we've exhausted
  the cheaper adversarial mechanisms (mechanism-field filter,
  outcome-driven eviction, ATLANTIS two-level enforcement) and
  still see Reflector tips with plausible-but-wrong mechanisms
  surviving into the prompt.
- **Pain signal**: Post-run tip audit finds tips with reasonable-
  looking mechanisms that never help (repeated zero-utility retrievals
  despite the mechanism field passing the parser). When the parser-
  level filter isn't enough because the mechanism is plausible-but-
  false, only a different model can catch it.
- **Context**: Discussed 2026-04-19. Same-Gemma debate would slow
  runs 2-3× with marginal quality gain; different-model debate has
  real literature-backed lift. Relevant prior work: Du et al.,
  "Improving Factuality and Reasoning in Language Models through
  Multiagent Debate" (arXiv 2305.14325, ICML 2024); Liang et al.,
  "Encouraging Divergent Thinking in LLMs through Multi-Agent
  Debate" (arXiv 2305.19118).

### DEF-21 — Bisection within a failed reboot-verify family

- **What**: The per-package-family reboot-verify pass (journey/36)
  groups pending advisories by family (kernel, glibc, openssl,
  etc.), applies + reboots + verifies each family as a unit. On
  family failure, every advisory in the family is marked failed
  with the family-level reason (`family_reboot_failed`,
  `family_health_failed`). No attempt is made to isolate which
  specific advisory within the failed family caused the problem.
  Bisection would: split the failed family into halves, reboot
  each half separately, binary-search to the specific bad
  advisory. `log₂(N)` extra reboots in the worst case.
- **Why deferred**: In practice, kernel/glibc/systemd batches
  mostly fail for cross-cutting reasons (hardware regression,
  cross-package interaction) rather than one individually bad
  advisory. Family-level attribution is usually the right
  granularity for operator triage. Bisection doubles the
  engineering complexity of the reboot-verify pass and pays off
  only when single-advisory isolation matters more than the
  extra reboot cost. We need to see families fail often enough
  that bisection's value is obvious before we build it.
- **Revisit when**: Family-failure logs show ≥3 runs where a
  single family failed and operator review concluded only one
  advisory in the bundle was the cause. That's the signal that
  bisection would have isolated the bad apple and saved the
  rest. Until then, "whole family escalated" is fine.
- **Pain signal**: Operator reviews a family failure, manually
  tests each advisory in isolation, finds only one was the
  culprit, and re-runs with the bad advisory removed. If this
  happens more than a couple times across skills with reboot-
  required advisories, bisection-on-failure earns its keep.
- **Context**: [journey/36](journal/journey/36-per-family-reboot-batching.md)
  (the design decision that deferred this). Implementation layer
  would be inside `CveSkillRuntime.resolve_deferred` — the
  snapshot infrastructure is already there for per-family
  rollback, bisection is a retry loop with halved batches on top
  of the existing snapshot/apply/reboot/verify primitives.

### DEF-14 — Harness as training-data factory (fine-tuning pipeline)

- **What**: Every run produces structured (context, action, outcome)
  tuples with deterministic outcome labels — this is, by construction,
  a fine-tuning corpus. A full design sits in
  [`futures/harness-as-training-data-factory.md`](journal/futures/harness-as-training-data-factory.md),
  covering both skill-specific fine-tuning (teach a model STIG) and
  architecture-pattern fine-tuning (teach a model to act in the
  Architect / Worker / Auditor shape).
- **Why deferred**: V2 schema (per-tip outcomes, per-rule credit,
  prompt-time lesson IDs) isn't stable yet. Fine-tuning on a moving
  schema produces a model that fights the next schema revision.
  Corpus size is also marginal — a handful of runs isn't enough data
  to move a 4B/30B model meaningfully.
- **Revisit when**: V2 schema holds steady across three consecutive
  runs, AND we have at least ~20 runs' worth of (context, action,
  outcome) data in Postgres.
- **Pain signal**: We keep hand-crafting prompts to compensate for the
  base model's blind spots in a specific skill domain, and the prompt
  library grows faster than the skill manifest. At that point, the
  fine-tune is cheaper than continuing to patch prompts.
- **Context**: [futures/harness-as-training-data-factory.md](journal/futures/harness-as-training-data-factory.md)
  (full design with phases F1–F4).

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
