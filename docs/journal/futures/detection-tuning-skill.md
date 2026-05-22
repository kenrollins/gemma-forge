---
id: futures-detection-tuning-skill
type: futures
title: "Detection Engineering as a Third Skill — Sigma Rule Tuning Against Labeled Corpora"
date_first_surfaced: 2026-05-22
related:
  - adr/0020-skill-provided-worker-context
  - journey/38.7-runs-8-and-9-what-the-data-said
  - journey/33-second-skill-cve-pivot
status: SPECULATIVE — proposal for a possible weekend project, not built, not scheduled
one_line: "STIG and CVE both shipped as binary-evaluator remediation skills. A third skill — detection rule tuning against labeled threat-telemetry corpora — would put a meaningfully different-shaped problem on the same harness: statistical thresholds instead of pass/fail, output is a spec instead of a system state, cross-run intelligence is about authoring patterns instead of remediation patterns. Surfaced as a candidate KBR Cyber Range / Lab demo skill."
---

# Detection Engineering as a Third Skill — Sigma Rule Tuning Against Labeled Corpora

## What this entry is

A captured proposal, not a commitment. Surfaced 2026-05-22 during demo
planning for a KBR Cyber Range / Lab tech-day showing. The CVE skill
(journey/33) was internally noted as "underwhelming" because its loop
shape duplicates STIG's — both are deterministic-pass/fail remediation
loops. A third skill that's structurally different on the same harness
would prove the architecture isn't just "STIG and STIG-shaped variants."

This document captures the idea, the honest unknowns, an implementation
sketch, and the decision points so that if someone builds it later they
have the proposal as written, not a recollection.

## The proposition

Build a STIG-style autonomous remediation skill, but for **detection
rules** instead of system-configuration rules:

- **Work item**: a Sigma detection rule that's failing some quality
  bar (too noisy, missing known true positives, or both).
- **Evaluator**: run the rule against a labeled threat-telemetry
  corpus, compute precision/recall against the ground-truth labels.
- **Worker**: propose rule modifications (tighten selectors, add
  exclusions, refine field matches) and emit the candidate.
- **Reflector**: read the evaluator's output and explain in
  structured natural language what class of events the rule
  misclassified.
- **Architect**: pick which rule to work on next from the queue,
  reengage when the loop plateaus.

PASS criterion (suggested, not validated): `precision >= 0.95 AND
recall >= 0.80`. May need tuning per dataset; could be reframed as
"F1 above 0.85" if dataset noise makes the per-axis thresholds
unrealistic.

## Why this fits the Ralph architecture

Every existing harness mechanism maps cleanly:

| Harness mechanism | What it provides for detection tuning |
|---|---|
| Reflexion loop | Try rule → check against corpus → reflector explains → tighten or relax |
| Cross-run memory (tips, DEF-27 causal attribution) | Sigma authoring patterns accumulate across runs: "use `Image\|endswith` not `Image\|contains` for path matching", "Powershell encoded-command detection needs case-insensitive match on both `-enc` and `-EncodedCommand`" |
| `EvaluatorMetadata` | Skill declares signal_type=graded (not binary — F1 is continuous), expected_confidence=high, eviction_threshold tunable per corpus |
| Architect re-engagement | After N attempts that can't reach the threshold, ESCALATE with reasoning ("needs a new data source", "needs a behavioral baseline not in this corpus") |
| **DEF-28's `worker_context`** | Returns `{sigma_schema_excerpt, sample_events_for_technique, prior_rule_version}` — gives the Worker the authoritative spec for what a valid Sigma rule looks like AND a representative sample of what it's supposed to catch |
| Per-family reboot batching (CVE pattern) | N/A for detection tuning — rules are read-only changes; no system disruption to handle |
| Snapshot/revert | Trivial — rules are text files. Snapshot is `git stash` shaped |

Zero harness changes. Pure skill-side work.

## The cross-run intelligence story (the actual demo value)

The architectural claim that matters for the KBR audience isn't "we
can tune one rule." It's that **the tip pool accumulated over many
runs is composed of generic Sigma authoring patterns that transfer
across corpora and across threat scenarios**.

Hypothetical demo arc:

> **Run 1 against BOTSv3** (a Splunk SOC training corpus). Ralph
> tunes 100 detection rules. ~60% pass the threshold. Tip pool ends
> with ~150 patterns about Sigma authoring.
>
> **Run 2 against MITRE ATT&CK Evaluation data** (APT29 simulation,
> entirely different threat actor, different telemetry source). Tip
> pool from BOTS is loaded. First-attempt pass rate ~75% because the
> general Sigma authoring patterns transfer even though the attack
> scenario is unrelated.
>
> **Run 3 against DARPA OpTC** (federal red-team gold standard). Tip
> pool now has ~250 patterns. First-attempt pass rate ~85% on a
> corpus Ralph has never seen.

