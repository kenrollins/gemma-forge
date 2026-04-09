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
import shlex
from dataclasses import dataclass, field
from typing import Optional

import asyncssh


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
    """Run a bash script on the target VM and return (stdout, stderr, exit_code)."""
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
            timeout=120,
        )
        return (
            result.stdout or "",
            result.stderr or "",
            result.returncode or 0,
        )


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

    if rc == 0:
        return f"APPLIED: {description}\nOutput: {stdout.strip()}"
    else:
        # Keep the revert script stored — the fix may have partially executed.
        return (
            f"APPLY_FAILED: {description}\n"
            f"Exit code: {rc}\n"
            f"Stdout: {stdout.strip()}\n"
            f"Stderr: {stderr.strip()}"
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
