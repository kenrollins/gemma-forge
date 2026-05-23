# ADR-0021: STIG adopts the per-family reboot pattern + OVAL criteria enrichment

- **Status:** Accepted
- **Date:** 2026-05-22
- **Deciders:** Ken Rollins
- **Related:** [ADR-0020](0020-skill-provided-worker-context.md), [journey/38.9](../journal/journey/38.9-def-28-in-production.md), [journey/36](../journal/journey/36-per-family-reboot-batching.md), [journey/37](../journal/journey/37-per-family-reboot-batching-landed.md)

## Context

Run 10 finished at 5.95h / 89.6% fix rate ([journey/38.9](../journal/journey/38.9-def-28-in-production.md)) but escalated 26 rules. Per-rule analysis of those 26 sorted them into four buckets:

| Bucket | Count | Root cause |
|---|---:|---|
| Reboot-required | 8-11 | Reflector + Architect both name "kernel reboot required" |
| Scanner-gap edge cases | 11 | Worker did the right thing per XCCDF description, but scanner expects more specific syntax/state than the description captured |
| Audit-kernel-state | 4 | Could be reboot, could be audit-subsystem reload |
| Environmental/policy | 3 | Need human judgment |

The reboot-required bucket needs *runtime behavior* the architecture didn't have. The scanner-gap bucket needs *richer input* than DEF-28's description alone provided. This ADR records the engineering decisions for both, bundled because they ship together for Run 11.

## Decisions

### 1. DEF-29 — STIG opts into the per-family reboot pattern from CVE

The harness already supports deferred-verification via `EvaluatorMetadata.deferrable_failure_modes` + `SkillRuntime.resolve_deferred()`. CVE proved out the per-family reboot architecture in [journey/36](../journal/journey/36-per-family-reboot-batching.md) and [journey/37](../journal/journey/37-per-family-reboot-batching-landed.md). STIG mirrors that pattern for its own reboot-required rules.

Specifically:

**StigEvaluator changes:**
- `metadata.deferrable_failure_modes = ["needs_reboot"]`
- A curated `_REBOOT_REQUIRED_RULES` set (11 rules — the cryptography/FIPS family + grub2_audit_argument + aide_use_fips_hashes + enable_fips_mode)
- `evaluate()` returns `FailureMode.NEEDS_REBOOT` when a rule in that set is in the `evaluator_gap` state (system healthy, rule still failing). The harness then defers the item rather than retrying — preserving the Worker's on-disk changes.

**StigSkillRuntime.resolve_deferred:**
- Groups deferred items by family via `_stig_reboot_family()`:
  - `fips` — all cryptography rules + aide_use_fips_hashes (depend on kernel FIPS mode being active)
  - `kernel-cmdline` — grub2_audit_argument (kernel parameter change)
- Per family: save snapshot → reboot → wait 120s for SSH → mission healthcheck → per-item re-evaluate via `stig_check_rule` → roll back family snapshot on failure
- Always deletes the family snapshot after (success or failure)

Key difference from CVE's `resolve_deferred`: STIG doesn't need an *apply* step. The Worker's apply already happened during the failed attempt loop, and because the harness skips revert when `failure_mode` is in `deferrable_failure_modes`, the change is still on disk when resolve_deferred runs. STIG's flow is purely "reboot the system + re-verify each item."

The curated rule list is maintained rather than pattern-matched because "needs reboot" isn't reliably inferable from the rule id alone. Easier to add a rule than to debug a regex that misfires on a similarly-named non-reboot rule.

### 2. DEF-28-deeper — OVAL criteria in `worker_context`

DEF-28 ([ADR-0020](0020-skill-provided-worker-context.md)) gave the Worker the XCCDF rule *description* — natural-language text from `<xccdf:description>`. That closed the audit category gap dramatically (R10: 27% → 93%) but the remaining 11 scanner-gap edge cases were rules where the description was ambiguous or missed a specific check the scanner applied.

DEF-28-deeper extends `extract_xccdf_descriptions()` to also extract the OVAL definition's criteria tree. The OVAL `<criterion>` elements have natural-language `comment` attributes that describe *exactly* what the scanner verifies — file paths, AND/OR logic, value bounds, negation flags. The criteria tree is rendered as nested bullets and surfaced in `worker_context()` as `oval_criteria`.

Empirical examples from the live datastream:

