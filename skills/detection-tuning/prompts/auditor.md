# Auditor — detection-tuning skill

TODO (Saturday morning): real prompt.

The detection-tuning skill has no separate mission-app health to
guard. The evaluator is deterministic and surfaces P/R/F1 directly.
Use `check_eval_health` to confirm the eval pipeline is reachable;
that's the entirety of the Auditor's responsibility here.
