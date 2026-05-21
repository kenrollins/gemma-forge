# ADR-0019: Context-graph outcome attribution — graded outcome + per-retrieval causal signal

- **Status:** Accepted
- **Date:** 2026-05-21
- **Deciders:** Ken Rollins
- **Related:** [ADR-0016](0016-graphiti-neo4j-postgres-memory-stack.md), [ADR-0018](0018-vllm-mtp-cutover.md), [architecture/02](../journal/architecture/02-context-graphs-decision-provenance.md)

## Context

V2 memory (ADR-0016) records, for every tip retrieved into a Worker prompt, the outcome that followed:

```sql
tip_retrievals(id, run_id, attempt_id, tip_id, rule_id, rank,
               similarity_score, outcome_value, outcome_confidence,
               retrieved_at)
```

The dream pass and the retrieval ranker consume this to compute per-tip
hit rate as `avg(outcome_value × outcome_confidence)`. The credit-assignment
rule is:

> tip retrieved + attempt succeeded → tip earns positive utility

This rule assumes any retrieved tip either contributed to the success
or was ignored irrelevantly. The assumption fails when the corpus
contains *plausible-but-wrong* tips that the Worker might follow on
some future run even though it ignored them this run. Their
"helpfulness" is a credit-assignment artifact, not signal.

Run 7's cryptography category collapse (journey/38.5) is the empirical
witness. Five tips retrieved for `harden_sshd_macs_openssh_conf_crypto_policy`
in both Runs 6 and 7 — identical tips, identical rank order — all
advised "use `update-crypto-policies`, don't manually edit ssh_config."
Run 6's Worker ignored them (used `cat <<EOF > ssh_config`, which
worked by overwriting the conflicting `Include` directive). All five
retrievals got `outcome_value = 1` because the rule passed. Run 7's
Worker (post-MTP, more in-context-obedient) followed the literal advice;
the rule failed three times in a row.

Two structural problems are visible in that case:

1. **outcome_value is binary** despite the field being `DOUBLE PRECISION`.
   STIG's evaluator uses the default `outcome_signal_from_eval_result`
   helper, which projects `passed → 1.0` / `not passed → 0.0`. Every
   pass looks identical (whether at attempt 1 or attempt 12); every
   failure looks identical (whether broken-the-system or just-not-yet);
   the "harmful" range of the float field has been zero-rows across
   the entire 8,810-row history.
2. **No causal attribution**. The retrieval row records what was
   retrieved and what outcome followed. It doesn't record whether
   the Worker's action reflected the retrieved tip. Lucky-neighbor
   tips look identical to causally-contributing tips in the credit
   signal.

[Architecture/02](../journal/architecture/02-context-graphs-decision-provenance.md)
states the general pattern: a retrieval-augmented agentic system that
scores retrievals on outcome alone accumulates plausible-but-wrong
items that survive by correlation rather than contribution. The fix
is to add per-retrieval causal attribution as a first-class column,
which converts the schema from a knowledge graph (facts) into a
context graph (causation). This ADR records the engineering decision
for the STIG-first implementation.

## Decision

**Two complementary additions to the V2 memory schema and dream pass.
Both land for Run 8.**

### DEF-26 — Graded outcome_value via attempt + failure mode

The STIG `Evaluator.signal_for` method now takes an `attempt_number`
kwarg and produces graded values from binary evaluator output. The
`OutcomeSignal` framework already supported this (`value: float ∈ [0,1]`);
we're using more of the range than the original binary helper.

Scoring function (deterministic, no LLM):

| Result | Attempt | value |
|---|---|---|
| passed | 1     | 1.0 |
| passed | 2 – 3 | 0.8 |
| passed | 4+    | 0.5 |
| failed (`evaluator_gap` / `clean_failure`) | any | 0.0 |
| failed (`health_failure` — tip broke the system) | any | −0.2 |

The negative leg is the missing column journey/38.5 named: a tip
retrieved during a healh-failure attempt now subtracts from utility
rather than getting `0` (indistinguishable from "tip wasn't relevant").

CVE preserves binary semantics for now — its failure modes are
mostly external (RPM conflicts, network) rather than learning-curve.
Revisit if CVE shows the same plausible-but-misleading-tip pattern
in future runs.

Confidence stays at 1.0 (the evaluator is deterministic). The
graded `value` is the new signal.

### DEF-27 — Per-retrieval causal attribution

`{stig,cve}.tip_retrievals` gains three columns (migration 0006):

```sql
tip_followed_llm           BOOLEAN
tip_followed_emb           DOUBLE PRECISION
tip_followed_computed_at   TIMESTAMPTZ
```

