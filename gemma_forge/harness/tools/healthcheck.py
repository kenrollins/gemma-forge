"""Mission-app healthcheck tool for the Ralph loop.

This is the Auditor's gate: after each STIG fix, the Auditor calls
this to verify the mission app is still healthy. If it returns
UNHEALTHY, the fix must be reverted.
"""

from __future__ import annotations

from .ssh import SSHConfig, _run_ssh


async def mission_healthcheck(
    config: SSHConfig,
    healthcheck_cmd: str = "/usr/local/bin/mission-healthcheck.sh",
) -> str:
    """Run the mission-app healthcheck on the target VM.

    Returns:
        "HEALTHY: ..." if all services are up, or
        "UNHEALTHY: ..." with details of what's broken.
    """
    stdout, stderr, rc = await _run_ssh(config, healthcheck_cmd)
    return stdout.strip()
