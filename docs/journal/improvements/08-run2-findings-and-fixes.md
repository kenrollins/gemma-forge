# Run 2 Proposed Fixes — For Discussion

These are organized by where they belong. None are committed yet.

## Skill-level fixes (skills/stig-rhel9/)

### 1. Architect prompt: audit_rules_immutable ordering
Add to architect.md within the audit category guidance:
"Process `audit_rules_immutable` LAST within the audit category.
This rule sets `-e 2` which locks the kernel audit system until
reboot. All other audit rules must be applied before this one."

**Impact:** Potentially 20-35 additional remediations in the audit
category. The highest-leverage single change available.

### 2. Worker prompt: three additions
- Audit immutability check: `auditctl -s | grep -q 'enabled 2'`
  before attempting audit rule changes
- Tool output control: use `--quiet` or pipe through `head -50`
  for package installs to avoid context overflow
- Identity-aware sudoers: "Run `whoami` inside the fix_script to
  identify the harness agent's username before modifying sudoers"

### 3. Fix _categorize_rule ordering in runtime.py
`"sudo" in rid` matches before `"audit" in rid`, misclassifying
`audit_rules_privileged_commands_sudo*` as `privileged-access`.
Move the audit check above the sudo check.

### 4. Pre-skip list in skill.yaml
21 rules that always skip with identical reasoning:
- `partition_for_*` (7 rules) — impossible on running system
- `grub2_*` (6 rules) — requires reboot
- `enable_fips_mode`, `fips_crypto_subpolicy`,
  `sysctl_crypto_fips_enabled` — high risk, breaks SSH
- `harden_sshd_ciphers_*`, `harden_sshd_macs_*` — needs FIPS

Saves ~10-15 minutes of architect time and ~30K tokens.

### 5. Remove top-15 truncation in run_stig_scan
The scan currently shows only 15 rules at first selection. The
Architect has to re-engage repeatedly to discover the full 270.
Show all failing rules (or at least categorized counts).

## Harness-level fixes (general, benefits any skill)

### 6. Tool result truncation
Cap `apply_fix` output before feeding back to the model. Two
context overflow errors (16K limit) from unconstrained `dnf install`
output. Add a max-bytes guard in the tool result handling path.

### 7. Lesson environment tagging (research needed)
50 SSH lessons at weight 1.0 say "RPM DB is broken" — true in
Run 1's environment, potentially misleading in Run 3. Lessons
currently have no concept of "this was learned in a specific
environment state." 

Options:
a. Tag lessons with a run environment hash
b. Decay all lesson weights on VM rebuild/reset
c. Add a "confidence" dimension separate from weight
d. Accept the noise — the model may waste a few attempts but
   the lessons will self-correct via weight decay

This needs discussion. It's a real architectural question about
memory fidelity vs. memory portability.

## Questions for Ken

1. The immutable ordering fix is skill-specific (architect.md).
   But should the harness also support declaring ordering
   constraints in the skill manifest? E.g., `ordering_rules:
   [{rule: "audit_rules_immutable", position: "last_in_category"}]`

2. The pre-skip list trades automation for speed. The system
   *can* learn to skip these (and did, in 10-28 iterations per
   skip). Is it better to let it discover the skips (harness
   learning) or hardcode them (skill efficiency)?

3. The tool result truncation — should this be in the harness
   (cap all tool outputs to N bytes) or in the skill prompts
   (tell the Worker to use --quiet)?

4. The lesson environment tagging — is this worth building now
   or is it a v6 concern?
