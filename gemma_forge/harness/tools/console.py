"""Virsh console fallback — out-of-band command execution via serial console.

When SSH fails (because the agent hardened SSH policy, enabled FIPS, or
changed firewall rules), this module provides a fallback command channel
through libvirt's serial console. The hypervisor-level console bypasses
the guest's network stack, SSH config, and crypto policy entirely.

Nothing the agent does inside the VM can break this channel — it operates
at the virtualization layer.

Usage:
    result = await run_via_console("gemma-forge-mission-app", "whoami")
    # Returns (stdout, stderr, exit_code)
"""

import asyncio
import logging
import re

logger = logging.getLogger(__name__)

# Unique markers to delimit command output in the serial stream
_START_MARKER = "___GEMMAFORGE_CMD_START___"
_END_MARKER = "___GEMMAFORGE_CMD_END___"
_RC_MARKER = "___GEMMAFORGE_RC___"

# Lock — only one virsh console session per VM at a time
_console_lock = asyncio.Lock()


async def run_via_console(
    domain: str,
    command: str,
    user: str = "adm-forge",
    timeout: int = 300,
) -> tuple[str, str, int]:
    """Execute a command on a VM via virsh console (serial port).

    This is the fallback channel when SSH is unavailable. It:
      1. Opens a virsh console session to the domain
      2. Sends the command wrapped in unique markers
      3. Parses the output between markers
      4. Extracts the exit code
      5. Closes the console session

    Args:
        domain: libvirt domain name (e.g., "gemma-forge-mission-app")
        command: bash command to execute
        user: user to run as (via sudo)
        timeout: max seconds to wait for command completion

    Returns:
        (stdout, stderr, exit_code) — same interface as _run_ssh()
    """
    async with _console_lock:
        return await _console_exec(domain, command, user, timeout)


async def _console_exec(
    domain: str,
    command: str,
    user: str,
    timeout: int,
) -> tuple[str, str, int]:
    """Internal: execute a command through virsh console."""

    # Wrap the command to produce parseable output:
    #   echo START_MARKER
    #   sudo bash -c '<command>' 2>/tmp/stderr.txt
    #   echo RC_MARKER $?
    #   echo END_MARKER
    #   cat /tmp/stderr.txt (for stderr capture)
    wrapped = (
        f"echo {_START_MARKER}\n"
        f"sudo -u {user} bash -c {_shell_quote(command)} 2>/tmp/_gf_stderr.txt\n"
        f"echo {_RC_MARKER} $?\n"
        f"echo {_END_MARKER}\n"
        f"cat /tmp/_gf_stderr.txt 2>/dev/null\n"
        f"echo {_END_MARKER}_STDERR\n"
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "virsh", "console", domain, "--force",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Send a newline first to get a login prompt or shell prompt
        proc.stdin.write(b"\n")
        await proc.stdin.drain()
        await asyncio.sleep(1)

        # Send the wrapped command
        proc.stdin.write(wrapped.encode() + b"\n")
        await proc.stdin.drain()

        # Read output with timeout
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                _read_until_marker(proc, _END_MARKER + "_STDERR"),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            return ("", f"CONSOLE_TIMEOUT: command exceeded {timeout}s", 124)

        # Close the console
        proc.stdin.write(b"\x1d")  # Ctrl+] to exit virsh console
        await proc.stdin.drain()
        proc.kill()

        output = stdout_bytes.decode(errors="replace")

        # Parse stdout between markers
        stdout = _extract_between(output, _START_MARKER, _RC_MARKER)
        stderr = _extract_between(output, _END_MARKER, _END_MARKER + "_STDERR")

        # Parse exit code
        rc_match = re.search(f"{_RC_MARKER} (\\d+)", output)
        rc = int(rc_match.group(1)) if rc_match else 1

        logger.info("Console exec completed (rc=%d, %d chars output)", rc, len(stdout))
        return (stdout.strip(), stderr.strip(), rc)

    except Exception as e:
        logger.error("Console exec failed: %s", e)
        return ("", f"CONSOLE_ERROR: {e}", 125)


async def _read_until_marker(
    proc: asyncio.subprocess.Process,
    marker: str,
) -> tuple[bytes, bytes]:
    """Read from the process until we see the end marker."""
    collected = b""
    while True:
        try:
            chunk = await asyncio.wait_for(
                proc.stdout.read(4096),
                timeout=5,
            )
        except asyncio.TimeoutError:
            # No data for 5s — check if we have the marker already
            if marker.encode() in collected:
                break
            continue

        if not chunk:
            break
        collected += chunk
        if marker.encode() in collected:
            break

    return collected, b""


def _extract_between(text: str, start: str, end: str) -> str:
    """Extract text between two markers."""
    start_idx = text.find(start)
    end_idx = text.find(end, start_idx + len(start) if start_idx >= 0 else 0)
    if start_idx >= 0 and end_idx >= 0:
        return text[start_idx + len(start):end_idx].strip()
    return ""


def _shell_quote(s: str) -> str:
    """Quote a string for bash -c."""
    return "'" + s.replace("'", "'\\''") + "'"
