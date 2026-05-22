---
title: Futures
---

# Futures — Things We Considered But Haven't Built

This section is for ideas that came up during the project, are worth
preserving, and would be valuable to someone — possibly future-Ken,
possibly someone else picking up gemma-forge — if and when there's
time, motivation, or external pressure to pursue them.

These are not journey entries. They didn't happen. They are not
improvements either, because they aren't fixes for things that
broke. They are *speculative engineering notes*: enough detail that
a competent engineer could pick one up and start work, but no
implementation, no commitments, no schedule.

If a futures entry ever does get implemented, it should be promoted
to a journey entry that documents the actual build. The original
futures entry stays as the record of when the idea first surfaced
and what the thinking was at that point.

## Entries

- [**The Harness Is a Training Data Factory**](harness-as-training-data-factory.md) — the open-weights case study nobody is running yet. The accumulated structured (context, action, outcome) data from agentic harness runs is, by construction, a fine-tuning corpus. With open weights you can close the loop locally; with closed weights you cannot.
- [**Detection Engineering as a Third Skill**](detection-tuning-skill.md) — proposed weekend-project skill for tuning Sigma rules against labeled threat-telemetry corpora (DARPA OpTC, BOTS, MITRE Evaluations). Surfaced while looking for additional use cases that exercise different problem shapes than the two existing skills, captured with honest unknowns. Statistically-graded evaluator instead of binary pass/fail; corpus is per-run config so the tip pool accumulates corpus-independent Sigma authoring patterns. Tests the architectural claim that "skills are pluggable" against a structurally different problem shape than STIG/CVE.
