# ADR-0020: Skill-provided Worker context (DEF-28) + follow-aware credit (DEF-03)

- **Status:** Accepted
- **Date:** 2026-05-22
- **Deciders:** Ken Rollins
- **Related:** [ADR-0019](0019-context-graph-outcome-attribution.md), [journey/38.7](../journal/journey/38.7-runs-8-and-9-what-the-data-said.md), [architecture/02](../journal/architecture/02-context-graphs-decision-provenance.md)

## Context

The four-run arc R6 → R9 ([journey/38.7](../journal/journey/38.7-runs-8-and-9-what-the-data-said.md)) surfaced two findings the prior architecture couldn't address:

1. **78 rules failed in every single run.** Sampling the Reflector's text and the actual XCCDF rule descriptions revealed that ~88% of these chronic failures share a single cause: the Worker is guessing the OpenSCAP scanner's contract from the rule's title alone, while the authoritative specification — file paths, exact directive syntax (`typeset -xr` vs `export`), required string values — is sitting in `/usr/share/xml/scap/ssg/content/ssg-rl9-ds.xml` on the VM. The harness has never read it.

2. **Run 9 regressed despite more accumulated DEF-27 data.** R8's dream pass at end-of-run retired 587 tips using the follow-aware utility formula. R9 went on to lose 9 audit-category rules, suggesting the original DEF-27 follow-modifier coefficient (`0.0` for "followed=false but rule passed") was too punitive — tangentially-helpful tips were being scored as zero-contribution and getting retired.

This ADR records the engineering decisions for both findings, plus a tuning change.

## Decisions

### 1. DEF-28 — `SkillRuntime.worker_context(item) -> dict | None`

A new optional method on the `SkillRuntime` Protocol lets each skill enrich the Worker's apply_fix prompt with whatever its evaluator's authoritative spec looks like. STIG returns the XCCDF rule description; CVE could later return Vuls advisory metadata; future skills opt in or skip.

```python
# gemma_forge/harness/interfaces.py
class SkillRuntime(Protocol):
    ...
    def worker_context(self, item: WorkItem) -> Optional[dict]:
        """Per-item enrichment for the Worker's apply_fix prompt."""
        return None  # default: skip the section
```

Ralph.py's prompt assembly calls it and adds a `work_item_context` section to the prompt when non-None. Failure-safe: any exception is logged and the section is skipped, preserving pre-DEF-28 behavior.

### 2. STIG XCCDF prefetch + cache

`StigSkillRuntime` gains `prefetch_xccdf_descriptions()` — an async helper that:

1. SSHes to the VM once, `cat`s the 27 MB SCAP datastream
2. Parses locally with `xml.etree` (avoids per-rule xmllint round-trips)
3. Builds an in-memory dict: `rule_id → {title, description, source}`

Cost: ~5 seconds at run start. Subsequent `worker_context()` calls are O(1) dict reads. Pre-fetch is invoked once during ralph.py's startup phase before the Ralph loop begins.

Empirical verification on the live datastream: **1,523 rule descriptions extracted**, including the canonical text the Reflector has been quietly diagnosing for months. The `accounts_tmout` description literally says *"typeset -xr"* and *"/etc/profile.d/tmout.sh"* — what the Worker has been spending 10 attempts oscillating around in every run since R1.

### 3. DEF-03 — follow-aware category credit in the dream pass

`compute_category_credits` adds a second signal alongside the legacy remediated/escalated counts:

```python
follow_aware_signal = 2.0 * AVG(outcome_value × confidence × follow_modifier) - 1.0
```

Aggregated per-category via a `tip_retrievals × work_items` join. `CategoryCredit.confidence_signal` prefers the follow-aware signal when ≥5 retrievals in the category have DEF-27 data; falls back to the legacy `2 * success_rate - 1` formula otherwise (pre-DEF-27 runs, sparse categories).

The architectural shift: lesson confidence in the dream pass now reflects *"did wins come from followed advice"* rather than just *"did wins happen"*. A category with 100% wins but 0% follow rate (lessons never actually used) loses confidence; a category with 50% wins all from followed advice gains.

### 4. DEF-27 coefficient tuning

The follow-modifier value for "followed=false, outcome positive" moves from `0.0` to `0.3`:

```sql
CASE
    WHEN tip_followed_llm IS TRUE THEN 1.0
    WHEN tip_followed_llm IS FALSE THEN 0.3   -- was 0.0
    WHEN tip_followed_emb IS NOT NULL AND tip_followed_emb >= 0.6 THEN 1.0
    WHEN tip_followed_emb IS NOT NULL AND tip_followed_emb <  0.6 THEN 0.3   -- was 0.0
    ELSE 0.5
END
```

Justification: a tip that's in the prompt and gets ignored by the Worker still contributes to the agent's reasoning context, even if the literal advice isn't followed. R9's audit-category regression (journey/38.7) showed that treating these as zero-contribution retires tips that turn out to be load-bearing. The 0.3 keeps the directional signal (followed tips earn 3.3× more credit than ignored ones) while preserving partial credit for context-shaping.

Applied identically in `memory/retrieval.py:_fetch_hit_rates` and `memory/eviction.py`.

