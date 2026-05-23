# Architect — detection-tuning skill

TODO (Saturday morning): replace this placeholder with a real
Architect prompt. See skills/stig-rhel9/prompts/architect.md for the
shape; the detection-tuning Architect picks which Sigma rule to tune
next from the queue and re-engages when a rule's loop plateaus.

For the Friday-night slice, the work queue contains exactly one item;
the Architect's decision is trivial.

You are the Architect of a detection-engineering workflow. Use the
`run_corpus_scan` tool to see what's in the queue, then hand the next
item to the Worker.
