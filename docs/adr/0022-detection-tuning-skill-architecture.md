# ADR-0022: detection-tuning — graded skill on the binary-shaped harness

- **Status:** Accepted
- **Date:** 2026-05-24
- **Deciders:** Ken Rollins
- **Related:** [ADR-0020](0020-skill-provided-worker-context.md), [futures/detection-tuning-skill](../journal/futures/detection-tuning-skill.md), [futures/detection-tuning-preflight](../journal/futures/detection-tuning-preflight.md)

## Context

STIG (ADR-0006) and CVE (entry 33) are both deterministic-binary skills: the scanner returns PASS or FAIL, the fix either landed or didn't, the harness's `FailureMode` enum and `EvalResult.passed` boolean fit cleanly. The reflexion loop tuned tightly to that shape.

The third skill — **detection-tuning** — is structurally different and exists in part to *test* that the harness is plug-and-play across problem shapes (entry 38.7 noted CVE's "underwhelming" similarity to STIG; this is the rebuttal). Specifically:

- **Outcome is graded, not binary.** Sigma rule quality is precision/recall/F1 on a labeled corpus. There's no "correct" rule — only better/worse points on a P-R tradeoff curve. The proposal's suggested PASS bar is `P >= 0.95 AND R >= 0.80`, but a rule sitting at `(0.92, 0.85)` is informatively-close-to-passing, not zero-value, the way `OpenSCAP: FAIL` is.

- **The "fix" is a spec, not a system state.** The Worker writes a `.yml` file. There is no `/etc/`, no `sshd_config`, no `dnf` advisory — just a text edit. Snapshot/revert is `git stash`-shaped (in-memory text dict here; trivial).

- **There is no mission app to protect.** Health checks, immediate-revert routing, journal scraping — none of it applies. The corpus is a static dataset; the only thing that can break is the eval pipeline itself (asserted internally).

- **The interesting architectural claim is cross-corpus.** The proposal's headline value is that Sigma authoring patterns accumulated across runs transfer across different labeled corpora ("Ralph learns how to author Sigma rules, getting measurably better on each new corpus it sees"). That requires the corpus to be a per-run input, not a per-skill compile-time constant.

This ADR records the engineering decisions made to fit detection-tuning onto the existing harness without changing the harness's binary-shaped contracts.

## Decisions

### 1. Graded outcome via `EvaluatorMetadata.signal_type="graded"` + F1 as `OutcomeSignal.value`

`DetectionEvaluator.metadata` declares `signal_type="graded"` (vs STIG's `"binary"`). `signal_for(result, attempt_number)` maps:

- `result.passed=True` → `value = damp(attempt_number) * f1` where damp = 1.0 (try 1), 0.9 (tries 2–3), 0.7 (4+) — mirrors STIG's attempt-aware damping but uses F1 instead of a flat 1.0/0.8/0.5 ladder so partial-credit tuning is preserved.
- `result.passed=False` → `value = f1` directly — a near-miss (F1=0.85) is informative about *almost*-helpful tips even when below the PASS bar.

Confidence is always `1.0` (the evaluator is deterministic — same rule + same corpus = same score).

`EvalResult.passed` is `(precision >= pass_precision AND recall >= pass_recall)`, defaulting to `(0.95, 0.80)` from the proposal. Skills override these per-corpus in the manifest if dataset noise makes the thresholds unrealistic.

### 2. `FailureMode` enum extension for detection-specific routing

The existing enum (HEALTH_FAILURE / EVALUATOR_GAP / FALSE_NEGATIVE / CLEAN_FAILURE / NEEDS_REBOOT / RPM_CONFLICT / POLICY_VIOLATION) doesn't map cleanly to detection-tuning's failure shapes. Adding four new members:

- `RULE_TOO_NOISY` — precision below threshold, recall acceptable
- `RULE_TOO_NARROW` — recall below threshold, precision acceptable
- `RULE_PARSE_FAILURE` — rule uses Sigma constructs the evaluator doesn't support (`|cidr`, list-of-dicts blocks, exotic conditions)
- `CORPUS_GAP` — the rule's `logsource:` declaration maps to events the corpus doesn't contain

The two-axis-loser case (both P and R below threshold) reuses `RULE_TOO_NOISY` with `signals["detection_failure_mode"]="rule_too_noisy_and_narrow"` rather than minting a fifth enum value — keeps the enum to canonical failure axes and uses the signals dict for the conjunction.

Existing skill behavior is unchanged: STIG and CVE never produce these new values, so their routing is untouched.

### 3. Hex value normalization at corpus load, not at matcher

Real Windows telemetry stores access masks as zero-padded hex (`0x001fffff`); SigmaHQ rule authors write the canonical un-padded form (`0x1fffff`). The Sigma `|contains` modifier is substring-match — `"0x1fffff" in "0x001fffff"` is False as strings — and a naïve evaluator scores zero matches even when every event obviously should have matched.

Two places this could be fixed:

- **In the matcher**: every comparator promotes both sides to int when they look like hex. Adds branch noise to every field check; tricky semantics for `|contains` of hex (does `"0x10" contains "0x1"` mean "16 contains 1"?).
- **In the corpus loader**: strip leading zeros from `0x0+…` strings at load time so storage is canonical. One place to fix; matcher stays simple.

The corpus is the source of variance (different EVTX parsers pad differently); rules are written to a stable convention. Normalize the data, not the matcher. `corpus_loader._normalize_hex_cell` runs once per cell at load time over a curated set of hex-bearing columns (`GrantedAccess`, `Keywords`, `Hashes`).

### 4. Corpus-as-input architecture — sibling loader classes, no shared ABC

The skill needs to score the same Sigma rule against different labeled corpora — EVTX-ATTACK-SAMPLES today, OTRF Security-Datasets today, DARPA OpTC potentially later. Three structural shapes were considered:

- **One loader class with a `format=` arg**: keeps the API singular but mixes EVTX-CSV and SDS-NDJSON parsing in one place; gets uglier with each added corpus.
- **A shared `Corpus` ABC with subclass per corpus**: clean Python, but the contract is only three methods (`total_events`, `scope_for_logsource`, `label_positives`) — an ABC for three methods that already match is ceremony over substance.
- **Sibling classes, duck typing on the three-method protocol**: `CorpusLoader` for EVTX-CSV, `SdsCorpus` for SDS-NDJSON; same method signatures, no inheritance. `SdsCorpus.label_positives = staticmethod(CorpusLoader.label_positives)` shares the labeling logic since both corpora synthesize a `EVTX_FileName` column.

The third was picked. Adding a third corpus is `cp SdsCorpus.py NewCorpus.py + edit _load()`; if a fourth arrives that wants something the existing implementations don't expose, *that's* the moment to extract a Protocol.

The `Evaluator` accepts whichever corpus is passed in. No skill-side code knows which corpus it's scoring against — the corpus name lives in the manifest, the manifest dictates which loader to instantiate, the harness's `_build_skill_runtime` passes the instance through. Cross-corpus runs just instantiate a different loader at startup.

### 5. Skill-side helpers in `gemma_forge/harness/tools/sigma/`, not in the skill dir

STIG's helpers live at `gemma_forge/harness/tools/openscap.py`; CVE's at `gemma_forge/harness/tools/vuls.py`. Following that pattern, detection-tuning's Sigma evaluator and corpus loaders live at `gemma_forge/harness/tools/sigma/{corpus_loader,sigma_eval}.py`. The skill dir holds only the manifest, prompts, and `runtime.py` (the harness-interface adapter).

Two reasons this matters:

- **Reusability**: a hypothetical second detection-engineering skill (Splunk SPL tuning, YARA-L tuning) would share the corpus-loading infrastructure without reaching across skill directories.
- **Loading model**: the harness's `importlib.util.spec_from_file_location` loads skill `runtime.py` ad-hoc, not as a package. Co-located helpers in the skill dir would need a relative-import workaround. Helpers in `gemma_forge/` are imported normally.

### 6. DEF-28 `worker_context` applied to a graded-skill spec

ADR-0020 introduced `worker_context` for STIG's XCCDF descriptions. Detection-tuning's `worker_context` returns three keys per item:

- `description`: the rule's own `title` + `description` text (Sigma's authoritative intent)
- `current_rule_yaml`: the working-copy text the Worker should iterate on (vs guessing from a blank slate)
- `sample_positive_events`: three positive events from the labeled corpus, serialized as JSON — so the Worker sees the actual field values it must match against, not just the rule's abstract specification

This is the same architectural pattern as STIG's XCCDF prefetch — *the Worker needs to see the authoritative spec, not infer it from the work-item title* — applied to a graded-skill domain. Validating ADR-0020's claim that `worker_context` is generally useful (not just for STIG-shaped skills) was an explicit design goal of this skill.

## Alternatives considered

### Promoting the two-axis-loser case to a fifth `FailureMode`

A `RULE_TOO_NOISY_AND_NARROW` enum value would be slightly clearer at the routing level than overloading `RULE_TOO_NOISY` with a `signals` discriminator. Rejected because:

- The harness's `FailureMode` is the *routing* axis; it should stay canonical (one tag per behavior).
- Both two-axis-loser routing and one-axis-loser routing are the same right now (CLEAN_FAILURE-equivalent triage + reflexion).
- The conjunction is informative for the *Reflector / Worker* (different tuning strategy), and those read `signals` directly.

### Embedding pysigma in the evaluator instead of parsing YAML

Using pysigma's parsed `SigmaRule` AST instead of walking the raw YAML dict is the "correct" way per the Sigma ecosystem. Rejected for this build because pysigma's primitives are structured for backend codegen (SPL, KQL, ESQL) and don't map cleanly to "evaluate against a pandas Series." Walking the YAML directly gives us the exact semantics in ~80 lines instead of fighting pysigma's type lattice. If a future construct can't be handled cleanly, the path forward is adopting pysigma's parser for that subset rather than rewriting the whole evaluator.

### Per-rule-technique automatic labeling instead of curated `positive_filename_keywords`

The current manifest declares per-work-item `positive_filename_keywords` (human-curated). An alternative is automatic labeling: parse the rule's `tags: [attack.t1003.001, ...]` and infer positive corpus files by technique→filename heuristics. Rejected for now because:

- Human-curated labels match real detection-engineering practice (ground truth IS curated by analysts in this domain).
- Auto-inference would silently mislabel and inflate F1 noise.
- The cost of curation per rule is ~30 seconds; the queue is small.

If the work queue grows to hundreds of rules per corpus, the cost shifts and inference becomes attractive — *as a supplement* to curation, not a replacement.

## Consequences

### Positive

- **The harness *is* plug-and-play across problem shapes.** Same five interfaces, same reflexion loop, same memory system — fed by a graded evaluator instead of a binary one, with a corpus instead of a system. The "skills are pluggable" claim is now empirically defensible (three skills, two different shapes) instead of architecturally asserted.
- **DEF-28 `worker_context` validated in a second domain.** STIG returns XCCDF; detection-tuning returns Sigma schema + samples. Pattern works in both. ADR-0020's bet that `worker_context` was generally useful was right.
- **Cross-corpus is wireable.** `CorpusLoader` and `SdsCorpus` plug into the same `Evaluator` and `SkillRuntime` shapes. When Path B's harness wire-up lands, swapping corpora is a `--corpus <name>` flag.
- **Reusable Sigma infrastructure.** Future SPL/KQL/YARA-L tuning skills inherit the corpus loaders + Sigma evaluator essentially for free.

### Negative / accepted trade-offs

- **Graded skill needs more memory samples before confident eviction.** `EvaluatorMetadata.min_retrievals_before_eviction=10, eviction_threshold=0.5` (vs STIG's 3, 0.3). Conservative defaults; tune after the first real run produces data.
- **The Sigma evaluator covers the common 80% of constructs, not 100%.** `|cidr`, list-of-dicts blocks, exotic conditions raise `RuleParseError` and route to `RULE_PARSE_FAILURE`. Acceptable: the Architect's prompt SKIPs these and the loop moves on to a tuneable rule. If a high-value rule turns out to need the unsupported subset, that's the moment to invest.
- **Cross-corpus per-rule labeling diverges.** EVTX-ATTACK-SAMPLES uses filename keywords like `mimikatz`/`lsass`/`hashdump`; SDS uses `empire_*`/`covenant_*`/`cmd_*`/`psh_*` prefixes. The first cross-corpus run will surface "rules that scored well on corpus A score 0 on corpus B because the labels don't fit" — that's an *architectural* finding, not a defect. The manifest's labels block likely needs to become per-corpus.

### Reversibility

- `FailureMode` enum additions are append-only; existing skills are untouched.
- `signal_type="graded"` is a per-skill declaration; STIG/CVE stay binary.
- The corpus-loader-per-corpus pattern reverses to "one loader with format flag" trivially if the second corpus turns out to be the last one we'll ever have.
- `worker_context` returning None is a one-line revert per ADR-0020's contract.

## What success looks like

The first end-to-end `forge run detection-tuning --corpus evtx-attack-samples` produces a tip pool. A subsequent `forge run detection-tuning --corpus sds` against the same five rules has *measurably higher* first-attempt success rate because the tip pool transfers. If yes, the cross-corpus story is empirically supported and the demo writes itself. If no, the skill still works as "Sigma tuning automation" — the architectural claim narrows but the build doesn't.

## References

- [`futures/detection-tuning-skill.md`](../journal/futures/detection-tuning-skill.md) — the original proposal
- [`futures/detection-tuning-preflight.md`](../journal/futures/detection-tuning-preflight.md) — pre-flight findings + Friday-night / Path A / Path C build notes
- [`ADR-0020`](0020-skill-provided-worker-context.md) — DEF-28 `worker_context`, the pattern this skill validates in a second domain
- `gemma_forge/harness/interfaces.py` — Protocol contracts; FailureMode extension lives here
- `gemma_forge/harness/tools/sigma/` — corpus loaders + Sigma evaluator
- `skills/detection-tuning/runtime.py` — the SkillRuntime adapter
- Friday-night commit: `5bbde57`
- Path A commit (real prompts + multi-item queue): `5ff98b6`
- Path C commit (second corpus, SDS): `bd06ba6`
