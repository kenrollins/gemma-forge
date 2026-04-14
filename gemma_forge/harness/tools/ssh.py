"""SSH tools for the Ralph loop — apply fixes and revert on failure.

These tools run bash scripts on the target VM via SSH. The revert
mechanism is script-based: the Worker generates both a fix script and a
revert script. The harness stores the revert script and executes it if
the Auditor's healthcheck fails.

This is a deliberate design choice over VM-snapshot-based revert:
"the agent reverts its own fix" is a stronger demo story than "the VM
goes back to a snapshot."
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from dataclasses import dataclass, field
from typing import Optional

import asyncssh

logger = logging.getLogger(__name__)


@dataclass
class SSHConfig:
    """Connection details for the target VM."""

    host: str
    user: str
    key_path: str
    connect_timeout: int = 10


# Module-level state: stores the last applied revert script so
# ssh_revert can undo the most recent fix without the caller needing
# to track it.
_last_revert_script: Optional[str] = None
_last_fix_description: Optional[str] = None


async def _run_ssh(config: SSHConfig, script: str) -> tuple[str, str, int]:
    """Run a bash script on the target VM. Falls back to virsh console if SSH fails.

    The fallback is critical: if the agent hardens SSH (FIPS, cipher policy,
    firewall), it can lock itself out. The serial console is an out-of-band
    channel that bypasses the guest's network stack entirely — nothing the
    agent does inside the VM can break it.
    """
    # Try SSH first (fast, full-featured)
    try:
        async with asyncssh.connect(
            config.host,
            username=config.user,
            client_keys=[config.key_path],
            known_hosts=None,
            connect_timeout=config.connect_timeout,
        ) as conn:
            result = await conn.run(
                f"sudo bash -c {shlex.quote(script)}",
                check=False,
                timeout=300,
            )
            return (
                result.stdout or "",
                result.stderr or "",
                result.returncode or 0,
            )
    except (asyncssh.process.TimeoutError, asyncio.TimeoutError, TimeoutError) as e:
        logger.warning("SSH timeout — falling back to virsh console: %s", e)
    except (OSError, asyncssh.Error) as e:
        logger.warning("SSH failed — falling back to virsh console: %s", e)

    # Fallback: virsh console (out-of-band, survives SSH lockout)
    try:
        from .console import run_via_console
        logger.info("Using virsh console fallback for command execution")
        return await run_via_console(
            domain="gemma-forge-mission-app",
            command=script,
            user=config.user,
        )
    except Exception as e:
        logger.error("Both SSH and console fallback failed: %s", e)
        return ("", f"ALL_CHANNELS_FAILED: SSH and console both failed — {e}", 126)


# -- Snapshot management (authoritative revert mechanism) --------------------
#
# See docs/whitepaper/improvements/04-snapshot-based-revert.md for the full
# design. Short version: Worker-written revert scripts are fragile (they need
# sudo, they need to be correct, they can't undo filesystem state they didn't
# anticipate). libvirt snapshots are authoritative — the revert target is a
# pristine qcow2 COW state that cannot be corrupted by the guest.
#
# Snapshot lifecycle:
#   baseline  — VM initial state, never modified after first creation
#   progress  — rolling, advanced after each successful remediation so prior
#               fixes are preserved across attempts
#   Failed attempts restore to `progress`; the very first attempt (before
#   any remediations) restores to `baseline` because `progress` won't exist yet.

SNAPSHOT_SCRIPT = "/data/code/gemma-forge/infra/vm/scripts/vm-snapshot.sh"


async def _run_snapshot_cmd(action: str, name: str = "", timeout: int = 60) -> tuple[bool, str]:
    """Invoke the vm-snapshot.sh helper and return (ok, output)."""
    import os
    if not os.path.exists(SNAPSHOT_SCRIPT):
        return False, f"snapshot script missing: {SNAPSHOT_SCRIPT}"
    args = [SNAPSHOT_SCRIPT, action]
    if name:
        args.append(name)
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = (stdout + stderr).decode("utf-8", errors="replace").strip()
        return proc.returncode == 0, output[-500:]
    except asyncio.TimeoutError:
        return False, f"snapshot {action} timed out after {timeout}s"
    except Exception as e:  # noqa: BLE001
        return False, f"snapshot {action} error: {e}"


async def snapshot_exists(name: str) -> bool:
    """True if a named libvirt snapshot exists for the target VM."""
    ok, output = await _run_snapshot_cmd("list", timeout=15)
    if not ok:
        return False
    return name in output


async def snapshot_save_progress() -> tuple[bool, str]:
    """Create (or replace) the rolling `progress` snapshot.

    Called after each successful rule remediation so the VM state
    containing accumulated fixes can be restored on failure of a
    subsequent attempt without losing prior progress.
    """
    # Delete an existing `progress` snapshot first to keep the chain short.
    # libvirt won't error if it doesn't exist; ignore failure.
    await _run_snapshot_cmd("delete", "progress", timeout=30)
    ok, output = await _run_snapshot_cmd("create", "progress", timeout=60)
    return ok, output


async def snapshot_restore_progress() -> tuple[bool, str]:
    """Restore the VM to the most recent known-good state.

    Prefers `progress` (which includes accumulated successful remediations)
    and falls back to `baseline` when `progress` does not yet exist.
    """
    if await snapshot_exists("progress"):
        ok, output = await _run_snapshot_cmd("restore", "progress", timeout=60)
        return ok, f"restored=progress detail={output}"
    ok, output = await _run_snapshot_cmd("restore", "baseline", timeout=60)
    return ok, f"restored=baseline detail={output}"


# -- Environment diagnostics ---------------------------------------------------
#
# Before we nuke the VM back to a snapshot, we capture WHY it failed. This
# gives the Reflector concrete forensics to reason about (instead of just
# "APPLY_FAILED"), preserves a post-mortem in the event log for humans, and
# informs the architect's re-engagement decisions.

_DIAG_SCRIPT = r"""
set -o pipefail
echo "=== SUDO_PROBE ==="
(sudo -n whoami 2>&1; echo "rc=$?") | head -5
echo
echo "=== SERVICE_STATUS ==="
for svc in nginx postgresql sshd chronyd; do
  state=$(systemctl is-active "$svc" 2>&1 || true)
  echo "$svc: $state"
