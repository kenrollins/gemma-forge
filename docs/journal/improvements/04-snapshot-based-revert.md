---
id: improvement-04-snapshot-based-revert
type: improvement
title: "Improvement: Snapshot-Based Revert with Diagnostic Capture"
date: 2026-04-11
tags: [L4-orchestration, snapshot-revert, refactor]
related:
  - journey/14-overnight-run-findings
  - architecture/01-reflexive-agent-harness-failure-modes
one_line: "Hypervisor-level snapshot-based revert with pre-revert diagnostic capture, replacing Worker-written revert scripts that could not recover from environment corruption."
---

# Improvement: Snapshot-Based Revert with Diagnostic Capture

**Status:** Implemented 2026-04-11 as part of the Fix #5 pass
**Related:** `journey/14-overnight-run-findings.md` Finding 3 (sudo deadlock)
**Files changed:** `gemma_forge/harness/tools/ssh.py`, `gemma_forge/harness/ralph.py`

## The problem this solves

The original revert mechanism was script-based: the Worker generated both a
`fix_script` and a `revert_script`, and the harness ran the revert script via
SSH+sudo when the fix failed its evaluation. This had three fatal assumptions:

1. **The revert script is correct.** The Worker sometimes produces reverts
   that don't cover the same filesystem state the fix modified, or that have
   bash syntax errors, or that silently no-op on state that was partially
   applied.
2. **Sudo still works after the fix.** The overnight run hit a rule
   (`sudo_remove_nopasswd`) whose fix ran `sed -i 's/NOPASSWD/...' /etc/sudoers`.
   That fix passed the STIG rule check but broke passwordless sudo. The revert
   script needed sudo to restore sudoers. Sudo was broken. Revert failed
   silently. From that point on, every subsequent attempt for the next ~7
   hours saw sudo broken and reported "privilege escalation deadlock" in 42
   reflections before the run was stopped.
3. **The filesystem is still writable.** A fix can break disk space,
   readonly-remount `/etc`, remove `/bin/bash`, or corrupt any number of
   preconditions the revert script implicitly depends on.

None of these assumptions hold under adversarial conditions, and "an agent
trying to remediate STIG rules" is exactly adversarial for the revert
mechanism.

## The right answer: VM snapshots

libvirt snapshots are authoritative. A restored snapshot is a pristine
qcow2 COW state that cannot be corrupted by anything the guest does. Restore
is fast (~2–5 seconds on internal snapshots). libvirt is rock-solid at this.

But we don't want to lose accumulated progress. If rule 1 and rule 2 are
successfully remediated and rule 3 fails, rolling back to `baseline` would
lose rules 1 and 2. The fix is a two-snapshot scheme:

- **`baseline`** — VM initial state, never modified after first creation.
  The forever fallback when nothing else exists.
- **`progress`** — rolling snapshot, advanced after each successful
  remediation. Contains all accumulated successful fixes. Replaced (delete +
  create) after each win so the chain stays short.

On failure of an attempt, restore to `progress` if it exists, else
`baseline`. Successful attempts preserve their work by advancing `progress`.

## Diagnostics before revert — the learning layer

When an attempt fails, we could just restore the snapshot and move on. But
that throws away the one thing that matters: **why did it fail?** Without
forensics, the Reflector only sees "APPLY_FAILED" and can't reason about
root causes.

Before the snapshot restore, we run `gather_environment_diagnostics()` which
captures a structured post-mortem via SSH (or virsh console fallback):

| Section | What it tells us |
|---|---|
| `SUDO_PROBE` | Did the fix break non-tty sudo? (`sudo -n whoami`) |
| `SERVICE_STATUS` | Which mission-critical services are still running? |
| `MISSION_HEALTHCHECK` | Does the mission app's own healthcheck pass? |
| `RECENT_AUTH_FAILURES` | What's in journalctl for PAM/sudo/auth errors? |
| `SUDOERS_STATE` | Has `/etc/sudoers` or `/etc/sudoers.d/` been modified? |
| `PAM_STATE` | Has `/etc/pam.d/sudo` been rewritten? |
| `FS_STATE` | Is the filesystem still mounted rw? Can we write to /tmp? |
| `NETWORK_STATE` | What's listening? Did the fix break network? |
| `RECENT_JOURNAL_ERRORS` | Top journal errors in the last 5 minutes |

This gets serialized into a structured `post_mortem` event in the run log,
with boolean flags (`sudo_ok`, `services_ok`, `mission_healthy`) for quick
decisioning and the full parsed sections for the Reflector's context. The
Reflector now receives real facts about *why* things broke, not just the
fact that they broke.

## The revert sequence (new)