```
accounts_tmout:
  ALL of:
    - TMOUT value in /etc/profile <= var_accounts_tmout
    - TMOUT value in /etc/profile.d/*.sh <= var_accounts_tmout
    - At least one config file has TMOUT defined
    - All configured TMOUT values must be >= 1

mount_option_boot_nosuid:
  ALL of:
    - ANY of:
      - nosuid on /boot
      - NOT: /boot does not exist
    - ANY of:
      - nosuid on /boot in /etc/fstab
      - NOT: /boot does not exist in /etc/fstab

audit_rules_dac_modification_chown:
  ANY of:
    - ALL of:
      - audit augenrules 32-bit chown
      - audit augenrules 64-bit chown
    - ALL of:
      - audit auditctl 32-bit chown
      - audit auditctl 64-bit chown
```

The accounts_tmout criteria explain why R10's Worker couldn't satisfy the rule despite trying: it set TMOUT in *one* config file when the OVAL says "TMOUT value in /etc/profile AND TMOUT value in /etc/profile.d/*.sh AND at least one defined AND all values >= 1." The Worker only matched some of those clauses.

mount_option_boot_nosuid's criteria explain the scanner-gap on that rule: the scanner checks BOTH the live mount state AND the `/etc/fstab` config. R10's Worker only modified `/etc/fstab` and remounted — the live state probably didn't reflect `nosuid` because `/boot` couldn't be remounted with `nosuid` while in use (would need a reboot).

The criteria are rendered with operator semantics (ALL/ANY/NOT) so the Worker can reason about the boolean structure. Criterion elements with no `comment` attribute fall back to surfacing the test_ref id, which is less useful but at least leaves a breadcrumb.

The prompt section adds ~100-300 tokens per rule — well within the apply_fix budget.

## Alternatives considered

### Follow OVAL test references to actual test/object/state elements

The OVAL criteria comments are usually enough, but some criterion elements are bare references (e.g., `accounts_logon_fail_delay` has `<criterion test_ref="oval:ssg-test_accounts_logon_fail_delay:tst:1"/>` with no comment). Following the reference into the OVAL test/object/state tree would give the Worker the actual file paths and regex patterns the scanner uses.

Rejected for now: complexity ramp is significant, the criterion comments cover the majority of escalated rules from R10's analysis, and the value of resolving the residual ~3-4 rules where bare references are the issue doesn't justify the added code path. Defer until Run 11 data shows whether the bare-reference rules are still escalating.

### Hardcode reboot-required behavior into the harness

Rejected because it would couple the harness to STIG-specific knowledge. The Protocol + Skill design specifically isolates "what needs a reboot" inside the skill. CVE has its own reboot taxonomy (per-package-family); STIG has its own (FIPS vs kernel-cmdline). Other skills will have their own. The harness only knows the contract: skills declare deferrable failure modes and implement resolve_deferred.

### Skip reboot-required rules entirely

Rejected because the user-visible outcome is worse: the rule appears in `state.escalated` with no path to remediation. With the per-family reboot pattern, those rules move to remediated.

### Reboot once at end of run for all reboot-required rules together

Rejected for the same reason CVE picked per-family: failure isolation. If the FIPS family's reboot brings the VM up but breaks the mission app, we want to know that and revert FIPS, not roll back the entire run's worth of unrelated changes. Per-family snapshots preserve the gains from healthy families.

## Consequences

### Positive

- **DEF-29 closes the reboot-required gap structurally.** The 8-11 rules in `_REBOOT_REQUIRED_RULES` move from "escalates every run" to "remediated via end-of-run reboot." Projected fix-rate lift: +3-4 percentage points on top of R10's 89.6%.
- **DEF-28-deeper closes the scanner-gap edge cases.** Rules like `accounts_tmout` (TMOUT in *both* config locations), `mount_option_boot_nosuid` (both `/proc/mounts` AND `/etc/fstab`), `audit_rules_dac_modification_chown` (both augenrules and auditctl paths) now have the OVAL criteria explicit in the prompt. Projected lift: +3-5 percentage points.
- **Combined projection: ~94-96% fix rate for Run 11.** That gets the residual escalations into the single digits — the target the user named ("less than 10 after a run, I think that's a pretty good rate").
- **Skill plug-and-play preserved.** DEF-29 lives entirely in `skills/stig-rhel9/runtime.py` and reuses the existing harness Protocol slot. DEF-28-deeper extends a harness-level helper but the per-skill consumption stays in the skill's `worker_context`. CVE could adopt the same OVAL-style enrichment pattern with Vuls advisory criteria; future skills decide for themselves what their "scanner criteria" look like.

