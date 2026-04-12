---
id: improvement-07-rule-dependency-awareness
type: improvement
title: "Improvement #7: Rule Dependency Awareness"
date: 2026-04-12
tags: [L4-orchestration, reflexion-loop]
related:
  - journey/18-second-overnight-run
  - architecture/01-reflexive-agent-harness-failure-modes
---

# Improvement #7: Rule Dependency Awareness

## Problem

The architect selects rules independently. When multiple rules share a
prerequisite (e.g., all AIDE rules depend on a working AIDE database),
the architect attempts each one separately, and each independently
discovers and fails on the same prerequisite.

In the second overnight run, 5 AIDE rules each spent ~1000s grinding
on database initialization failures. Total: 83 minutes on a problem
that should have been solved once.

## Why this matters beyond STIG

Any skill with prerequisite chains will hit this pattern. Examples:

- **Certificate rotation**: renewing a CA cert must happen before
  renewing leaf certs that chain to it.
- **Kubernetes hardening**: namespace-level policies must exist before
  pod-level constraints that reference them.
- **Database compliance**: schema migrations must land before row-level
  audit triggers that reference new columns.

This is a harness concern, not a domain concern.

## Proposed mechanism

**Post-mortem clustering.** After each rule escalation or
multi-attempt failure, scan the post-mortem reasons for shared root
causes. If 2+ rules mention the same failing prerequisite (same
service, same config file, same error message), group them and
present the group to the architect with a recommendation:

> "Rules aide_verify_acls, aide_check_audit_tools, and
> aide_scan_notification all failed because AIDE database
> initialization fails. Recommend attempting aide_build_database first."

The architect then chooses to:
- **Accept**: re-order the queue to attempt the prerequisite first.
- **Skip group**: escalate the entire group as a dependency chain
  failure (one escalation reason, not five separate ones).

### Implementation sketch

1. After each `rule_complete` with outcome `escalated`, extract
   key phrases from the post-mortem reason (service names, file
   paths, error signatures).
2. Maintain a `prerequisite_clusters` map in the harness state.
3. When a new rule's post-mortem matches an existing cluster,
   add it to the cluster.
4. When the architect selects the next rule, check if any cluster
   has a suggested prerequisite. If so, surface it in the
   architect's context.

This is deliberately lightweight — no upfront dependency graph, no
domain-specific knowledge. The harness discovers dependencies
empirically from its own failures.

## What this is not

Not a full dependency resolver. Not a static analysis of STIG rule
relationships. The harness should discover dependencies at runtime
from failure patterns, not require them to be declared upfront. This
keeps the mechanism skill-agnostic.

## Verification

- Create 3 test rules that share a prerequisite (rule B and C depend
  on rule A being remediated first).
- Run them without dependency awareness: confirm all 3 attempt
  independently and B/C fail on the prerequisite.
- Run with dependency awareness: confirm the harness surfaces the
  cluster to the architect and A is attempted before B and C.