The dream pass at end-of-run scores each unscored retrieval against
the Worker's *actual fix_script* (read from the run's JSONL — the
attempts.approach column is a 500-char-truncated narrative, not the
real script). Two complementary signals:

- **`tip_followed_llm`**: LLM judge call to vLLM at temperature 0,
  fixed per-skill prompt (`skills/<skill>/prompts/tip_follow_judge.md`).
  Outputs `{"followed": bool, "reasoning": "..."}`. Captures
  *directional* alignment ("tip said use X, worker used Y"). Smoke
  test on the cryptography case: 4/4 correct judgments. Batched 8-way
  concurrent against vLLM; ~10-15 minutes added to end-of-run for
  ~3,500 retrievals.
- **`tip_followed_emb`**: cosine similarity between tip text and
  fix_script, encoded via `sentence-transformers/all-MiniLM-L6-v2`
  on CPU. Deterministic, fast, captures *topical* overlap but is
  known to miss directional alignment on short technical text — the
  cryptography smoke showed 0.705 vs 0.703 for followed vs ignored,
  so the LLM judge is load-bearing for this case. The embedding is
  the deterministic floor and a sanity check on the judge.

Consumers of the new columns:

- **Retrieval ranker** (`memory/retrieval.py:_fetch_hit_rates`) —
  updated to use a `CASE` expression that weights outcome by
  `tip_followed`. LLM-judge `true` → full credit; LLM-judge `false`
  → zero credit even on success; embedding ≥ 0.6 used as fallback;
  pre-DEF-27 rows (both NULL) get 0.5 (conservative).
- **Eviction policy** (`memory/eviction.py`) — same `CASE`. Tips
  retrieved often during successes but rarely actually followed
  earn low `avg_utility` and get retired.
- **Audit tool** (`tools/audit_memory.py`) — surfaces the
  misleading-tip detector query (high outcome, low follow rate) as
  a first-class report section.

Failure semantics: per-row failures (LLM judge HTTP error, parse
failure, embedding NaN) leave that column NULL. The retrieval ranker's
`CASE` expression handles NULL gracefully. Whole-step failures (dream
pass can't load the prompt, can't reach vLLM) leave the entire run's
retrievals NULL and the credit-assignment falls back to outcome-only —
no worse than pre-fix behavior.

### Auxiliary fix — `tip_retrievals.attempt_id` populated

The original schema declared `attempt_id BIGINT REFERENCES attempts(id)`
on `tip_retrievals` (migration 0003), but `log_retrievals` hardcoded
NULL — at retrieval-log time the attempts row doesn't yet exist. The
fix: `MemoryStore.save_attempt` now returns the new `attempts.id`;
ralph.py tracks per-attempt retrieval ids in a dict; a new
`update_retrieval_attempt_ids` UPDATEs the FK in batch after each
save_attempt. Future runs have clean joins for any downstream
analytics (the DEF-27 scoring path uses JSONL, so it works regardless,
but other consumers benefit).

## Alternatives considered

### LLM judge alone (no embedding leg)

Simpler. The LLM judge is the load-bearing signal anyway — the
cryptography case shows embedding alone can't distinguish followed
vs ignored on short technical text. Why keep the embedding?

Because the embedding is the *deterministic-where-correctness-matters*
leg, and it's nearly free to compute (CPU, ~70 seconds for 3,500
encodings). It serves three purposes:

- Cross-validation: if the LLM judge says "followed" but embedding
  is 0.05, the row is anomalous and worth manual review.
- Fallback: when the LLM judge fails for a row (HTTP error, parse
  failure), embedding ≥ 0.6 is a usable proxy.
- Long-term: if we observe over many runs that embedding agrees with
  the judge >95% of the time, we can drop the judge for cost and
  keep just the deterministic signal. The data to make that call
  comes from collecting both.

### Embedding alone (no LLM judge)

Insufficient. The cryptography smoke showed 0.705 vs 0.703 for
followed vs ignored — generic sentence embeddings capture topical
similarity but not directional alignment on short technical text. A
domain-tuned embedding model might do better, but training one is
out of scope.

### Score at retrieval time, not in the dream pass

Costs ~3,000 extra LLM calls during the run (30-60 minutes added to
wall clock) and adds latency to every Ralph turn. The signal isn't
needed at retrieval time — the retrieval ranker reads *historical*
follow rates from prior runs' dream-pass scoring. End-of-run scoring
is the right cadence.

### Self-attestation from the Worker ("I'm following tip Y")

