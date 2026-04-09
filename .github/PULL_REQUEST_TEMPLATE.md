<!-- Thanks for contributing to GemmaForge! -->

## Summary

<!-- What does this PR change and why? Lead with the why. -->

## Type of change

- [ ] Bug fix
- [ ] New feature / new skill
- [ ] Refactor (no behavior change)
- [ ] Documentation / ADR
- [ ] CI / supply chain
- [ ] Other

## Architectural impact

<!--
If this PR introduces a non-obvious technical decision, link or add the ADR
under docs/adr/. Federal evaluators read ADRs first — every meaningful
choice should be captured there with rationale, alternatives, and
consequences. See docs/adr/template.md.
-->

- Related ADR(s):

## How was this tested?

<!--
- Unit / integration tests added or updated?
- End-to-end Ralph loop run? Paste the trace ID from Jaeger / Langfuse.
- For VM-touching changes: which Rocky 9 baseline snapshot did you start from?
-->

## Checklist

- [ ] Code is formatted (`ruff format`) and lints clean (`ruff check`)
- [ ] Type checks pass (`mypy gemma_forge`)
- [ ] Tests pass (`pytest`)
- [ ] `docker compose -f docker-compose.yml config` validates
- [ ] No host-specific absolute paths committed (use `${VM_ROOT}` etc.)
- [ ] No secrets, keys, qcow2 disks, or model weights committed
- [ ] ADR added or updated if a non-obvious decision was made
- [ ] README or docs updated if user-facing behavior changed
