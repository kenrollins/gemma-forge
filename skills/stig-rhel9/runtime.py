# NOTE: Do NOT add `from __future__ import annotations` to this module.
# ADK's FunctionTool parser requires real type objects, not lazy strings.

"""STIG RHEL9 skill runtime — implements the five harness interfaces.

This module bridges the abstract harness interfaces to the concrete
STIG-specific tools (OpenSCAP, SSH, virsh snapshots, mission healthcheck).
The harness imports this at runtime when the stig-rhel9 skill is loaded.
"""

import logging

from gemma_forge.harness.interfaces import (
    Checkpoint,
    EvalResult,
    Evaluator,
    EvaluatorMetadata,
    Executor,
    FailureMode,
    OutcomeSignal,
    WorkItem,
    WorkQueue,
    outcome_signal_from_eval_result,
)
from gemma_forge.harness.tools.healthcheck import mission_healthcheck
from gemma_forge.harness.tools.journal import read_recent_journal
from gemma_forge.harness.tools.openscap import stig_check_rule, stig_scan
from gemma_forge.harness.tools.ssh import (
    SSHConfig,
    _run_snapshot_cmd,
    check_sudo_healthy,
    gather_environment_diagnostics,
    snapshot_exists,
    snapshot_restore_progress,
    snapshot_save_progress,
    ssh_apply,
)

logger = logging.getLogger(__name__)


# -- Module-level config (avoids closures that break ADK parsing) ----------

_ssh_config: SSHConfig | None = None
_stig_profile: str = ""
_stig_datastream: str = ""


async def run_stig_scan() -> str:
    """Scan the target VM for STIG compliance violations."""
    assert _ssh_config is not None
    full = await stig_scan(_ssh_config, _stig_profile, _stig_datastream)
    lines = full.split("\n")
    rules = [l for l in lines if l.startswith("- ")]
    header = lines[0] if lines else ""

    # Show category summary + all rules (not truncated) so the Architect
    # can make informed ordering decisions across the full rule set.
    from collections import Counter
    cats: Counter = Counter()
    for r in rules:
        parts = r[2:].split(": ", 1)
        if parts:
            cats[_categorize_rule(parts[0].strip())] += 1
    cat_summary = " | ".join(f"{cat}: {cnt}" for cat, cnt in cats.most_common())

    return (
        f"{header}\n\n"
        f"Total failing: {len(rules)} rules\n"
        f"By category: {cat_summary}\n\n"
        + "\n".join(rules)
    )


async def apply_fix(fix_script: str, revert_script: str, description: str) -> str:
    """Apply a STIG fix to the target VM via SSH.

    Args:
        fix_script: The bash commands to apply the fix.
        revert_script: The bash commands to undo the fix.
        description: One-line description of what this fix does.
    """
    assert _ssh_config is not None
    return await ssh_apply(_ssh_config, fix_script, revert_script, description)


# -- Interface implementations ------------------------------------------------


class StigWorkQueue:
    """Produces STIG work items from an OpenSCAP scan."""

    def __init__(self, ssh_config: SSHConfig, profile: str, datastream: str):
        self._ssh = ssh_config
        self._profile = profile
        self._datastream = datastream

    async def scan(self) -> list[WorkItem]:
        raw = await stig_scan(self._ssh, self._profile, self._datastream)
        items = []
        for line in raw.split("\n"):
            if line.startswith("- "):
                parts = line[2:].split(": ", 1)
                if len(parts) == 2:
                    rule_id = parts[0].strip()
                    title = parts[1].strip()
                    items.append(WorkItem(
                        id=rule_id,
                        title=title,
                        category=_categorize_rule(rule_id),
                    ))
        return items


class StigExecutor:
    """Applies fixes via SSH and exposes ADK tool functions."""

    def __init__(self, ssh_config: SSHConfig):
        self._ssh = ssh_config

    async def apply(self, item: WorkItem, fix_script: str,
                    revert_script: str, description: str) -> str:
        return await ssh_apply(self._ssh, fix_script, revert_script, description)

    def get_agent_tools(self) -> list:
        return [apply_fix]