### Negative / accepted trade-offs

- **Two VM reboots per Run 11** (one for `fips`, one for `kernel-cmdline`). Each adds ~60-120s. Wall-clock impact: 2-4 minutes total. Acceptable on a 6-12h run.
- **Risk of reboot-induced VM corruption.** Mitigated by per-family snapshot rollback (same mitigation CVE proved out in journey/37). A bad reboot reverts the family and marks all family items with `family_<exception_type>` — the run continues, the items escalate, no orphaned state.
- **The reboot list is curated and maintained manually.** New STIG content updates could introduce new reboot-required rules. Detection signal for "we missed one": a rule consistently escalates with Reflector text naming reboot. We'd add it to the set.
- **OVAL criteria can be verbose for rules with deep criterion trees.** Rendered output is capped at 1500 chars (same as description). Most rules well under that; the few that aren't get truncated.

### Reversibility

- DEF-29: remove `"needs_reboot"` from `deferrable_failure_modes` and clear `_REBOOT_REQUIRED_RULES`. The Evaluator returns `EVALUATOR_GAP` again instead of `NEEDS_REBOOT`; the harness handles those rules with the normal retry-then-escalate loop. One-line revert.
- DEF-28-deeper: return `oval_criteria: ""` from `worker_context()`. The prompt section's `if oval:` guard makes that a no-op. The XCCDF extractor still parses the OVAL (cheap, harmless); just doesn't expose it.

## Bets going into Run 11

- **Fix rate**: 94-96% (vs R10's 89.6%). DEF-28-deeper closes the scanner-gap edge cases; DEF-29 closes the reboot-required cases.
- **Escalations**: 8-15 (vs R10's 26). Sub-10 is the ambitious target.
- **Audit category**: 95%+ maintained from R10; possibly higher with DEF-28-deeper sharpening the augenrules/auditctl distinction.
- **Cryptography category**: should jump from 25% (R7-R10) to 60-80% — most cryptography escalations are reboot-required.
- **Wall clock**: ~6.5-7h. R10 was 5.95h; DEF-29 adds 2-4 min for reboots, DEF-28-deeper adds ~100-300 tokens per Worker prompt (negligible).

## Run 11 result and the FIPS calibration amendment

Run 11 (journey/38.11) landed at 90.2% fix rate ex-skip — 0.6pp above R10's 89.6%, well below ADR's 94-96% projection. The miss traces to a single tuning constant: the FIPS family reboot's SSH-wait window was `range(24)` with 5s sleeps (~240s wall-clock), enough for a non-FIPS reboot but not for the first FIPS-mode boot from a non-FIPS baseline (dracut FIPS module reprobe). All 7 deferred FIPS items came back as `family_exception_runtimeerror`. If they had verified cleanly, fix rate would have landed at ~92.9%, still below the projected band but in-margin.

**Fix landed before Run 12** (commit shipping with this amendment): replace the count-based poll loop with a deadline-based wait and introduce `_FAMILY_REBOOT_WAIT_S` — a per-family timeout dict where `fips` gets 600s and other families stay at 180s. The loop exits as soon as SSH actually answers, so the change costs nothing on a fast reboot.

The architecture's behavior under the failure mode was otherwise correct: rules deferred, family batched, snapshot taken, timeout emitted as a named event with detail. The journey/38.10 bet "at least one DEF-29 family fails" hit (with medium confidence), but 38.10 underpriced its contagion onto the high-confidence bets.

## References

- [`adr/0020`](0020-skill-provided-worker-context.md) — the `worker_context` Protocol method this ADR extends.
- [`journey/36`](../journal/journey/36-per-family-reboot-batching.md) — CVE's per-family reboot design.
- [`journey/37`](../journal/journey/37-per-family-reboot-batching-landed.md) — CVE's per-family reboot implementation that DEF-29 mirrors.
- [`journey/38.9`](../journal/journey/38.9-def-28-in-production.md) — R10 result that motivated DEF-29 + DEF-28-deeper.
- [`deferred.md`](../deferred.md) DEF-29, DEF-28-deeper (now both shipping; promotion-to-journey when Run 11 lands).
- SCAP datastream on the target VM: `/usr/share/xml/scap/ssg/content/ssg-rl9-ds.xml` (XCCDF descriptions + embedded OVAL definitions).