## Alternatives considered

### Worker-callable tool instead of prompt enrichment

Could expose `get_xccdf_description(rule_id)` as an ADK tool the Worker decides to call. Rejected because:

- The Worker doesn't know it should ask — the discovery is precisely that the Worker has been guessing for months without realizing the spec exists.
- An extra LLM-to-tool round-trip per attempt adds latency.
- Pre-flight enrichment is one decision (does the rule have a description? yes → include it) vs N decisions during the loop.

### Embedding the XCCDF descriptions on disk in the repo

Ship a `skills/stig-rhel9/xccdf_descriptions.json` file with the pre-extracted descriptions. Rejected because:

- The SCAP content is versioned with the system's scap-security-guide package. Stale shipped JSON would be wrong after VM updates.
- The runtime extraction takes 5 seconds — cheap, current, automatic.
- Skill stays self-contained without an "update this JSON when the VM updates" maintenance dance.

### LLM-judge for "is this rule actually fixable"

Skip rules that look reboot-required or out-of-scope. Rejected because:

- Coupling skill capabilities to LLM judgment adds non-determinism to which rules even get tried.
- The harness already supports a clean opt-out via `EvaluatorMetadata.deferrable_failure_modes` + `resolve_deferred()` (CVE's reboot pattern). STIG can adopt that when reboot integration ships (DEF-29).

## Consequences

### Positive

- **DEF-28 closes the chronic-failure long tail's largest known cause**. The XCCDF description for 7 of 8 sampled chronic failures literally contains the answer the Worker has been guessing at. Projected: 25-40 of the 78 chronic failures convert to remediated.
- **Skill plug-and-play preserved**. `worker_context` is opt-in on the Protocol; non-STIG skills don't need changes. STIG-specific knowledge (XCCDF format, SCAP datastream path) lives in the STIG skill, not in the harness.
- **DEF-03 makes the dream pass do per-tip-aware category credit**. Categories where wins were lucky-neighbors get less confidence boost; categories where wins were earned via followed tips get more.
- **DEF-27 tuning addresses R9's audit-category regression directly**. The R9→R10 comparison will isolate the coefficient effect.
- **The XCCDF description is what the demo audience would have wanted**. A live demo can now show "here's the rule, here's the spec, here's how the Worker matches the spec" — visceral and concrete.

### Negative / accepted trade-offs

- **Prompt size grows**. ~300 tokens per Worker apply_fix prompt for the XCCDF description. Within the existing 8K context budget; `assemble_prompt` already handles size-based truncation.
- **The XCCDF descriptions can be verbose**. Some rules have multi-paragraph descriptions covering related policy variations. Truncated to 1500 chars in the extractor; longer descriptions still get the leading content (where the operative spec usually lives).
- **First-run cold start adds ~5 seconds**. Pre-fetch runs before the Ralph loop starts; not in any user-facing path.
- **DEF-03's follow-aware signal can be aggressive**. In R9 data, the audit category's category-level signal is -0.96 with follow-aware vs -0.46 with legacy. The architecturally-correct interpretation: lessons in audit weren't being followed and shouldn't accrue confidence. Run 10 will show whether this is too harsh.

### Reversibility

- DEF-28: `worker_context` returns None → no section added → behavior identical to pre-DEF-28. One-line revert.
- DEF-03: `CategoryCredit.confidence_signal` falls back to legacy if `follow_aware_signal` is None or sample size too small. Reverting requires not populating those fields — also one-line.
- DEF-27 tuning: the coefficient is a number in two SQL queries.

## Bets going into Run 10

- **Fix rate**: 64-68% (vs R6/R8's ~62%). DEF-28 contributes most; DEF-03/27 tuning corrects R9's regression.
- **Audit category**: 50-65% (vs R8/R9's 27-36%). The 50 chronic audit failures all share the OpenSCAP-static-file-check pattern that XCCDF descriptions address.
- **Cryptography**: minor improvement (35-50%, vs ~25-33%). Mostly reboot-required; awaits DEF-29.
- **Wall clock**: similar to R8 (11-12h). DEF-28 prefetch adds 5s; XCCDF section adds ~300 tokens per Worker prompt (minimal effect on tok/s).

If Run 10 lands in those ranges, DEF-28's projected lift is empirically validated and DEF-29 (reboot adoption) becomes the obvious next architectural change. If Run 10 doesn't move, the XCCDF description alone isn't enough and the Worker's reasoning ceiling is closer than we thought.

## References

- [`journey/38.7`](../journal/journey/38.7-runs-8-and-9-what-the-data-said.md) — the four-run analysis that surfaced both findings.
- [`architecture/02`](../journal/architecture/02-context-graphs-decision-provenance.md) — the per-retrieval causal pattern this builds on.
- [`adr/0019`](0019-context-graph-outcome-attribution.md) — the prior DEF-26/27 decisions this ADR amends with coefficient tuning + DEF-03 consumer-side completion.
- [`deferred.md`](../deferred.md) DEF-28, DEF-29 (reboot adoption — next change), DEF-03.
- OpenSCAP / SCAP datastream: `/usr/share/xml/scap/ssg/content/ssg-rl9-ds.xml` on the target VM.