class StigEvaluator:
    """Deterministic evaluation via OpenSCAP + health checks + journal."""

    metadata = EvaluatorMetadata(
        signal_type="binary",
        expected_confidence="high",
        cost_per_evaluation="cheap",
        # OpenSCAP is deterministic; we can act on per-(tip, rule) hits
        # after only a few retrievals at a low utility floor (Xu et al.
        # arxiv 2505.16067 §4.2).
        min_retrievals_before_eviction=3,
        eviction_threshold=0.3,
    )

    def __init__(self, ssh_config: SSHConfig, profile: str, datastream: str):
        self._ssh = ssh_config
        self._profile = profile
        self._datastream = datastream

    def signal_for(self, result: EvalResult) -> OutcomeSignal:
        """Binary signal: pass=1.0/fail=0.0, full confidence (OpenSCAP is deterministic)."""
        return outcome_signal_from_eval_result(result, confidence=1.0)

    async def evaluate(self, item: WorkItem) -> EvalResult:
        health = await mission_healthcheck(self._ssh)
        health_ok = "HEALTHY" in health

        rule_result = await stig_check_rule(
            self._ssh, item.id, self._profile, self._datastream)
        rule_ok = "PASS" in rule_result.upper()

        journal = await read_recent_journal(self._ssh)
        journal_clean = "JOURNAL_CLEAN" in journal or "no entries" in journal.lower()

        # Classify the failure mode for evaluation triage
        if health_ok and rule_ok:
            # Success — journal noise doesn't matter
            return EvalResult(
                passed=True,
                summary=f"health={health_ok} rule={rule_ok} journal={journal_clean}",
                signals={
                    "health": health, "health_ok": health_ok,
                    "rule_check": rule_result, "rule_ok": rule_ok,
                    "journal": journal[:300], "journal_clean": journal_clean,
                },
            )

        if not health_ok:
            mode = FailureMode.HEALTH_FAILURE
        elif health_ok and not rule_ok:
            mode = FailureMode.EVALUATOR_GAP
        else:
            mode = FailureMode.CLEAN_FAILURE

        return EvalResult(
            passed=False,
            failure_mode=mode,
            summary=f"health={health_ok} rule={rule_ok} journal={journal_clean}",
            signals={
                "health": health, "health_ok": health_ok,
                "rule_check": rule_result, "rule_ok": rule_ok,
                "journal": journal[:300], "journal_clean": journal_clean,
            },
        )


class StigCheckpoint:
    """VM-level checkpoint via libvirt snapshots."""

    async def exists(self, name: str) -> bool:
        return await snapshot_exists(name)

    async def save(self, name: str) -> tuple[bool, str]:
        if name == "progress":
            return await snapshot_save_progress()
        ok, detail = await _snap_create(name)
        return ok, detail

    async def restore(self, name: str) -> tuple[bool, str]:
        if name == "progress":
            return await snapshot_restore_progress()
        return await _run_snapshot_cmd("revert", name, timeout=60)

    async def delete(self, name: str) -> tuple[bool, str]:
        try:
            await _run_snapshot_cmd("delete", name, timeout=30)
            return True, f"deleted {name}"
        except Exception as exc:
            return False, str(exc)


async def _snap_create(name: str) -> tuple[bool, str]:
    """Create a named snapshot."""
    try:
        await _run_snapshot_cmd("create", name, timeout=60)
        return True, f"created {name}"
    except Exception as exc:
        return False, str(exc)


class StigSkillRuntime:
    """Bundles all STIG interfaces for the harness."""

    def __init__(self, ssh_config: SSHConfig, profile: str, datastream: str):
        global _ssh_config, _stig_profile, _stig_datastream
        _ssh_config = ssh_config
        _stig_profile = profile
        _stig_datastream = datastream

        self._work_queue = StigWorkQueue(ssh_config, profile, datastream)
        self._executor = StigExecutor(ssh_config)
        self._evaluator = StigEvaluator(ssh_config, profile, datastream)
        self._checkpoint = StigCheckpoint()
        self._ssh = ssh_config

    @property
    def work_queue(self) -> WorkQueue:
        return self._work_queue

    @property
    def executor(self) -> Executor:
        return self._executor

    @property
    def evaluator(self) -> Evaluator:
        return self._evaluator

    @property
    def checkpoint(self) -> Checkpoint:
        return self._checkpoint

    def get_scan_tool(self):
        return run_stig_scan

    async def check_sudo_healthy(self) -> tuple[bool, str]:
        """Skill-specific: verify SSH+sudo after restore."""
        return await check_sudo_healthy(self._ssh)

    async def gather_diagnostics(self) -> dict:
        """Skill-specific: capture environment state for post-mortem."""
        return await gather_environment_diagnostics(self._ssh)


def _categorize_rule(rule_id: str) -> str:
    """Classify a STIG rule into a coarse family."""
    rid = rule_id.lower()
    name = rid.split("content_rule_", 1)[-1]
    if "aide" in rid: return "integrity-monitoring"
    if any(k in rid for k in ("fips", "crypto", "hash", "cipher", "ssl", "tls")):
        return "cryptography"
    # Partition/mount rules check before "audit" substring: rules like
    # partition_for_var_log_audit are filesystem-shaped even though the
    # path contains "audit". Audit rules (audit_rules_*, auditd_*) start
    # with the audit prefix, never with partition_for_ / mount_option_,
    # so they still fall through to the audit branch below.
    if name.startswith("partition_for_") or name.startswith("mount_option_"):
        return "filesystem"
    if "audit" in rid: return "audit"
    if "sudo" in rid or "nopasswd" in rid: return "privileged-access"
    if "partition" in rid or "mount" in rid: return "filesystem"
    if "selinux" in rid: return "mac"
    if any(k in rid for k in ("kernel", "sysctl", "grub", "boot")): return "kernel"
    if any(k in rid for k in ("firewall", "firewalld", "iptables")): return "network-firewall"
    if "ssh" in rid: return "ssh"
    if any(k in rid for k in ("password", "pam", "faillock")): return "authentication"
    if any(k in rid for k in ("banner", "motd", "issue")): return "banner"
    if any(k in rid for k in ("package", "rpm", "dnf", "gpg")): return "package-management"
    if any(k in rid for k in ("log", "rsyslog", "journald")): return "logging"
    if "service" in rid or "systemd" in rid: return "service-config"
    if any(k in rid for k in ("user", "account", "umask")): return "user-account"
    return "other"
