# NOTE: Do NOT add `from __future__ import annotations` to this module.
# ADK's FunctionTool parser requires real type objects, not lazy strings.

"""STIG RHEL9 skill runtime — implements the five harness interfaces.

This module bridges the abstract harness interfaces to the concrete
STIG-specific tools (OpenSCAP, SSH, virsh snapshots, mission healthcheck).
The harness imports this at runtime when the stig-rhel9 skill is loaded.
"""

import contextlib
import logging

from gemma_forge.harness.interfaces import (
    Checkpoint,
    DeferredItemOutcome,
    EmitEvent,
    EvalResult,
    Evaluator,
    EvaluatorMetadata,
    Executor,
    FailureMode,
    OutcomeSignal,
    WorkItem,
    WorkQueue,
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
    rules = [line for line in lines if line.startswith("- ")]
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
        f"By category: {cat_summary}\n\n" + "\n".join(rules)
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
                    items.append(
                        WorkItem(
                            id=rule_id,
                            title=title,
                            category=_categorize_rule(rule_id),
                        )
                    )
        return items


class StigExecutor:
    """Applies fixes via SSH and exposes ADK tool functions."""

    def __init__(self, ssh_config: SSHConfig):
        self._ssh = ssh_config

    async def apply(
        self, item: WorkItem, fix_script: str, revert_script: str, description: str
    ) -> str:
        return await ssh_apply(self._ssh, fix_script, revert_script, description)

    def get_agent_tools(self) -> list:
        return [apply_fix]


_REBOOT_REQUIRED_RULES: set[str] = {
    # DEF-29 — STIG rules whose remediation requires a kernel reboot to
    # take effect. Detected empirically across Runs 7-10: in every run,
    # these rules' Reflector text named "kernel reboot required" or
    # "kernel parameter immutable at runtime." See journey/38.9's
    # "What's still escalating" section for the per-rule breakdown.
    #
    # Curated rather than pattern-matched because the "needs reboot"
    # signal isn't reliably inferable from the rule id alone. Easier
    # to maintain a known list than a brittle regex.
    "xccdf_org.ssgproject.content_rule_configure_crypto_policy",
    "xccdf_org.ssgproject.content_rule_fips_crypto_subpolicy",
    "xccdf_org.ssgproject.content_rule_fips_custom_stig_sub_policy",
    "xccdf_org.ssgproject.content_rule_harden_sshd_ciphers_openssh_conf_crypto_policy",
    "xccdf_org.ssgproject.content_rule_harden_sshd_ciphers_opensshserver_conf_crypto_policy",
    "xccdf_org.ssgproject.content_rule_harden_sshd_macs_openssh_conf_crypto_policy",
    "xccdf_org.ssgproject.content_rule_harden_sshd_macs_opensshserver_conf_crypto_policy",
    "xccdf_org.ssgproject.content_rule_sysctl_crypto_fips_enabled",
    "xccdf_org.ssgproject.content_rule_aide_use_fips_hashes",
    "xccdf_org.ssgproject.content_rule_grub2_audit_argument",
    "xccdf_org.ssgproject.content_rule_enable_fips_mode",
}


# DEF-29 calibration (journey/38.11): per-family max-wait for SSH to
# return after a reboot. The FIPS family on a cold (non-FIPS → FIPS)
# transition runs dracut's FIPS-module probe on first boot, which is
# materially slower than a normal reboot. Run 11's 24×5s window was
# enough for a non-FIPS reboot but not the first FIPS one. 600s gives
# generous headroom; the loop exits as soon as SSH actually answers.
_FAMILY_REBOOT_WAIT_S: dict[str, int] = {
    "fips": 600,
    "kernel-cmdline": 180,
    "other-reboot": 180,
}


def _stig_reboot_family(rule_id: str) -> str:
    """Group reboot-required rules into families for per-family processing.

    The per-family pattern from CVE (journey/36, /37) is intended to
    isolate failure: if FIPS-related items succeed after reboot but
    kernel-cmdline items fail, we want to keep the FIPS gains and
    only roll back kernel-cmdline. For STIG specifically the FIPS
    items dominate; kernel-cmdline is a 1-item family today.
    """
    rid = rule_id.lower()
    if "grub2" in rid:
        return "kernel-cmdline"
    if any(k in rid for k in ("fips", "crypto_policy")):
        return "fips"
    if "aide_use_fips" in rid:
        return "fips"  # depends on FIPS being active
    return "other-reboot"


class StigEvaluator:
    """Deterministic evaluation via OpenSCAP + health checks + journal."""

    metadata = EvaluatorMetadata(
        signal_type="binary",
        expected_confidence="high",
        cost_per_evaluation="cheap",
        min_retrievals_before_eviction=3,
        eviction_threshold=0.3,
        # DEF-29: STIG opts into the per-family reboot pattern from CVE.
        # When a reboot-required rule's apply succeeds but the rule check
        # still fails because the kernel hasn't picked up the change, the
        # Evaluator returns NEEDS_REBOOT and the harness defers the item;
        # StigSkillRuntime.resolve_deferred batches the deferred items by
        # reboot family, snapshots, reboots, and re-evaluates.
        deferrable_failure_modes=["needs_reboot"],
    )

    def __init__(self, ssh_config: SSHConfig, profile: str, datastream: str):
        self._ssh = ssh_config
        self._profile = profile
        self._datastream = datastream

    def signal_for(self, result: EvalResult, *, attempt_number: int = 1) -> OutcomeSignal:
        """Graded outcome from a binary evaluator, using attempt_number + failure_mode.

        OpenSCAP is binary (PASS/FAIL), but the harness has deterministic
        side-signals — attempt count, failure-mode classification — that
        let us project richer outcome quality without an LLM judge. See
        journey/38.5 for the cryptography case that motivated this and
        ADR-0019 for the scoring rationale.

        Scoring function (deterministic, tunable):
          passed AND attempt 1            -> 1.0   (clean first-try win)
          passed AND attempt 2-3          -> 0.8   (solid win, minor course-correct)
          passed AND attempt 4+           -> 0.5   (helped slowly, lots of churn)
          EVALUATOR_GAP (clean fail)      -> 0.0   (tip didn't help, tip didn't break)
          CLEAN_FAILURE                   -> 0.0   (same shape)
          HEALTH_FAILURE                  -> -0.2  (tip's advice broke the system)

        Note: per DEF-27, the per-retrieval row also gets a tip_followed
        signal from the dream pass. The value here is "did this attempt
        succeed and how cleanly"; whether *this specific tip* deserves
        credit for that success is the dream pass's job to attribute via
        tip_followed_llm × outcome_value.
        """
        if result.passed:
            if attempt_number <= 1:
                value = 1.0
            elif attempt_number <= 3:
                value = 0.8
            else:
                value = 0.5
        else:
            fm = (
                result.failure_mode.value
                if hasattr(result.failure_mode, "value")
                else str(result.failure_mode)
            )
            value = -0.2 if fm == "health_failure" else 0.0
        return OutcomeSignal(
            value=value,
            confidence=1.0,
            metadata={
                "failure_mode": (
                    result.failure_mode.value
                    if hasattr(result.failure_mode, "value")
                    else str(result.failure_mode)
                ),
                "attempt_number": attempt_number,
                **result.signals,
            },
        )

    async def evaluate(self, item: WorkItem) -> EvalResult:
        health = await mission_healthcheck(self._ssh)
        health_ok = "HEALTHY" in health

        rule_result = await stig_check_rule(self._ssh, item.id, self._profile, self._datastream)
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
                    "health": health,
                    "health_ok": health_ok,
                    "rule_check": rule_result,
                    "rule_ok": rule_ok,
                    "journal": journal[:300],
                    "journal_clean": journal_clean,
                },
            )

        if not health_ok:
            mode = FailureMode.HEALTH_FAILURE
        elif health_ok and not rule_ok:
            # DEF-29 — for the curated set of reboot-required rules, an
            # evaluator-gap result (Worker applied something, system stayed
            # healthy, rule still fails) typically means the change is on
            # disk but the kernel hasn't picked it up. Defer rather than
            # retry; resolve_deferred batches by family and reboots.
            if item.id in _REBOOT_REQUIRED_RULES:
                mode = FailureMode.NEEDS_REBOOT
            else:
                mode = FailureMode.EVALUATOR_GAP
        else:
            mode = FailureMode.CLEAN_FAILURE

        return EvalResult(
            passed=False,
            failure_mode=mode,
            summary=f"health={health_ok} rule={rule_ok} journal={journal_clean}",
            signals={
                "health": health,
                "health_ok": health_ok,
                "rule_check": rule_result,
                "rule_ok": rule_ok,
                "journal": journal[:300],
                "journal_clean": journal_clean,
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
        self._profile = profile
        self._datastream = datastream
        # DEF-28: XCCDF rule descriptions cache. Populated lazily on
        # first worker_context() call via prefetch_xccdf_descriptions().
        # ~5 seconds to fetch + parse the 27 MB datastream once;
        # subsequent lookups are O(1) dict reads. Empty dict means
        # "not yet loaded" or "load failed" — worker_context returns
        # None in either case and prompts work as before.
        self._xccdf_cache: dict[str, dict] = {}
        self._xccdf_load_attempted: bool = False

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

    async def prefetch_xccdf_descriptions(self) -> int:
        """Pre-load all XCCDF rule descriptions into the cache.

        Optional but recommended — call this once at run start (after
        the VM is reachable) so the first worker_context() call doesn't
        pay the ~5 second SSH+parse cost mid-loop. The harness invokes
        it automatically during the run's pre-flight phase.

        Returns the number of rules cached. Safe to call repeatedly;
        subsequent calls no-op if the cache is already populated.
        """
        if self._xccdf_cache:
            return len(self._xccdf_cache)
        from gemma_forge.harness.tools.openscap import extract_xccdf_descriptions

        try:
            self._xccdf_cache = await extract_xccdf_descriptions(
                self._ssh,
                datastream=self._datastream,
            )
        except Exception as exc:
            logger.warning("DEF-28 XCCDF prefetch failed: %s", exc)
            self._xccdf_cache = {}
        finally:
            self._xccdf_load_attempted = True
        return len(self._xccdf_cache)

    def worker_context(self, item: WorkItem) -> dict | None:
        """DEF-28 — return the XCCDF rule description for the Worker.

        The 27 MB SCAP datastream on the target VM contains, for every
        STIG rule, the authoritative description of what makes that rule
        pass (file paths, exact directive syntax, required strings).
        Without this, the Worker has been guessing at the scanner's
        contract from the rule title alone — see journey/38.7 for the
        78-rule chronic-failure analysis that motivated DEF-28.

        Returns ``None`` when:
          - the cache hasn't been pre-fetched (and we won't block here)
          - the rule isn't in the XCCDF datastream (rare)
          - the prefetch failed earlier (logged at warning level)

        Returning None is safe: ralph.py's prompt assembly skips the
        ``work_item_context`` section and the run proceeds as before.
        """
        if not self._xccdf_cache:
            return None
        entry = self._xccdf_cache.get(item.id)
        if not entry:
            return None
        return {
            "description": entry["description"],
            "oval_criteria": entry.get("oval_criteria") or "",  # DEF-28-deeper
            "check_artifact": entry.get("source"),
            "title": entry.get("title"),
        }

    # ------------------------------------------------------------------
    # DEF-29 — per-family reboot batching for reboot-required STIG rules.
    #
    # Mirrors the CVE pattern from journey/36 / journey/37. Differences:
    #   - CVE's resolve_deferred applies dnf upgrade per family then
    #     reboots. STIG's apply has ALREADY happened during the failed
    #     attempt loop; we just need to snapshot, reboot, and re-evaluate
    #     because the kernel hadn't picked up the change yet.
    #   - Families for STIG today: "fips" (the crypto-policy family +
    #     aide_use_fips_hashes), "kernel-cmdline" (grub2_audit_argument).
    # ------------------------------------------------------------------

    async def resolve_deferred(
        self,
        reason: str,
        items: list,
        emit: EmitEvent | None = None,
    ) -> tuple[bool, str, list[DeferredItemOutcome]]:
        """Resolve deferred reboot-required STIG rules via per-family reboot.

        Items have failure_mode=NEEDS_REBOOT, set by StigEvaluator for
        rule ids in _REBOOT_REQUIRED_RULES that fail OpenSCAP rule-check
        while keeping the system healthy. The Worker's apply already
        happened during the failed attempt; we just need to reboot and
        re-evaluate.

        Per-family flow:
            1. Save snapshot ``stig-pre-family-<name>``
            2. Issue reboot via SSH (ignore the dropped connection)
            3. Wait for SSH to come back (24×5s polls, 120s ceiling)
            4. Mission healthcheck
            5. Per-item: re-run oscap xccdf eval --rule <id>
            6. On any family-level failure → revert snapshot, mark all
               family items with the specific ``family_<mode>`` reason
            7. Always delete the family snapshot afterward

        Returns per-item DeferredItemOutcome list. Items with passed=True
        flow to remediated; failed items get escalated with the per-item
        reason captured.
        """
        emit = emit or (lambda _e, _d: None)

        if reason != "needs_reboot":
            return (True, f"StigSkillRuntime: unknown reason '{reason}'", [])
        if not items:
            return (True, "no items to resolve", [])

        # --- Group by family ---------------------------------------------
        by_family: dict[str, list] = {}
        for item in items:
            family = _stig_reboot_family(item.id)
            by_family.setdefault(family, []).append(item)

        # Order: fips first (sets up the kernel state most rules depend on),
        # then kernel-cmdline, then any other-reboot tail. The order
        # matters because if FIPS-related rules fail (rare), we want to
        # know that before applying kernel-cmdline changes.
        family_order = sorted(
            by_family.keys(),
            key=lambda f: {"fips": 0, "kernel-cmdline": 1, "other-reboot": 2}.get(f, 99),
        )

        logger.info(
            "resolve_deferred: %d items across %d families: %s",
            len(items),
            len(by_family),
            {f: len(by_family[f]) for f in family_order},
        )
        emit(
            "deferred_resolve_plan",
            {
                "total_items": len(items),
                "total_families": len(by_family),
                "family_order": family_order,
                "families": {f: [it.id for it in by_family[f]] for f in family_order},
            },
        )

        outcomes: list[DeferredItemOutcome] = []
        for pos, family in enumerate(family_order, start=1):
            family_items = by_family[family]
            family_outcomes = await self._process_stig_family_batch(
                family,
                family_items,
                emit=emit,
                position=pos,
                total_families=len(family_order),
            )
            outcomes.extend(family_outcomes)

        passed_n = sum(1 for o in outcomes if o.passed)
        summary = (
            f"STIG resolve_deferred: {len(by_family)} families, "
            f"{len(items)} items, {passed_n} verified, "
            f"{len(outcomes) - passed_n} failed"
        )
        logger.info(summary)
        return (passed_n > 0, summary, outcomes)

    async def _process_stig_family_batch(
        self,
        family: str,
        family_items: list,
        *,
        emit: EmitEvent,
        position: int,
        total_families: int,
    ) -> list[DeferredItemOutcome]:
        """Snapshot, reboot, verify one family. On any failure: revert all."""
        snap_name = f"stig-pre-family-{family}"
        logger.info(
            "family=%s: %d items: %s",
            family,
            len(family_items),
            [i.id for i in family_items],
        )
        emit(
            "family_batch_start",
            {
                "family": family,
                "position": position,
                "total_families": total_families,
                "item_count": len(family_items),
                "item_ids": [it.id for it in family_items],
                "snapshot_name": snap_name,
            },
        )

        snap_ok, snap_detail = await self._checkpoint.save(snap_name)
        if not snap_ok:
            logger.error("family=%s snapshot save failed: %s", family, snap_detail)
            emit(
                "family_batch_complete",
                {
                    "family": family,
                    "passed": False,
                    "reason": "family_snapshot_save_failed",
                    "detail": snap_detail[:200],
                },
            )
            return [
                DeferredItemOutcome(
                    rule_id=item.id,
                    passed=False,
                    reason="family_snapshot_save_failed",
                    metadata={"family": family, "detail": snap_detail[:200]},
                )
                for item in family_items
            ]

        try:
            outcomes = await self._reboot_and_verify_family(
                family,
                family_items,
                emit=emit,
            )
            passed_n = sum(1 for o in outcomes if o.passed)
            emit(
                "family_batch_complete",
                {
                    "family": family,
                    "passed": True,
                    "items_verified": passed_n,
                    "items_total": len(outcomes),
                },
            )
            return outcomes
        except Exception as exc:
            logger.exception("family=%s: unhandled exception", family)
            err_tag = type(exc).__name__.lower()
            emit(
                "family_exception",
                {
                    "family": family,
                    "exception_type": type(exc).__name__,
                    "detail": str(exc)[:200],
                },
            )
            await self._checkpoint.restore(snap_name)
            emit(
                "family_batch_complete",
                {
                    "family": family,
                    "passed": False,
                    "reason": f"family_exception_{err_tag}",
                    "detail": str(exc)[:200],
                },
            )
            return [
                DeferredItemOutcome(
                    rule_id=item.id,
                    passed=False,
                    reason=f"family_exception_{err_tag}",
                    metadata={"family": family, "exception": str(exc)[:200]},
                )
                for item in family_items
            ]
        finally:
            try:
                await self._checkpoint.delete(snap_name)
            except Exception as exc:
                logger.warning("family=%s snapshot delete failed: %s", family, exc)

    async def _reboot_and_verify_family(
        self,
        family: str,
        family_items: list,
        *,
        emit: EmitEvent,
    ) -> list[DeferredItemOutcome]:
        """Reboot, wait for SSH, healthcheck, per-item re-evaluate."""
        import asyncio as _aio
        import time as _time

        from gemma_forge.harness.tools.ssh import _run_ssh

        # --- 1. Issue reboot --------------------------------------------
        logger.info("family=%s: issuing reboot", family)
        emit("family_reboot_issued", {"family": family})
        with contextlib.suppress(Exception):  # SSH drops mid-reboot, expected
            await _run_ssh(self._ssh, "sudo reboot")

        # --- 2. Wait for SSH back ---------------------------------------
        # Per-family timeout: FIPS-mode kernel boot from a non-FIPS baseline
        # is materially slower because dracut re-probes the FIPS module on
        # first boot. Run 11 (journey/38.11) timed out at the 24×5s window
        # that worked for normal reboots; the FIPS family needs roughly
        # 5-10 minutes. Other reboot families stay at the prior budget.
        max_wait_s = _FAMILY_REBOOT_WAIT_S.get(family, _FAMILY_REBOOT_WAIT_S["other-reboot"])
        await _aio.sleep(10)
        ssh_up = False
        wait_start = _time.time()
        deadline = wait_start + max_wait_s
        attempt = 0
        while _time.time() < deadline:
            attempt += 1
            try:
                stdout, _stderr, _rc = await _run_ssh(self._ssh, "echo ok")
                if "ok" in stdout:
                    ssh_up = True
                    wait_s = round(_time.time() - wait_start + 10, 1)
                    logger.info("family=%s: SSH back up after ~%.1fs", family, wait_s)
                    emit("family_ssh_up", {"family": family, "wait_s": wait_s})
                    break
            except Exception:
                pass
            emit(
                "family_ssh_wait_tick",
                {
                    "family": family,
                    "elapsed_s": round(_time.time() - wait_start + 10, 1),
                    "attempt": attempt,
                    "max_wait_s": max_wait_s,
                },
            )
            await _aio.sleep(5)

        if not ssh_up:
            emit(
                "family_ssh_timeout",
                {
                    "family": family,
                    "waited_s": round(_time.time() - wait_start + 10, 1),
                    "max_wait_s": max_wait_s,
                },
            )
            raise RuntimeError("reboot_ssh_timeout")

        # --- 3. Mission healthcheck -------------------------------------
        emit("family_healthcheck_start", {"family": family})
        health = await mission_healthcheck(self._ssh)
        if "HEALTHY" not in health:
            logger.error(
                "family=%s post-reboot healthcheck failed: %s",
                family,
                health[:200],
            )
            emit("family_healthcheck_failed", {"family": family, "detail": health[:200]})
            raise RuntimeError("reboot_health_failed")
        emit("family_healthcheck_ok", {"family": family})

        # --- 4. Per-item re-evaluate via OpenSCAP rule check ------------
        emit("family_verify_start", {"family": family})
        outcomes: list[DeferredItemOutcome] = []
        for item in family_items:
            rule_result = await stig_check_rule(
                self._ssh,
                item.id,
                self._profile,
                self._datastream,
            )
            rule_ok = "PASS" in rule_result.upper()
            reason = "family_verified" if rule_ok else "family_still_failing"
            outcomes.append(
                DeferredItemOutcome(
                    rule_id=item.id,
                    passed=rule_ok,
                    reason=reason,
                    metadata={"family": family, "rule_check": rule_result[:200]},
                )
            )
            emit(
                "family_item_verified",
                {
                    "family": family,
                    "rule_id": item.id,
                    "passed": rule_ok,
                    "outcome_reason": reason,
                },
            )

        passed_n = sum(1 for o in outcomes if o.passed)
        logger.info(
            "family=%s: verified %d/%d items post-reboot",
            family,
            passed_n,
            len(outcomes),
        )
        emit(
            "family_verify_complete",
            {
                "family": family,
                "passed": passed_n,
                "total": len(outcomes),
            },
        )
        return outcomes


def _categorize_rule(rule_id: str) -> str:
    """Classify a STIG rule into a coarse family."""
    rid = rule_id.lower()
    name = rid.split("content_rule_", 1)[-1]
    if "aide" in rid:
        return "integrity-monitoring"
    if any(k in rid for k in ("fips", "crypto", "hash", "cipher", "ssl", "tls")):
        return "cryptography"
    # Partition/mount rules check before "audit" substring: rules like
    # partition_for_var_log_audit are filesystem-shaped even though the
    # path contains "audit". Audit rules (audit_rules_*, auditd_*) start
    # with the audit prefix, never with partition_for_ / mount_option_,
    # so they still fall through to the audit branch below.
    if name.startswith("partition_for_") or name.startswith("mount_option_"):
        return "filesystem"
    if "audit" in rid:
        return "audit"
    if "sudo" in rid or "nopasswd" in rid:
        return "privileged-access"
    if "partition" in rid or "mount" in rid:
        return "filesystem"
    if "selinux" in rid:
        return "mac"
    if any(k in rid for k in ("kernel", "sysctl", "grub", "boot")):
        return "kernel"
    if any(k in rid for k in ("firewall", "firewalld", "iptables")):
        return "network-firewall"
    if "ssh" in rid:
        return "ssh"
    if any(k in rid for k in ("password", "pam", "faillock")):
        return "authentication"
    if any(k in rid for k in ("banner", "motd", "issue")):
        return "banner"
    if any(k in rid for k in ("package", "rpm", "dnf", "gpg")):
        return "package-management"
    if any(k in rid for k in ("log", "rsyslog", "journald")):
        return "logging"
    if "service" in rid or "systemd" in rid:
        return "service-config"
    if any(k in rid for k in ("user", "account", "umask")):
        return "user-account"
    return "other"


def build_runtime(harness_cfg: dict) -> "StigSkillRuntime":
    """Manifest-declared builder. Called by the harness's _build_skill_runtime.

    Reads `vm` and `stig` blocks from harness_cfg. The harness has no
    skill-specific knowledge — this function owns the config layout.
    """
    from gemma_forge.harness.tools.ssh import SSHConfig

    vm_cfg = harness_cfg.get("vm", {}) or {}
    stig_cfg = harness_cfg.get("stig", {}) or {}
    ssh_config = SSHConfig(
        host=vm_cfg.get("ip", "192.168.122.43"),
        user=vm_cfg.get("user", "adm-forge"),
        key_path=vm_cfg.get("ssh_key", "/data/vm/gemma-forge/keys/adm-forge"),
    )
    profile = stig_cfg.get(
        "profile",
        "xccdf_org.ssgproject.content_profile_stig",
    )
    datastream = stig_cfg.get(
        "datastream",
        "/usr/share/xml/scap/ssg/content/ssg-rl9-ds.xml",
    )
    return StigSkillRuntime(ssh_config, profile, datastream)
