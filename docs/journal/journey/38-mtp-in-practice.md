---
id: journey-38-mtp-in-practice
type: journey
title: "MTP Bought 6.9 Hours and Cost 8 Rules"
date: 2026-05-21
tags: [L3-model, mtp, speculative-decoding, postmortem, run-results]
related:
  - journey/12-bf16-tp4-full-precision
  - journey/34-run-6-ordering-works-runtime-doesnt
  - adr/0018-vllm-mtp-cutover
one_line: "Run 7 finished in 12.2 hours — 6.9 hours faster than Run 6 — by switching vLLM to 0.21.0 with Gemma 4 MTP speculative decoding. It also fixed 145 rules to Run 6's 153, and the cryptography category collapsed from 83% to 29% on the same starting state. The wall-clock win was bigger than the synthetic-benchmark math predicted (1.57× vs the 1.8× the per-role tok/s implied); the regression is the open question."
---

# MTP Bought 6.9 Hours and Cost 8 Rules

Run 7 finished in 12.20 hours. Run 6 took 19.08 hours on the same starting snapshot, the same skill, the same harness, the same model. The only change was vLLM 0.19.0 → 0.21.0 plus Gemma 4 MTP speculative decoding using `google/gemma-4-31B-it-assistant` as the drafter, `num_speculative_tokens=2`. The wall-clock saving was 6.88 hours.

It also fixed 8 fewer rules.

## The numbers that arrived as predicted

The synthetic benchmark before launch read 41 tok/s vs 15 tok/s baseline — a 2.74× speedup on a prose-completion prompt at 99% MTP acceptance. The number was always going to come down on real workloads. The question was how much.

Final per-role medians across 2,399 LLM calls, captured from `data.timing.tok_per_sec` in the run JSONL:

| Agent     | Calls | Median tok/s | p10 | p90 | Median TTFT |
|-----------|------:|-------------:|----:|----:|------------:|
| Architect |   723 |         28.0 | 25.6 | 30.0 | 10.34 s |
| Reflector |   827 |         29.0 | 27.7 | 30.1 | 15.09 s |
| Worker    |   849 |         25.9 | 20.6 | 29.1 | 10.27 s |

Median ≈ 27.5 tok/s against the 15 tok/s baseline. **1.83× real-workload throughput, holding steady across all 12 hours of the run.** Hour-by-hour the median moved between 27.7 and 28.8 — no warmup, no decay. MTP delivered exactly what the benchmark walked back to once it left the easy-prose regime.

Aggregate MTP acceptance off vLLM's `/metrics` endpoint at run-end:

```
drafts          = 356,695
draft_tokens    = 713,390
accepted_tokens = 520,906
acceptance      = 73.0%
tok/step        = 2.46
```

The drafter is good at Ralph's prompts, even though Ralph's prompts are mostly STIG XML, bash scripts, and tool-call arguments rather than the prose the synthetic benchmark used. Two thirds of speculated tokens were verified. The "tok/step" metric is the multiplier on what each forward pass produces — going from "1 token per forward" to "2.46 tokens per forward" *is* the speedup, materially.

## The Worker's wide distribution is real and predicted

Worker tok/s p10 (20.6) vs p90 (29.1) is the widest distribution of the three roles. Architect and Reflector both have p90-p10 spread under 5 tok/s; Worker's spread is 8.5. The Worker writes bash scripts; the drafter speculates poorly on long shell pipelines where the next token genuinely could be `-l`, `-a`, `-h`, `-r`, or a path. The benchmark's prose stays near 99% acceptance; the Worker drops into the 60s on the script-heavy tail.

The architectural read is that **MTP's win is shape-dependent within a single skill**, and the harness's role distribution determines the aggregate. A skill that was 80% Architect-style discursive prompts would see closer to the synthetic 2.7× win; Ralph's 35/40/25 mix of Architect/Worker/Reflector lands at 1.83×.

## The number that didn't arrive: fix rate

Run 6 remediated 153 of 247 rules processed (61.9%). Run 7 remediated 145 of 249 processed (58.2%). The same starting snapshot. The same memory pool (Postgres `stig.tips` carried forward, 3,150 active tips at run start). The same skill manifest. The same OpenSCAP profile.

Of the 270 rules with terminal outcomes in both runs:

| Run 6 outcome | Run 7 outcome | Count |
|---|---|---|
| remediated | remediated | 136 |
| escalated  | escalated  | 87 |
| skip       | skip       | 18 |
| remediated | escalated  | **14** |
| escalated  | remediated | 7 |
| remediated | skip       | 3 |
| skip       | escalated  | 3 |
| skip       | remediated | 2 |

Net of 17 regressions against 9 gains: −8 rules. The number aligns with the headline delta exactly.

The attempts-per-success comparison on the 136 rules remediated in both runs:

```
Run 6: mean=1.17 attempts  median=1.0
Run 7: mean=1.29 attempts  median=1.0
Faster in Run 7: 5    Same: 120    Slower: 11
```

Run 7 used 0.12 more attempts per win on average — small, within plausible noise, but the direction is *worse*, not better. Memory should be amortizing toward fewer attempts as the tip corpus matures. This run didn't show that amortization. If anything, it showed mild drift the other way.

## The cryptography category collapsed

Per-category fix rate side-by-side:

