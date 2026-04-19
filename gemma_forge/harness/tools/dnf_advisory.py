"""dnf --advisory tool wrappers for the CVE-response skill.

Applies a specific Red Hat / Rocky security advisory via dnf over SSH.
Parses dnf's output for structured success/failure signals.

Design:
- ``apply_advisory()``: runs ``dnf upgrade --advisory=<ID> -y`` via SSH,
  captures stdout/stderr/exit code.
- ``list_pending_advisories()``: equivalent of
  ``dnf updateinfo list --security`` for a quick cross-check.
- ``check_advisory_resolved()``: after apply, confirm the advisory is
  no longer in the pending list.
- ``check_needs_reboot()``: runs ``needs-restarting -r`` to detect
  NEEDS_REBOOT verdicts for kernel / glibc advisories.

All SSH runs use the shared ``_run_ssh`` helper so SSH config, timeouts,
and error handling stay consistent with the STIG skill.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .ssh import SSHConfig, _run_ssh

logger = logging.getLogger(__name__)


@dataclass
class AdvisoryApplyResult:
    """Structured result of a single advisory apply."""
    advisory_id: str
    exit_code: int
    stdout: str
    stderr: str
    # Parsed: true if dnf actually upgraded packages (not "nothing to do")
    packages_upgraded: list[str]
    # Parsed: true if dnf output mentions a reboot being needed
    reboot_hinted: bool
    # Parsed: true if the advisory ID appears unknown to dnf
    unknown_advisory: bool


async def apply_advisory(
    config: SSHConfig,
    advisory_id: str,
) -> AdvisoryApplyResult:
    """Apply one advisory via ``dnf upgrade --advisory=<ID> -y``.

    Returns a structured result. Never raises on dnf failure — caller
    inspects exit_code / stdout / stderr / parsed flags to decide the
    verdict.

    Security note: the advisory_id is interpolated into the shell
    command. We validate format before running to prevent injection.
    """
    # Strict format validation: RLSA-YYYY:NNNN or RHSA-YYYY:NNNN only
    if not re.fullmatch(r"R[LH]SA-\d{4}:\d+", advisory_id):
        raise ValueError(
            f"apply_advisory: invalid advisory_id format {advisory_id!r}; "
            f"expected R[L|H]SA-YYYY:NNNN"
        )

    script = (
        f"set -o pipefail; "
        f"sudo dnf upgrade --advisory={advisory_id} -y 2>&1 | tee /tmp/dnf-apply.log; "
        f"EC=${{PIPESTATUS[0]}}; "
        f"echo '---DNF_EXIT---'; "
        f"echo $EC"
    )
    stdout, stderr, _rc = await _run_ssh(config, script)
    raw = stdout
    exit_code = _parse_exit_marker(raw)
    stdout = raw.split("---DNF_EXIT---")[0]

    return AdvisoryApplyResult(
        advisory_id=advisory_id,
        exit_code=exit_code,
        stdout=stdout[-4000:],  # trim for log storage
        stderr="",
        packages_upgraded=_parse_upgraded_packages(stdout),
        reboot_hinted=_parse_reboot_hint(stdout),
        unknown_advisory=_parse_unknown_advisory(stdout),
    )


def _parse_exit_marker(raw: str) -> int:
    """Extract the exit code emitted after ---DNF_EXIT---."""
    parts = raw.rsplit("---DNF_EXIT---", 1)
    if len(parts) != 2:
        return -1
    tail = parts[1].strip()
    try:
        return int(tail.splitlines()[0])
    except (ValueError, IndexError):
        return -1


def _parse_upgraded_packages(stdout: str) -> list[str]:
    """Extract upgraded package names from dnf's Upgraded section.

    dnf output looks like:
      Upgraded:
        openssl-3.0.7-4.el9_5.1.x86_64    openssl-libs-3.0.7-4.el9_5.1.x86_64
      Complete!

    or "Nothing to do. Complete!" if the advisory is already applied.
    """
    m = re.search(r"^Upgraded:\n((?:\s+.+\n)+)", stdout, re.MULTILINE)
    if not m:
        return []
    block = m.group(1)
    pkgs: list[str] = []
    for line in block.splitlines():
        for token in line.split():
            # Strip arch suffix: "openssl-3.0.7-4.el9.x86_64" → keep full NVRA
            if token.strip():
                pkgs.append(token.strip())
    return pkgs


_REBOOT_KEYWORDS = (
    "reboot required", "reboot is needed", "reboot your system",
    "please reboot", "need to reboot",
)


def _parse_reboot_hint(stdout: str) -> bool:
    """Check dnf stdout for reboot-required language."""
    low = stdout.lower()
    return any(kw in low for kw in _REBOOT_KEYWORDS)


def _parse_unknown_advisory(stdout: str) -> bool:
    """Detect 'no match for advisory' / 'no packages marked for update' patterns.

    Rocky's dnf phrases this as 'No match for argument: <advisory>' or
    'Nothing to do. Complete!' depending on whether any listed package
    is installed. The first is the relevant signal for 'advisory
    invalid'; the second usually means 'already applied or packages
    not on this host'.
    """
    return "no match for argument" in stdout.lower()


async def list_pending_advisories(
    config: SSHConfig,
    severity: str | None = None,
) -> list[str]:
    """Return the list of advisory IDs dnf considers still applicable.

    Used by the Evaluator to confirm an advisory was actually cleared
    after apply. Parses ``dnf updateinfo list --security`` output.
    """
    filter_arg = ""
    if severity:
        filter_arg = f" --sec-severity={severity}"
    script = f"sudo dnf updateinfo list --security{filter_arg} 2>&1 || true"
    stdout, stderr, _rc = await _run_ssh(config, script)
    raw = stdout

    # Each line looks like:
    #   RLSA-2026:6266  Moderate/Sec.  libxslt-1.1.34-14.el9_7.1.x86_64
    ids: set[str] = set()
    for line in raw.splitlines():
        m = re.match(r"^(R[LH]SA-\d{4}:\d+)\s+", line.strip())
        if m:
            ids.add(m.group(1))
    return sorted(ids)


async def check_advisory_resolved(
    config: SSHConfig,
    advisory_id: str,
) -> bool:
    """True if ``advisory_id`` is no longer in the pending list."""
    pending = await list_pending_advisories(config)
    return advisory_id not in pending


async def check_needs_reboot(
    config: SSHConfig,
) -> bool:
    """Run ``needs-restarting -r`` on the target. True if reboot required.

    Exit codes:
      0 — reboot not needed
      1 — reboot needed
      other — tool error / not installed

    Treating "other" as False (no reboot needed) is conservative:
    we'd rather under-detect reboot requirements than panic-reboot
    on ambiguous signals.
    """
    script = "sudo needs-restarting -r > /dev/null 2>&1; echo EXIT:$?"
    stdout, stderr, _rc = await _run_ssh(config, script)
    raw = stdout
    m = re.search(r"EXIT:(\d+)", raw)
    if not m:
        return False
    return m.group(1) == "1"


async def installed_package_diff(
    config: SSHConfig,
    before_nvras: list[str],
) -> tuple[list[str], list[str]]:
    """Compare current installed packages to a prior snapshot.

    Returns (removed, added). Used by the POLICY_VIOLATION check — if
    the Worker "fixed" a CVE by `dnf remove`-ing a baseline package,
    that package appears in ``removed``, and the evaluator rejects
    the apply.

    NVRA format: ``name-version-release.arch`` (e.g.
    ``openssl-3.0.7-4.el9_5.1.x86_64``).
    """
    script = "rpm -qa --queryformat '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\\n' | sort"
    stdout, stderr, _rc = await _run_ssh(config, script)
    raw = stdout
    current = {line.strip() for line in raw.splitlines() if line.strip()}
    before = set(before_nvras)

    removed = sorted(before - current)
    added = sorted(current - before)
    return removed, added
