# ADR-0022: Detection-tuning as the third skill — graded outcomes, corpus-as-input, manifest dispatch

- **Status:** Accepted (architecture as built 2026-05-25; skill set aside — see Consequences)
- **Date:** 2026-05-25
- **Deciders:** Ken Rollins
- **Related:** [ADR-0020](0020-skill-provided-worker-context.md), [ADR-0023](0023-network-pentest-skill-architecture.md)

> Recorded after the fact (2026-06-13) to file the decision that built the
> third skill. The architecture below is as-built; the build narrative and
> honest outcome are in
> [`journey/39`](../journal/journey/39-detection-tuning-third-skill-ships.md),
> the original proposal in
> [`futures/detection-tuning-skill`](../journal/futures/detection-tuning-skill.md).

## Context

STIG ([ADR-0006](0006-disa-stig-profile-not-cis.md)) and CVE remediation
were the first two skills on the Ralph harness. Both are the **same problem
shape**: a binary evaluator (rule passes or fails an OpenSCAP/OVAL rescan),
a system-state target, fix-and-rescan. CVE was internally judged
"underwhelming" precisely because its loop duplicated STIG's — it proved the
harness handled a *sibling*, not a structurally different problem.

The "skills are pluggable" claim needed a test against a problem shape that
was genuinely different along three axes:

1. **Outcome type** — statistical (precision/recall/F1 in [0,1]), not boolean.
2. **Target** — the artifact under change is a *spec* (a detection rule),
   not a running system; there is no mission app to protect.
3. **Cross-run intelligence** — the transferable knowledge is *authoring
   patterns* ("use `Image|endswith` not `Image|contains`"), not remediation
   patterns tied to a host.

**Detection engineering** (tuning Sigma rules against labeled
threat-telemetry corpora) is the candidate that exercises all three. If the
same five-interface harness handles it, the architecture is not STIG-shaped.

## Decision

Build **one generic `skills/detection-tuning/` skill** on the existing
harness, with the following architecture:

- **Corpus as a per-run input, not per-corpus skills.** `forge run
  detection-tuning --corpus <name>`. The tip pool is **corpus-independent**
  (Sigma authoring patterns transfer across corpora), so per-corpus skills
  (`detection-tuning-bots`, `…-optc`) are rejected — they would fragment the
  pool and kill the cross-corpus-transfer value.
- **Graded evaluator.** The skill declares `signal_type=graded` (continuous
  F1, not binary). PASS bar: `precision ≥ 0.95 AND recall ≥ 0.80`, with
  `F1 ≥ 0.85` as the fallback framing. Failure taxonomy: `RULE_TOO_NOISY`,
  `RULE_TOO_NARROW`, `RULE_PARSE_FAILURE`, `CORPUS_GAP` (escalate),
  `NEEDS_BASELINE` (escalate).
- **Manifest-driven dispatch.** The skill declares its runtime builder in
  `skill.yaml`'s `runtime:` block; `ralph.py` carries **zero** references to
  "detection-tuning". This is the first skill wired entirely this way —
  making the "drop a skill dir and the harness picks it up" claim
  empirically defensible.
- **`worker_context` for a graded domain** ([ADR-0020](0020-skill-provided-worker-context.md)/DEF-28).
  Returns `{sigma_schema_excerpt, sample_events_for_technique,
  prior_rule_version}` — the authoritative spec of a valid rule plus a
  representative sample of what it must catch. First application of the
  Protocol outside STIG's XCCDF descriptions.
- **Sub-runtimes:** `DetectionWorkQueue` (loads rules per `--corpus`),
  `DetectionExecutor` (writes the candidate rule), `DetectionEvaluator`
  (Sigma → SPL via `sigmac` → corpus → P/R/F1 + failure class),
  `DetectionCheckpoint` (`git stash`-shaped — rules are text), and
  `DetectionSkillRuntime` bundling them.
- **Develop against EVTX-ATTACK-SAMPLES** (lowest friction, Sigma-native);
  hold DARPA OpTC as the higher-credibility corpus for later.

Zero harness changes — pure skill-side work, by design.

## Alternatives considered

- **A binary evaluator (STIG-shaped) for detection too.** Rejected: it would
  duplicate the CVE mistake and prove nothing new. The whole point was to
  exercise `signal_type=graded`, which had never run before this skill.

- **Per-corpus skills.** Rejected: the cross-run intelligence is
  corpus-independent Sigma authoring patterns; per-corpus skills fragment the
  tip pool and undermine the transfer-across-corpora claim that is the skill's
  architectural value.

- **Don't build a third skill.** Rejected: with only STIG + a STIG-shaped CVE
  sibling, "skills are pluggable" remained asserted, not demonstrated. A
  structurally different third skill was the cheapest way to test it.

- **Target a native SIEM language (Splunk SPL / Elastic ESQL) instead of
  Sigma.** Set aside: Sigma is portable and `sigmac`-translatable to most
  targets, which keeps the skill audience-agnostic. A native-format variant
  remains open if a specific audience warrants it.

## Consequences

### Positive

- **The harness is not STIG-shaped.** Graded outcomes, spec-as-target, and
  corpus-as-input all ran on the unchanged five-interface harness.
- **`worker_context` generalizes** beyond STIG — a real second-domain data
  point for [ADR-0020](0020-skill-provided-worker-context.md).
- **Manifest dispatch works** — `ralph.py` instantiated a skill it has no
  knowledge of, from `skill.yaml` alone.
- **Cross-corpus tip transfer was observed** — authoring patterns measurably
  carried across corpora, the bigger version of the memory-stack value prop.
- The architecture transfers directly to the fourth skill
  ([ADR-0023](0023-network-pentest-skill-architecture.md)), which reused this
  manifest + sub-runtime + `worker_context` shape.

### Negative / accepted trade-offs

- **The skill itself produced 0/5 PASS** on the public corpora (both runs).
  Two causes, both downstream of framing, not the harness: the PASS bar
  (`P ≥ 0.95 AND R ≥ 0.80`, generic-ML thresholds) was misaligned with how
  detection engineers actually tune, and the corpora label at the *file*
  level (EVTX-ATTACK-SAMPLES, SDS), capping achievable F1 around 0.7–0.75
  regardless of rule quality. See
  [`journey/39`](../journal/journey/39-detection-tuning-third-skill-ships.md).
- **The skill is set aside** as a standalone result while retained as
  architectural validation. A skill that proves the architecture is not
  automatically a skill that produces a clean result — both properties matter,
  and this one had the first without the second.
- A graded evaluator needs careful, domain-true thresholds; lifting
  generic-ML numbers is a trap this skill walked into and documented.

## References

- [`futures/detection-tuning-skill`](../journal/futures/detection-tuning-skill.md) — the original proposal
- [`journey/39`](../journal/journey/39-detection-tuning-third-skill-ships.md) — the build + honest 0/5 outcome
- [ADR-0020](0020-skill-provided-worker-context.md) — the `worker_context` Protocol this skill exercises in a graded domain
- [ADR-0023](0023-network-pentest-skill-architecture.md) — the fourth skill, which reuses this architecture
