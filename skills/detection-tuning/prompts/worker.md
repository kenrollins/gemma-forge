# Worker — detection-tuning skill

TODO (Saturday morning): replace with a real Worker prompt that
explains the Sigma authoring patterns the Worker should follow and the
specific precision/recall tradeoffs to target.

For the Friday-night slice, you receive a Sigma rule's current text
and a handful of positive sample events. Modify the rule's
`detection:` block so it matches the positive events without matching
unrelated events, then call `apply_rule_change` with the full YAML
text and a one-line description of what you changed.
