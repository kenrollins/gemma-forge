"""Journal reader tool — reads recent system journal entries from the target VM.

Used by the Auditor to check for side effects that the healthcheck
might miss: service failures, SELinux denials, disk pressure warnings,
unexpected restarts, etc.
"""

from __future__ import annotations

from .ssh import SSHConfig, _run_ssh


async def read_recent_journal(
    config: SSHConfig,
    minutes: int = 5,
    priority: str = "err",
) -> str:
    """Read recent journal entries from the target VM.

    Args:
        config: SSH connection details.
        minutes: How many minutes back to look (default 5).
        priority: Minimum priority level (emerg, alert, crit, err, warning).

    Returns:
        Journal entries or "JOURNAL_CLEAN: no concerning entries".
    """
    script = f"""
journalctl --since '{minutes} minutes ago' --priority={priority} --no-pager --lines=30 2>/dev/null || echo "NO_JOURNAL"
"""
    stdout, stderr, rc = await _run_ssh(config, script)

    output = stdout.strip()
    if not output or output == "NO_JOURNAL" or "No entries" in output or "no entries" in output:
        return f"JOURNAL_CLEAN: no entries at priority {priority} or above in the last {minutes} minutes"

    return f"JOURNAL_ENTRIES (last {minutes}min, priority>={priority}):\n{output[:1500]}"