done
echo
echo "=== MISSION_HEALTHCHECK ==="
if [ -x /usr/local/bin/mission-healthcheck.sh ]; then
  timeout 10 /usr/local/bin/mission-healthcheck.sh 2>&1 | head -20
else
  echo "mission-healthcheck.sh not found or not executable"
fi
echo
echo "=== RECENT_AUTH_FAILURES ==="
(journalctl -p err --since "10 minutes ago" --no-pager 2>&1 || true) \
  | grep -iE 'sudo|pam|auth|permiss|fail' | tail -15
echo
echo "=== SUDOERS_STATE ==="
(stat -c '%n %s %y' /etc/sudoers 2>&1 || true)
(ls -la /etc/sudoers.d/ 2>&1 || true) | head -10
echo
echo "=== PAM_STATE ==="
(ls -la /etc/pam.d/sudo /etc/pam.d/system-auth /etc/pam.d/password-auth 2>&1 || true) | head -10
echo
echo "=== FS_STATE ==="
(mount | grep -E ' / | /etc | /var ' 2>&1 || true)
(touch /tmp/_forge_fs_probe && rm /tmp/_forge_fs_probe && echo "/tmp writable") || echo "/tmp NOT writable"
echo
echo "=== NETWORK_STATE ==="
(ss -tln 2>&1 || netstat -tln 2>&1 || true) | head -10
echo
echo "=== RECENT_JOURNAL_ERRORS ==="
(journalctl -p err --since "5 minutes ago" --no-pager 2>&1 || true) | tail -20
"""


async def gather_environment_diagnostics(config: SSHConfig) -> dict:
    """Capture a forensic snapshot of the target VM's state.

    Returns a structured dict with sections for sudo, services, healthcheck,
    auth failures, sudoers state, PAM state, filesystem, network, and recent
    journal errors. Used before a snapshot-based revert so the Reflector has
    real facts to reason about instead of just "APPLY_FAILED".

    Falls back to virsh console when sudo or SSH itself is broken — this is
    exactly when we most need out-of-band visibility. Unlike `_run_ssh`,
    which only falls back on connection-level errors, this function
    additionally falls back when sudo specifically fails (rc=1 with sudo
    diagnostics in stderr). The console runs as root via the serial port
    and bypasses the guest's sudo/auth stack entirely.
    """
    stdout, stderr, rc = await _run_ssh(config, _DIAG_SCRIPT)
    raw = stdout + (("\n" + stderr) if stderr else "")

    # If sudo is broken, the SSH path returns rc=1 with stderr mentioning sudo
    # and the diagnostic script never actually ran. Detect this and retry via
    # the console fallback so we still get useful forensics.
    looks_like_sudo_failure = (
        rc != 0 and (
            "sudo:" in stderr.lower() or "password is required" in stderr.lower()
            or "a terminal is required" in stderr.lower()
        )
    )
    diagnostic_didnt_run = "=== SUDO_PROBE ===" not in raw
    if looks_like_sudo_failure or diagnostic_didnt_run:
        try:
            from .console import run_via_console
            logger.warning("Diagnostic SSH/sudo failed (rc=%d) — falling back to virsh console", rc)
            console_stdout, console_stderr, console_rc = await run_via_console(
                domain="gemma-forge-mission-app",
                command=_DIAG_SCRIPT,
                user=config.user,
            )
            raw = console_stdout + (("\n" + console_stderr) if console_stderr else "")
            rc = console_rc
        except Exception as e:  # noqa: BLE001
            logger.error("Console fallback also failed: %s", e)
            # raw stays as the original SSH-attempt output, which at least
            # tells us "sudo broken" via the stderr
    # Parse the === SECTION === markers into a dict
    sections: dict = {"_raw": raw[:4000], "_exit_code": rc}
    current_section = None
    current_lines: list = []
    for line in raw.splitlines():
        if line.startswith("=== ") and line.endswith(" ==="):
            if current_section is not None:
                sections[current_section.lower()] = "\n".join(current_lines).strip()[:600]
            current_section = line.strip("= ").strip()
            current_lines = []
        elif current_section is not None:
            current_lines.append(line)
    if current_section is not None:
        sections[current_section.lower()] = "\n".join(current_lines).strip()[:600]

    # Derive boolean flags for quick decisioning.
    #
    # IMPORTANT: do NOT use substring containment for status flags. The
    # mission healthcheck outputs "HEALTHY: ..." or "UNHEALTHY:\n  - ...",
    # and "UNHEALTHY" contains "HEALTHY" as a substring. The check must
    # look for the marker at the start of a line, not anywhere in the
    # text. This bug was caught by the Tier 3 broken-state tests; see
    # docs/whitepaper/journey/15-the-test-as-architecture-discovery.md.
    sudo_text = sections.get("sudo_probe", "")
    sections["sudo_ok"] = ("rc=0" in sudo_text and "root" in sudo_text)

    svc_text = sections.get("service_status", "")
    # A service is healthy iff its line says "active". The naive check
    # "nginx: active in svc_text" was correct here only because the
    # status word doesn't have a substring trap, but be explicit to
    # match the same pattern we use for mission_healthy.
    sections["services_ok"] = (
        any(line.startswith("nginx: active") for line in svc_text.splitlines())
        and any(line.startswith("postgresql: active") for line in svc_text.splitlines())
    )

    hc_text = sections.get("mission_healthcheck", "")
    # Look for "HEALTHY:" at the start of a line AND no UNHEALTHY anywhere.
    # The script outputs either "HEALTHY: ..." (one line) or "UNHEALTHY:\n  - ...".
    sections["mission_healthy"] = (
        any(line.lstrip().startswith("HEALTHY:") for line in hc_text.splitlines())
        and "UNHEALTHY" not in hc_text
    )

    return sections


async def check_sudo_healthy(config: SSHConfig) -> tuple[bool, str]:
    """Quick sudo probe. Kept for compatibility and quick pre-checks.

    Prefer `gather_environment_diagnostics` for full forensics.
    """
    try:
        async with asyncssh.connect(
            config.host,
            username=config.user,
            client_keys=[config.key_path],
            known_hosts=None,
            connect_timeout=config.connect_timeout,
        ) as conn:
            result = await conn.run("sudo -n whoami 2>&1", check=False, timeout=15)
            stdout = (result.stdout or "").strip()
            rc = result.returncode or 0
            if rc == 0 and "root" in stdout:
                return True, "sudo_ok"
            return False, f"rc={rc} out={stdout[:120]}"
    except Exception as e:  # noqa: BLE001
        return False, f"ssh_error: {e}"


async def ssh_apply(
    config: SSHConfig,
    fix_script: str,
    revert_script: str,
    description: str = "",
) -> str:
    """Apply a STIG fix to the target VM.

    The revert script is stored BEFORE the fix runs, because even a
    partially-executed fix can leave damage that needs reverting.
    This was learned from the Phase 3 validation: a sed command broke
    aide.conf even though the overall script exited non-zero.

    Args:
        config: SSH connection details.
        fix_script: Bash script that applies the fix.
        revert_script: Bash script that reverts the fix. Stored for
            later use by ssh_revert().
        description: Human-readable description of what this fix does.

    Returns:
        A status string: "APPLIED: ..." or "APPLY_FAILED: ...".
    """
    global _last_revert_script, _last_fix_description

    # Store the revert script BEFORE running the fix — a partially-
    # executed fix can still leave damage that needs reverting.
    _last_revert_script = revert_script
    _last_fix_description = description

    stdout, stderr, rc = await _run_ssh(config, fix_script)

    # Cap tool output to avoid context overflow. The full output is
    # available in the run log; the model only needs enough to understand
    # what happened. 2000 chars ≈ ~500 tokens, well within budget.
    max_output = 2000
    out = stdout.strip()[:max_output]
    err = stderr.strip()[:max_output]
    if len(stdout.strip()) > max_output:
        out += "\n[...output truncated]"

    if rc == 0:
        return f"APPLIED: {description}\nOutput: {out}"
    else:
        # Keep the revert script stored — the fix may have partially executed.
        return (
            f"APPLY_FAILED: {description}\n"
            f"Exit code: {rc}\n"
            f"Stdout: {out}\n"
            f"Stderr: {err}"
        )


async def ssh_revert(config: SSHConfig) -> str:
    """Revert the most recently applied fix.

    Returns:
        A status string: "REVERTED: ..." or "REVERT_FAILED: ..." or
        "NO_REVERT: no fix to revert".
    """
    global _last_revert_script, _last_fix_description

    if _last_revert_script is None:
        return "NO_REVERT: no fix to revert (nothing was applied or already reverted)"

    revert = _last_revert_script
    desc = _last_fix_description or "unknown fix"

    stdout, stderr, rc = await _run_ssh(config, revert)

    # Clear the stored revert regardless of outcome — don't double-revert
    _last_revert_script = None
    _last_fix_description = None

    if rc == 0:
        return f"REVERTED: {desc}\nOutput: {stdout.strip()}"
    else:
        return (
            f"REVERT_FAILED: {desc}\n"
            f"Exit code: {rc}\n"
            f"Stdout: {stdout.strip()}\n"
            f"Stderr: {stderr.strip()}"
        )