Workers lie or get confused. Cheap to record, expensive to validate.
The judge + embedding combination is more reliable, and the run's
JSONL already preserves the Worker's narrative if we want it later.

### Defer to a later run (Run 9+)

The cryptography case named the failure mode concretely. Deferring
means Run 8 produces another generation of misleading-tip
accumulation under the same broken signal. With sub-10h runs, the
cost of NOT shipping today is one more run cycle of bad data.

## Consequences

### Positive

- **Misleading tips become discoverable**. The audit tool's section 4
  query (`outcome positive, follow rate low`) is now actionable
  rather than aspirational.
- **Retrieval ranking incorporates causation, not just correlation**.
  Tips retrieved during successes whose advice was ignored stop
  accumulating utility scores.
- **Eviction policy automatically retires lucky-neighbor tips**. The
  same `tip_followed` signal feeds the eviction sweep, so the corpus
  self-prunes the misleading items over multiple runs.
- **Graded outcome_value differentiates first-try wins from
  multi-attempt wins** — the dream pass's category-level credit
  assignment (and eventually DEF-03's per-tip credit) can use the
  richer signal.
- **Concrete architectural artifact for the whitepaper**. "We
  noticed credit-assignment was outcome-only and bolted on a causal
  column" is a defensible engineering decision with measurable
  before/after.
- **Backward compatible**. Pre-DEF-27 rows (`tip_followed_*` NULL)
  get 0.5 weight in the new ranker — a conservative fallback that
  doesn't break existing retrieval behavior.

### Negative / accepted trade-offs

- **Dream pass duration grows from ~3 seconds to ~12 minutes** for a
  full STIG run (3,500 retrievals × ~0.2s per judge call with 8-way
  concurrency). End-of-run wall clock impact is invisible. Mitigable
  by sampling top-N retrievals if it ever becomes a problem.
- **Mid-stream signal change**. Run 8's retrievals get graded outcomes
  and follow-scored; Runs 1–7's already-stored rows are unchanged.
  The retrieval ranker averages across the mix. Run 9+ accumulates
  cleaner data; Run 8 is the transition.
- **LLM judge is non-deterministic in the strict sense** (different
  vLLM seeds, different batching). Temperature 0 mitigates but
  doesn't eliminate. Acceptable because the signal is consumed
  statistically (per-tip averages) rather than as a hard filter.
- **The follow-modifier coefficients (1.0 / 0.0 / 0.5) are first-pass
  guesses**. Tunable as Run 8+ data accumulates. Captured here so the
  values are findable in one place rather than buried across two SQL
  blocks.
- **CVE inherits the schema but not the scoring** — CVE's
  `signal_for` still uses the binary helper, and CVE has no
  `tip_follow_judge.md` prompt yet. CVE adopts the pattern in its
  next run; for now the columns exist but stay NULL.

### Reversibility

- Schema additions are additive (new columns, all nullable). A
  rollback is a single migration that DROPs the three columns and
  reverts the retrieval/eviction SQL.
- The graded `signal_for` is a per-skill override; reverting STIG
  to the binary helper is a 5-line diff.
- Pre-fix behavior is recovered by leaving the new columns NULL
  forever — the retrieval ranker's `CASE` handles that path
  identically to the pre-DEF-27 code.

## References

- [`journey/38`](../journal/journey/38-mtp-in-practice.md) — Run 7's wall-clock + fix-rate regression that motivated the deep dive.
- [`journey/38.5`](../journal/journey/38.5-the-cryptography-regression-postmortem.md) — the cryptography case, the smoking gun.
- [`journey/38.6`](../journal/journey/38.6-context-graphs-and-the-tip-causal-signal.md) — context-graph thesis tied to the failure case.
- [`architecture/02`](../journal/architecture/02-context-graphs-decision-provenance.md) — project-agnostic pattern statement.
- [`adr/0016`](0016-graphiti-neo4j-postgres-memory-stack.md) — V2 memory stack this ADR extends.
- [Foundation Capital — Context Graphs: AI's Trillion-Dollar Opportunity](https://foundationcapital.com/ideas/context-graphs-ais-trillion-dollar-opportunity)
- [Mem0 architecture docs](https://mem0.ai) — outcome scoring without per-retrieval attribution.
- [Graphiti / Zep](https://github.com/getzep/graphiti) — bi-temporal facts; the analog of this ADR's "row-level attribution" sits on the edge attributes layer.
- `deferred.md` DEF-26 (graded outcome), DEF-27 (causal attribution), DEF-03 (per-tip credit — the natural next consumer of the new signal).