| Category              | Run 6      | Run 7      | Δ      |
|-----------------------|-----------:|-----------:|-------:|
| kernel                | 37/38 (97%) | 38/38 (100%) | +3pp |
| authentication        | 21/22 (95%) | 22/23 (96%)  | +1pp |
| service-config        | 6/6 (100%)  | 6/6 (100%)   | flat |
| package-management    | 12/14 (86%) | 12/14 (86%)  | flat |
| filesystem            | 6/7 (86%)   | 6/8 (75%)    | −11pp |
| logging               | 9/11 (82%)  | 7/11 (64%)   | −18pp |
| ssh                   | 13/18 (72%) | 12/18 (67%)  | −5pp |
| privileged-access     | 2/4 (50%)   | 3/4 (75%)    | +25pp |
| other                 | 8/22 (36%)  | 9/21 (43%)   | +7pp |
| audit                 | 29/84 (35%) | 23/84 (27%)  | −8pp |
| integrity-monitoring  | 2/6 (33%)   | 2/6 (33%)    | flat |
| user-account          | 3/8 (38%)   | 3/8 (38%)    | flat |
| **cryptography**      | **5/6 (83%)** | **2/7 (29%)** | **−54pp** |
| banner                | 0/1 (0%)    | 0/1 (0%)     | flat |

Cryptography is the outlier. The drop isn't marginal; it's catastrophic on a small category. Pulling the rule-level detail:

| Rule | Run 6 | Run 7 |
|---|---|---|
| `harden_sshd_macs_openssh_conf_crypto_policy` | remediated | **escalated** |
| `fips_crypto_subpolicy` | remediated | absent (not processed) |
| `harden_sshd_macs_opensshserver_conf_crypto_policy` | remediated | absent |
| `harden_sshd_ciphers_openssh_conf_crypto_policy` | absent | escalated |
| `fips_custom_stig_sub_policy` | absent | escalated |
| `sysctl_crypto_fips_enabled` | absent | escalated |
| `configure_crypto_policy` | escalated | escalated |
| `package_gnutls-utils_installed` | remediated | remediated |
| `package_rsyslog-gnutls_installed` | remediated | remediated |

Two patterns: rules the Run 6 baseline encountered and fixed that this run either didn't see or escalated on, and a fresh batch of FIPS subpolicy rules that *only appeared in Run 7's failing set* and all escalated. The baseline snapshot's OpenSCAP scan returned a different set of failing rules between the two runs — same snapshot, same evaluator, different verdict. That's its own surprise and deserves its own investigation.

## Two hypotheses, one of them testable next

The fix-rate regression has two plausible causes:

1. **Drafter randomness.** With speculative decoding, the model sees a slightly different effective token distribution as it accepts or rejects drafter predictions. The verifier still validates each token, but a token sequence that *would* have been chosen under greedy sampling can lose to one the drafter proposed at the same probability. On hard prompts where the model is near a decision boundary, this is a real source of variance. Cryptography rules involve subtle config-file edits with cryptic option names — exactly the prompt shape where being off by one token can change the outcome.
2. **VM-state divergence.** The baseline snapshot is supposed to be identical. The OpenSCAP scan returning a different failing-rule set between Run 6 and Run 7 says it wasn't. Libvirt snapshot determinism isn't a property libvirt actually guarantees — the snapshot is filesystem-level, but `dnf`, `systemd`, and the audit subsystem all hold timestamps and state that diverge subtly.

The first hypothesis would predict that re-running the same MTP-enabled stack would produce a *different* set of regressions. The second would predict the same regressions. Run 8 — landing observability work and DEF-26's graded `outcome_value`, but not changing MTP, the drafter, or the snapshot — will tell us which.

!!! quote ""
    The wall-clock saving is real and visible at every layer. The fix-rate regression is real and concentrated in one category. Both are facts about the same run.

## What we already know we'll do differently

DEF-23 (per-call `mtp` in JSONL) and DEF-25 (MTP tile on the dashboard) ship in Run 8's pre-flight, so the next run captures per-role acceptance natively and a demo audience can see it live. The cryptography rules go on a watch list — if Run 8 reproduces the same regression on the same rules, we know the snapshot is the variable; if Run 8 regresses on different rules, MTP's drafter-randomness story has its smoking gun.

The synthetic benchmark walked from 41 tok/s to a real-workload 27.5 tok/s. The wall clock walked from a predicted 9-12 hour band to an actual 12.2. The fix rate walked from "should be the same or better with MTP" to 8 rules worse. Two of the three numbers landed where the math said. The third didn't, and the journal is the place it gets named rather than smoothed.

---

## Related

- [`journey/12`](12-bf16-tp4-full-precision.md) — the bf16 TP=4 baseline that Run 6 ran on and Run 7 inherits.
- [`journey/34`](34-run-6-ordering-works-runtime-doesnt.md) — Run 6's 19.1h result. The benchmark Run 7 measured against.
- [`adr/0018`](../../adr/0018-vllm-mtp-cutover.md) — the decision record for the vLLM 0.21.0 + MTP cutover this run ran on.
- [`gotchas/mtp-streaming-usage.md`](../gotchas/mtp-streaming-usage.md) — the streaming + usage caveat we documented mid-cutover.
- Next: `journey/38.5` after Run 8 lands, testing the two hypotheses above.