The pitch in one sentence: *"Ralph isn't tuning **this** rule against
**this** corpus — it's learning how to author Sigma rules, and
getting measurably better at it across every run we throw at it."*

This is the *exact* claim STIG demonstrates ("knowledge transfers
across hosts, across baselines"), restated for detection
engineering ("knowledge transfers across threat scenarios, across
corpora"). Same architectural value proposition, fundamentally
different domain.

**Caveat I'm being honest about**: the cross-corpus transfer story is
a hypothesis. It's plausible based on how detection engineering
practitioners describe their work, but it hasn't been validated on
this stack. If the build happens, the first real metric is "did Run 2
benefit from Run 1's tips on a different corpus?" If yes, the demo
arc works. If no, the skill still works — but the marketing line
changes from "cross-corpus generalization" to "Sigma tuning
automation."

## Architectural decision: generic skill, corpus as run input

NOT per-corpus skills (`detection-tuning-bots`,
`detection-tuning-optc`, etc.). Reason: the cross-run intelligence
is about Sigma authoring patterns, which are *corpus-independent*.
Tips learned on BOTS apply to OpTC. Per-corpus skills would
fragment the tip pool and undermine the demo value.

Instead: **one `skills/detection-tuning/` skill**, with corpus as a
per-run configuration:

```bash
forge run detection-tuning --corpus bots-v3
forge run detection-tuning --corpus mitre-eval-apt29
forge run detection-tuning --corpus optc
```

Each run picks up the same tip pool. Each run contributes to it.

## Corpus inventory (claims to verify, do not take from this doc alone)

The following corpora are reported to exist and to be appropriate;
verify directly before building against any of them:

| Corpus | Provenance | Why it might fit | What to verify |
|---|---|---|---|
| DARPA OpTC | DARPA Transparent Computing program | Federal pedigree — strongest credibility with KBR | Actual download size, label format, attack-vs-benign ground truth schema |
| MITRE ATT&CK Evaluations public data | MITRE Engenuity | Per-technique labels mapping to ATT&CK | Which evaluation rounds have public telemetry, format consistency |
| Splunk BOTSv1/v2/v3 | Splunk public release | SOC-analyst-credible scenarios | License terms, dataset size, format (Splunk-specific or portable) |
| EVTX-ATTACK-SAMPLES (sbousseaden GitHub) | Community | Per-technique Windows EVTX, smallest footprint | Whether it's labeled enough for precision/recall, or just per-technique demonstrations |
| Mordor / Security Datasets (OTRF) | Open Threat Research Forge | Multi-scenario labeled telemetry | Maintenance status, schema |

**Recommended pre-build verification**: spend 2 hours actually
downloading one of these and looking at the label structure before
committing to the skill. If a corpus claims to be "labeled" but
labels are coarse-grained or inconsistent, the Evaluator's
precision/recall math becomes meaningless.

**Lowest-friction starting point**: EVTX-ATTACK-SAMPLES. Smallest
footprint, Sigma-native, well-known in the detection-engineering
community.

**Highest-credibility starting point for the demo**: DARPA OpTC,
for the federal provenance. Plan would be: develop against
EVTX-ATTACK-SAMPLES, validate against OpTC, frame OpTC as the
"production-grade" demo dataset.

## Open questions a practitioner should answer before building

The following are questions I genuinely don't know the answers to
and would benefit from someone with detection-engineering experience
to weigh in. If anyone at KBR or in the network has this background,
the answers shape the build significantly:

1. **Is Sigma the right rule language, or should we target Splunk
   SPL / Suricata / YARA-L instead?** KBR's actual SIEM and
   detection stack matters. Sigma is portable and translates to
   most others, but if their team writes Splunk SPL natively, a
   Splunk-native skill is more useful to them.

2. **What does "noisy rule" mean to practitioners?** Are the natural
   thresholds precision-first (fewer false positives) or recall-first
   (catch everything, accept noise)? This affects the PASS criterion.

3. **Is the test-corpus approach how detection engineers actually
   work?** They might iterate against live production data instead,
   in which case the corpus framing is artificial.

4. **Do rules that pass on one corpus actually generalize?** This is
   the architectural-value hypothesis. A skeptical practitioner
   should poke at it.

5. **Where do the rules come from?** A pre-loaded library? Generated
   from threat intel? Imported from SigmaHQ public repo? The
   WorkQueue contract depends on this.

## Implementation sketch (if it gets built)

Approximately a weekend's work given the harness reuse:

**File tree:**
```
skills/detection-tuning/
├── skill.yaml                    # name, version, FailureMode mapping
├── runtime.py                    # DetectionTuningSkillRuntime + sub-runtimes
├── corpus_loader.py              # Loads labeled corpora (BOTS, OpTC, etc.)
├── sigma_eval.py                 # Runs Sigma → SPL → corpus → P/R/F1
├── prompts/
│   ├── architect.md              # Which rule to tune next
│   ├── worker.md                 # Rule-modification instructions
│   └── tip_follow_judge.md       # DEF-27 follow judge for this skill
└── corpora/                      # OR a config pointer to /data/corpora/
```

**Sub-runtime mapping:**
- `DetectionWorkQueue` — loads rules to tune from `--corpus <name>`
- `DetectionExecutor` — applies a candidate rule (writes the file)
- `DetectionEvaluator` — runs candidate → corpus, returns P/R/F1, classifies failure
- `DetectionCheckpoint` — `git stash`-style save/restore of the rule file
- `DetectionSkillRuntime` — bundles them, implements `worker_context()` for DEF-28-style enrichment

**Sigma → SIEM-query conversion**: `sigmac` (the SigmaHQ converter)
is available as a Python package. Convert the candidate Sigma rule
to the target query language (Splunk SPL by default), then run
against the corpus.

**Failure-mode taxonomy:**
- `RULE_TOO_NOISY` — precision below threshold
- `RULE_TOO_NARROW` — recall below threshold
- `RULE_PARSE_FAILURE` — Sigma converter rejected the rule
- `CORPUS_GAP` — rule needs data source not in this corpus (escalate)
- `NEEDS_BASELINE` — rule depends on environmental baseline we don't have (escalate)

The harness handles everything else.

## What this would prove for the architecture

If it works:

1. **The harness isn't STIG-shaped.** It generalizes to structurally
   different problem shapes (statistical thresholds vs binary; output
   is a spec vs a system state).
2. **DEF-28's `worker_context` Protocol is generally useful.** It
   wasn't designed for STIG; it was designed for "skills that have
   an authoritative spec." Sigma rules have one (the schema +
   examples). Confirming this with a second domain is a real
   architectural data point.
3. **Cross-run intelligence transfers across domains within a
   skill** (cross-corpus). The bigger version of the V2 memory
   architecture's value proposition.
4. **Three different demo skills** — compliance remediation (STIG),
   vulnerability remediation with reboots (CVE), detection engineering
   (this). Each a different shape, all on the same harness. The
   "skills are pluggable" claim becomes empirically defensible
   instead of architecturally asserted.

## What this would cost

Engineering: ~2-3 days for a working skill, assuming the corpus
loading is straightforward. Could be a weekend project as proposed.

Time-to-demo-ready: another day for tuning the PASS thresholds and
selecting demo-friendly rules.

Risk: the cross-corpus generalization story might not hold
empirically. The skill still works as "Sigma tuning automation" but
loses its headline architectural value. Mitigation: do Run 1 against
EVTX-ATTACK-SAMPLES, then Run 2 against the same; only commit to the
cross-corpus framing if Run 2 measurably benefits from Run 1's tips.

## Decision points captured for the record

- **Whether to build it**: depends on (a) the KBR meeting outcome
  and whether they want this kind of skill, (b) whether the
  practitioner check above produces a clean go signal, (c) appetite
  for a weekend project.
- **Whether to use Sigma specifically vs the SIEM the audience uses**:
  ask before committing.
- **Whether to ship publicly**: a detection-tuning skill against
  public corpora is shareable as open source; the same skill against
  proprietary corpora is internal-only. Affects framing.
- **Whether the cross-corpus transfer claim survives empirical test**:
  graded by Run 2 data, not in this proposal.

## Caveats and honest unknowns

This proposal was synthesized from general knowledge about detection
engineering as a domain. The specifics about corpora, thresholds,
practitioner workflows, and Sigma conventions are my best guesses,
not validated facts. Before committing development effort, a quick
ground-truth with someone who does detection engineering for a
living would save significant rework.

The architectural pattern (Ralph loop + statistical evaluator +
skill-side spec enrichment) is grounded in our codebase. The domain
specifics aren't.

---

## Related

- [`adr/0020`](../../adr/0020-skill-provided-worker-context.md) — the
  `worker_context` Protocol method this proposal exercises.
- [`journey/38.7`](../journey/38.7-runs-8-and-9-what-the-data-said.md) —
  the four-run arc that motivated the "second-skill-was-underwhelming"
  reflection.
- [`journey/33`](../journey/33-second-skill-cve-pivot.md) — the CVE
  skill pivot, the project's prior "second skill" exercise.
- `futures/harness-as-training-data-factory.md` — the format model
  for this entry: speculative, honest about uncertainty, captured for
  the record without commitment.
