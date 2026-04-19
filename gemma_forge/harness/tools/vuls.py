"""Vuls tool wrappers for the CVE-response skill.

Vuls (github.com/future-architect/vuls) is the agentless SSH CVE
scanner. We run it via Docker, point it at the target VM, and parse
its JSON output into WorkItems.

Design:
- ``run_vuls_scan_report()``: runs scan+report in one Docker invocation,
  returns the parsed JSON result dict.
- ``parse_vuls_json()``: consumes Vuls's result JSON, emits a dict
  keyed by advisory ID with package + severity + reboot-required
  metadata.
- ``is_reboot_required_advisory()``: heuristic based on affected
  package names (kernel, glibc, systemd, dbus — anything that
  requires restart to take effect). Used by the ``deferrable_reboot``
  ordering predicate.

Rocky Linux 9 emits RLSA-* IDs, not RHSA-*. Shape is identical
(PREFIX-YYYY:NUMBER), so we treat both prefixes as equivalent at the
parse layer. The skill prompts reference advisories generically.

Prereqs:
- Vuls + go-cve-dictionary + goval-dictionary Docker images pulled
- DBs initialized at /data/vuls/db/{cve,oval}.sqlite3
- Config at /data/vuls/config/config.toml pointing at target VM
- known_hosts at /data/vuls/config/known_hosts for the target
- SSH key at the path referenced in config.toml
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Packages whose upgrade requires reboot to take effect on a running
# system. Kernel, glibc, systemd, dbus, D-Bus-replaced services. This
# is a heuristic, not exhaustive — Vuls doesn't explicitly mark
# reboot-required in RLSA metadata, so we infer from package names.
#
# ``needs-restarting -r`` on the VM post-apply gives the runtime
# answer; this list is for pre-apply deferral decisions.
_REBOOT_REQUIRED_PACKAGES = {
    "kernel", "kernel-core", "kernel-modules", "kernel-modules-core",
    "kernel-modules-extra", "kernel-tools", "kernel-tools-libs",
    "kernel-headers", "kpatch", "kpatch-dnf",
    "glibc", "glibc-common", "glibc-minimal-langpack",
    "glibc-langpack-en", "glibc-gconv-extra",
    "systemd", "systemd-libs", "systemd-pam", "systemd-udev",
    "systemd-resolved", "systemd-networkd",
    "dbus", "dbus-broker", "dbus-common", "dbus-libs",
}


@dataclass
class VulsAdvisory:
    """One advisory (RHSA or RLSA) extracted from Vuls JSON."""
    advisory_id: str                 # e.g., "RLSA-2026:6266"
    severity: str                    # Critical | Important | Moderate | Low
    cve_ids: list[str]               # ["CVE-2023-40403", ...]
    affected_packages: list[str]     # package names
    title: str                       # short description
    description: str                 # longer description
    requires_reboot: bool            # inferred from package names


def is_reboot_required_advisory(affected_packages: list[str]) -> bool:
    """True if any affected package requires reboot to activate the upgrade."""
    for pkg in affected_packages:
        # Strip version / arch suffixes: "kernel-5.14.0-611.x86_64" -> "kernel"
        base = pkg.split("-")[0] if "-" in pkg else pkg
        if base in _REBOOT_REQUIRED_PACKAGES:
            return True
        # Also match the fuller package-name prefix (e.g. "kernel-core")
        for rr_pkg in _REBOOT_REQUIRED_PACKAGES:
            if pkg.startswith(rr_pkg + "-") or pkg == rr_pkg:
                return True
    return False


def parse_vuls_json(result_json: dict, severity_filter: Optional[list[str]] = None) -> list[VulsAdvisory]:
    """Parse Vuls scan-report JSON into a deduplicated advisory list.

    Vuls JSON structure (relevant subset):
      scannedCves: { <CVE-ID>: {
         cveID, affectedPackages: [{name, fixedIn}, ...],
         distroAdvisories: [{advisoryID, severity, description, ...}],
         cveContents: {...}
      }}

    One CVE can be fixed by one advisory; one advisory can fix many CVEs.
    We emit one ``VulsAdvisory`` per distinct advisory_id, aggregating
    CVEs and affected packages across matching entries.

    Args:
        result_json: the parsed JSON from Vuls's result file.
        severity_filter: if non-empty, only return advisories whose
            severity is in this list (case-insensitive).

    Returns:
        Deduplicated ``VulsAdvisory`` list sorted by
        (severity rank, advisory_id).
    """
    severity_filter = [s.lower() for s in (severity_filter or [])]
    cves = result_json.get("scannedCves", {}) or {}

    # advisory_id -> VulsAdvisory (built up incrementally)
    by_advisory: dict[str, VulsAdvisory] = {}

    for cve_id, info in cves.items():
        distro_advs = info.get("distroAdvisories", []) or []
        aff_pkgs = [p["name"] for p in info.get("affectedPackages", []) or [] if p.get("name")]

        for adv in distro_advs:
            adv_id = adv.get("advisoryID", "")
            if not adv_id:
                continue
            severity = adv.get("severity", "Unknown")

            if severity_filter and severity.lower() not in severity_filter:
                continue

            if adv_id not in by_advisory:
                by_advisory[adv_id] = VulsAdvisory(
                    advisory_id=adv_id,
                    severity=severity,
                    cve_ids=[],
                    affected_packages=[],
                    title=_advisory_title(adv.get("description", "")),
                    description=adv.get("description", "")[:1000],
                    requires_reboot=False,  # set at end
                )
            entry = by_advisory[adv_id]
            if cve_id not in entry.cve_ids:
                entry.cve_ids.append(cve_id)
            for p in aff_pkgs:
                if p not in entry.affected_packages:
                    entry.affected_packages.append(p)

    # Set requires_reboot flag based on collected affected_packages
    for entry in by_advisory.values():
        entry.requires_reboot = is_reboot_required_advisory(entry.affected_packages)

    # Sort: severity ranked (Critical > Important > Moderate > Low > Unknown),
    # then advisory_id descending (newer first).
    severity_rank = {"critical": 0, "important": 1, "moderate": 2, "low": 3}
    def sort_key(a: VulsAdvisory) -> tuple:
        return (severity_rank.get(a.severity.lower(), 4), -_numeric_advisory(a.advisory_id))
    return sorted(by_advisory.values(), key=sort_key)


def _advisory_title(description: str) -> str:
    """Extract a one-line title from the advisory description."""
    first = (description or "").split("\n", 1)[0].strip()
    # Trim at first period for brevity
    if "." in first:
        first = first.split(".", 1)[0].strip()
    return first[:200] or "security update"


def _numeric_advisory(adv_id: str) -> int:
    """Parse the numeric part of an advisory ID for sort ordering.
    ``RLSA-2026:6266`` -> 20266266. Falls back to 0 for unparseable.
    """
    try:
        # Split PREFIX-YEAR:NUMBER -> YEAR, NUMBER
        parts = adv_id.split("-", 1)
        if len(parts) != 2:
            return 0
        year_num = parts[1]
        year, number = year_num.split(":")
        return int(year) * 10000 + int(number)
    except (ValueError, IndexError):
        return 0


async def run_vuls_scan_report(
    vuls_config_path: str = "/data/vuls/config/config.toml",
    vuls_data_dir: str = "/data/vuls",
    ssh_key_path: str = "/data/vm/gemma-forge/keys/adm-forge",
    known_hosts_path: str = "/data/vuls/config/known_hosts",
    image: str = "vuls/vuls:latest",
) -> dict:
    """Execute Vuls scan + report via Docker, return the parsed JSON result.

    Runs two sequential containers:
      1. ``vuls scan``  — populates results/<timestamp>/<host>.json
      2. ``vuls report --format-json`` — enriches with CVE data

    Returns the parsed JSON from the host's result file. Raises
    ``RuntimeError`` on scan failure or missing result file.
    """
    # Phase 1: scan
    scan_cmd = [
        "docker", "run", "--rm", "--network", "host",
        "-v", f"{vuls_data_dir}:/vuls",
        "-v", f"{ssh_key_path}:/root/.ssh/id_rsa:ro",
        "-v", f"{known_hosts_path}:/root/.ssh/known_hosts:ro",
        image, "scan", f"-config={vuls_config_path.replace(vuls_data_dir, '/vuls')}",
    ]
    logger.info("vuls: scan starting")
    proc = await asyncio.create_subprocess_exec(
        *scan_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"vuls scan failed (rc={proc.returncode}): {stdout.decode('utf-8', errors='replace')[:500]}")
    logger.info("vuls: scan completed")

    # Phase 2: report
    report_cmd = [
        "docker", "run", "--rm", "--network", "host",
        "-v", f"{vuls_data_dir}:/vuls",
        image, "report",
        f"-config={vuls_config_path.replace(vuls_data_dir, '/vuls')}",
        "-format-json",
    ]
    proc = await asyncio.create_subprocess_exec(
        *report_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"vuls report failed (rc={proc.returncode}): {stdout.decode('utf-8', errors='replace')[:500]}")
    logger.info("vuls: report completed")

    # Phase 3: find the latest result JSON
    results_root = Path(vuls_data_dir) / "results"
    if not results_root.is_dir():
        raise RuntimeError(f"vuls: results dir missing at {results_root}")

    # Vuls writes to results/<timestamp>/<host>.json; latest timestamp wins.
    timestamped = sorted(
        (d for d in results_root.iterdir() if d.is_dir() and d.name[0].isdigit()),
        key=lambda d: d.name,
    )
    if not timestamped:
        raise RuntimeError(f"vuls: no timestamped result dirs in {results_root}")
    latest_dir = timestamped[-1]

    json_files = list(latest_dir.glob("*.json"))
    if not json_files:
        raise RuntimeError(f"vuls: no JSON files in {latest_dir}")
    # If multiple hosts, pick the mission-app one by convention
    result_file = json_files[0]
    if len(json_files) > 1:
        for f in json_files:
            if "mission" in f.name:
                result_file = f
                break

    with open(result_file) as f:
        return json.load(f)
