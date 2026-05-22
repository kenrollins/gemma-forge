"""OpenSCAP tools for the Ralph loop — STIG scan and rescan.

Runs OpenSCAP's `oscap xccdf eval` on the target VM via SSH to discover
STIG findings. Returns structured results that the Architect agent uses
to select the next rule to remediate.

Also provides DEF-28's XCCDF rule description extractor — the
``extract_xccdf_descriptions`` helper pulls per-rule descriptions
from the on-disk SCAP datastream so the STIG skill can surface them
to the Worker's prompt. See journey/38.7 for why this exists.
"""

from __future__ import annotations

import re

from .ssh import SSHConfig, _run_ssh


async def stig_scan(
    config: SSHConfig,
    profile: str = "xccdf_org.ssgproject.content_profile_stig",
    datastream: str = "/usr/share/xml/scap/ssg/content/ssg-rl9-ds.xml",
    results_dir: str = "/tmp/gemma-forge-stig",
) -> str:
    """Run a full STIG scan on the target VM.

    Returns a summary of failing rules (rule ID + title) that the
    Architect can use to plan remediation. Limited to the first 20
    failures to keep the context manageable for the LLM.
    """
    scan_script = f"""
mkdir -p {results_dir}
oscap xccdf eval \
    --profile {profile} \
    --results {results_dir}/results.xml \
    --report {results_dir}/report.html \
    {datastream} 2>&1 || true

# Extract failing rules (exit code 2 = some rules failed, which is expected)
# Parse the XML results to get rule IDs and titles of failures
oscap xccdf generate report {results_dir}/results.xml 2>/dev/null | \
    grep -B2 'fail"' | grep -oP 'id="[^"]*"' | head -20 || \
    echo "PARSE_NOTE: grep-based extraction, may be incomplete"

# Also get a simple pass/fail summary
echo "---SUMMARY---"
grep -c 'result="pass"' {results_dir}/results.xml 2>/dev/null || echo "0"
echo "pass"
grep -c 'result="fail"' {results_dir}/results.xml 2>/dev/null || echo "0"
echo "fail"
grep -c 'result="notselected"' {results_dir}/results.xml 2>/dev/null || echo "0"
echo "notselected"
"""
    stdout, stderr, rc = await _run_ssh(config, scan_script)

    # The scan itself returns non-zero when rules fail — that's expected.
    # Parse the output to extract just the failing rules (compact format).
    # The raw output can be 70K+ chars; the LLM only needs rule IDs + titles.
    lines = stdout.replace("\r", "").split("\n")
    failing: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("Title\t"):
            title = lines[i].replace("Title\t", "").strip()
            if i + 1 < len(lines) and lines[i + 1].startswith("Rule\t"):
                rule_id = lines[i + 1].replace("Rule\t", "").strip()
                if i + 2 < len(lines) and "fail" in lines[i + 2].lower():
                    failing.append(f"- {rule_id}: {title}")
                i += 3
                continue
        i += 1

    summary = f"STIG SCAN: {len(failing)} failing rules found.\n\n"
    # Return ALL rules. ralph.py calls this directly for the initial
    # state population. The agent-facing tool wrapper (run_stig_scan
    # in ralph.py) truncates the output to fit context limits.
    if failing:
        summary += "Failing rules:\n"
        summary += "\n".join(failing)
    else:
        summary += "No failing rules — system is compliant!"

    return summary


async def stig_check_rule(
    config: SSHConfig,
    rule_id: str,
    profile: str = "xccdf_org.ssgproject.content_profile_stig",
    datastream: str = "/usr/share/xml/scap/ssg/content/ssg-rl9-ds.xml",
) -> str:
    """Re-check a single STIG rule after remediation.

    Returns "PASS" or "FAIL" for the specific rule.
    """
    check_script = f"""
oscap xccdf eval \
    --profile {profile} \
    --rule {rule_id} \
    {datastream} 2>&1 | tail -5
"""
    stdout, stderr, rc = await _run_ssh(config, check_script)

    if "pass" in stdout.lower():
        return f"RULE_CHECK: {rule_id} = PASS"
    elif "fail" in stdout.lower():
        return f"RULE_CHECK: {rule_id} = FAIL"
    else:
        return f"RULE_CHECK: {rule_id} = UNKNOWN\nOutput: {stdout.strip()}"


# DEF-28 — extract per-rule descriptions from the XCCDF datastream.
# The descriptions contain the authoritative scanner contract (file
# paths, exact directive syntax, expected string values) that the
# Worker has historically been guessing at from rule titles alone.
# See journey/38.7 for the discovery context.
#
# We use a single ssh+awk pass over the 27 MB datastream because
# loading it into Python locally would require shipping the file
# off-VM. The awk script extracts {rule_id, title, description} for
# every Rule element. ~5 seconds for the full datastream; called once
# per skill init by StigSkillRuntime.


async def extract_xccdf_descriptions(
    config: SSHConfig,
    datastream: str = "/usr/share/xml/scap/ssg/content/ssg-rl9-ds.xml",
) -> dict[str, dict]:
    """Pull per-rule {title, description} from the SCAP datastream.

    Approach: fetch the datastream file once via SSH (~27 MB), parse
    locally with xml.etree. Faster than per-rule xmllint calls on the
    VM (which would do 250+ round-trips) and avoids gawk-specific
    syntax that varies between systems.

    Returns a dict keyed by full rule_id (e.g.
    ``xccdf_org.ssgproject.content_rule_accounts_tmout``) mapping to::

        {"title": "...", "description": "...", "source": "<datastream path>"}

    Failure-safe: returns ``{}`` on any error. The caller (typically
    the STIG skill init) treats an empty dict as "no enrichment
    available" and prompts continue to work as before.
    """
    try:
        # `cat` the file via SSH; for 27 MB it's a few seconds over
        # localhost virtual networking.
        stdout, stderr, rc = await _run_ssh(config, f"cat {datastream}")
        if rc != 0 or not stdout:
            return {}
    except Exception:
        return {}

    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(stdout)
    except ET.ParseError:
        return {}

    # XCCDF uses namespaces; we match on local-name to avoid coupling
    # to the schema version.
    out: dict[str, dict] = {}
    for elem in root.iter():
        # Match <Rule id="..."> elements
        tag = elem.tag.rsplit("}", 1)[-1] if "}" in elem.tag else elem.tag
        if tag != "Rule":
            continue
        rid = elem.get("id", "")
        if not rid.startswith("xccdf_org.ssgproject.content_rule_"):
            continue
        title = ""
        desc = ""
        for child in elem:
            ctag = child.tag.rsplit("}", 1)[-1] if "}" in child.tag else child.tag
            if ctag == "title" and not title:
                title = (child.text or "").strip()
            elif ctag == "description" and not desc:
                # Description body may contain inline XHTML
                # (<code>, <pre>, <tt>, etc.). itertext() concatenates
                # all descendant text, ignoring the tags, which gives
                # us a clean human-readable description.
                desc_parts = list(child.itertext())
                desc = " ".join(p for p in desc_parts if p).strip()
                desc = re.sub(r"\s+", " ", desc)
                if len(desc) > 1500:
                    desc = desc[:1500].rstrip() + "..."
        if title and desc:
            out[rid] = {
                "title": title,
                "description": desc,
                "source": datastream,
            }
    return out