```python
# 1. Gather forensics (runs via SSH, falls back to virsh console if broken)
diagnostics = await gather_environment_diagnostics(ssh_config)

# 2. Emit structured post_mortem event for the log + Reflector
run_log.log("post_mortem", "harness", {
    "rule_id": ..., "category": ..., "attempt": ...,
    "sudo_ok": diagnostics["sudo_ok"],
    "services_ok": diagnostics["services_ok"],
    "mission_healthy": diagnostics["mission_healthy"],
    "sudo_probe": diagnostics["sudo_probe"],
    "service_status": diagnostics["service_status"],
    "recent_auth_failures": diagnostics["recent_auth_failures"],
    ...
})

# 3. Snapshot-restore to the last known-good state
ok, detail = await snapshot_restore_progress()  # prefers `progress`, falls back to `baseline`

# 4. Verify restore actually worked (lightweight sudo probe)
post_ok, _ = await check_sudo_healthy(ssh_config)
if not post_ok:
    run_log.log("environment_unrecoverable", ...)  # libvirt or snapshot chain is wrong
```

And on successful remediation:

```python
# Advance progress snapshot so future failures revert to this state
snap_ok, snap_detail = await snapshot_save_progress()
```

## Why this is also going to help with other problems

Ken's instinct — "it will be better in the long run and it will help with
other problems" — is exactly right. The snapshot-based revert also buys us:

1. **Resilience to Worker bugs.** A Worker that produces an incorrect
   revert script no longer corrupts future attempts.
2. **Resilience to unanticipated fix side effects.** A fix that modifies
   state the Worker didn't think about (log files, systemd runtime state,
   kernel parameters) is fully rolled back by the snapshot, not by
   whatever subset the revert_script happened to target.
3. **Honest attempt counting.** Previously a "successful revert" could
   leave the VM in a subtly different state than before the fix, which
   meant attempt N+1 on the same rule didn't start from a true
   known-good baseline. Now every attempt starts from a guaranteed
   known-good state.
4. **A path for skill authors to stop writing revert scripts.** Future
   skills don't need to produce `revert_script` at all — the snapshot is
   the revert. The `revert_script` field stays as informational metadata
   (the Worker says what it *thought* it was undoing), useful for post-run
   analysis but no longer load-bearing for correctness.
5. **Forensic-quality post-mortems.** The `post_mortem` event is a rich,
   structured record of exactly what broke. Future iterations of the
   dashboard can surface these as "failure cards" — the Federal audience
   will love seeing "here's the failure, here's exactly what broke, here's
   the rollback proof" instead of "rule X failed, we tried again."
6. **Decouples Reflector quality from Worker quality.** The Reflector can
   reason about real environment facts (from `post_mortem`) rather than
   the Worker's self-reported output. This is a more robust learning loop.

## What stayed

- The `ssh_apply` function still accepts `revert_script` for compatibility
  and logs it as metadata. Future cleanup: the skill manifest can make this
  field optional.
- The virsh-console fallback in `_run_ssh` is still there and is actually
  more important now — when SSH is broken, the diagnostic gather uses
  console to grab the post-mortem before we restore.
- The `baseline` snapshot creation and management is unchanged; it's still
  a one-time setup via `vm-snapshot.sh create baseline`.

## What the run start now does

`run_ralph()` does a preflight snapshot check before any scan or LLM call:

1. Verify `baseline` exists; hard-fail if not (with instructions to create it)
2. Delete any stale `progress` snapshot from a prior run (non-fatal if absent)
3. Emit a `snapshot_preflight` event

This ensures every run starts with a clean revert target and catches libvirt
issues early (before the LLM burns any tokens).

## Tests

Both paths were verified end-to-end against the real VM:

**Diagnostic capture against healthy baseline:**
```
sudo_ok: True
services_ok: True
mission_healthy: True
service_status:
  nginx: active
  postgresql: active
  sshd: active
  chronyd: active
mission_healthcheck: HEALTHY: nginx=ok postgres=ok sshd=ok
```

**Snapshot save/restore cycle:**
```
1. Create progress snapshot from current VM state → ok
2. Progress exists? True
3. Restore from progress → ok, restored=progress
4. Delete progress → ok
5. Progress exists after cleanup? False
```

Both paths complete in under 5 seconds against the current libvirt setup.

## Known limitations

- **Snapshot create time** will add ~2s per successful remediation. At 100
  remediations per run, that's 200s overhead — negligible against 10+ hours
  of LLM grinding.
- **Snapshot restore time** is ~2–5s for internal qcow2 snapshots. A run that
  has 50 failed attempts would add 100–250s of restore overhead, still
  negligible.
- **libvirt internal snapshots accumulate qcow2 metadata.** The two-snapshot
  scheme (baseline + single rolling progress) keeps this bounded. Do not
  use this pattern with unbounded snapshot chains.
- **The snapshot is VM-wide**, so if the fix modifies state outside /etc
  (e.g., /var/lib/postgresql), the revert rolls that back too. This is
  actually what we want for correctness.
- **Progress snapshot is host-local.** Moving the workload to a different
  libvirt host would lose it. For the demo, this is fine.
