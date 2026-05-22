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
    """Pull per-rule {title, description, oval_criteria} from the SCAP datastream.

    Approach: fetch the datastream file once via SSH (~27 MB), parse
    locally with xml.etree. Faster than per-rule xmllint calls on the
    VM (which would do 250+ round-trips) and avoids gawk-specific
    syntax that varies between systems.

    Returns a dict keyed by full rule_id mapping to::

        {
            "title": "...",
            "description": "...",            # natural-language spec
            "oval_criteria": "...",          # DEF-28-deeper: the
                                             # machine-checkable OVAL
                                             # criteria as a rendered
                                             # nested bullet list. The
                                             # description says what to
                                             # do; the criteria say what
                                             # the scanner verifies.
                                             # Empty string if rule has
                                             # no OVAL definition or
                                             # the criteria are bare.
            "source": "<datastream path>",
        }

    Failure-safe: returns ``{}`` on any error.
    """
    try:
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

    # --- Index OVAL definitions by id for fast lookup later -----------------
    # OVAL definitions are embedded in the datastream (SCAP source-1.2 format)
    # under their own component element. We pre-walk and index them so the
    # XCCDF Rule iteration below can pull criteria by check-content-ref name.
    oval_defs: dict[str, ET.Element] = {}
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1] if "}" in elem.tag else elem.tag
        if tag != "definition":
            continue
        did = elem.get("id", "")
        if did.startswith("oval:"):
            oval_defs[did] = elem

    # --- Walk XCCDF Rules ----------------------------------------------------
    out: dict[str, dict] = {}
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1] if "}" in elem.tag else elem.tag
        if tag != "Rule":
            continue
        rid = elem.get("id", "")
        if not rid.startswith("xccdf_org.ssgproject.content_rule_"):
            continue
        title = ""
        desc = ""
        oval_ref_id = ""
        for child in elem:
            ctag = child.tag.rsplit("}", 1)[-1] if "}" in child.tag else child.tag
            if ctag == "title" and not title:
                title = (child.text or "").strip()
            elif ctag == "description" and not desc:
                desc_parts = list(child.itertext())
                desc = " ".join(p for p in desc_parts if p).strip()
                desc = re.sub(r"\s+", " ", desc)
                if len(desc) > 1500:
                    desc = desc[:1500].rstrip() + "..."
            elif ctag == "check":
                # Find an OVAL check-content-ref. XCCDF rules often have
                # multiple <check> elements (OVAL, OCIL); we only want OVAL.
                for sub in child.iter():
                    stag = sub.tag.rsplit("}", 1)[-1] if "}" in sub.tag else sub.tag
                    if stag == "check-content-ref":
                        name = sub.get("name", "")
                        if name.startswith("oval:") and not oval_ref_id:
                            oval_ref_id = name
        if not title or not desc:
            continue
        oval_criteria = ""
        if oval_ref_id and oval_ref_id in oval_defs:
            oval_criteria = _render_oval_criteria(oval_defs[oval_ref_id])
        out[rid] = {
            "title": title,
            "description": desc,
            "oval_criteria": oval_criteria,
            "source": datastream,
        }
    return out


def _render_oval_criteria(definition: "ET.Element") -> str:
    """Render an OVAL <definition>'s <criteria> tree as nested bullets.

    DEF-28-deeper: the natural-language XCCDF description says what to
    do; the OVAL criteria say what the scanner *verifies*. The criteria
    tree is the load-bearing signal for the scanner-gap pattern from
    journey/38.7 — rules where the Worker did the right thing
    semantically but missed a specific check the scanner applies.

    Example output (mount_option_boot_nosuid)::

        ALL of:
          - ANY of:
            - nosuid on /boot
            - NOT: /boot does not exist
          - ANY of:
            - nosuid on /boot in /etc/fstab
            - NOT: /boot does not exist in /etc/fstab

    Returns empty string if the definition has no usable criteria or
    if all criterion elements lack ``comment`` attributes. Falls back
    silently — the description-only DEF-28 path still works.
    """
    import xml.etree.ElementTree as ET

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag

    def _walk(node: ET.Element, depth: int) -> list[str]:
        lines: list[str] = []
        ltag = _local(node.tag)
        indent = "  " * depth
        if ltag == "criteria":
            op = node.get("operator", "AND").upper()
            label = "ALL of:" if op == "AND" else "ANY of:" if op == "OR" else f"{op} of:"
            negate = node.get("negate", "false").lower() == "true"
            if negate:
                label = f"NOT ({label})"
            lines.append(f"{indent}- {label}" if depth > 0 else f"{label}")
            for child in node:
                ctag = _local(child.tag)
                if ctag in ("criteria", "criterion"):
                    lines.extend(_walk(child, depth + 1))
        elif ltag == "criterion":
            comment = node.get("comment", "").strip()
            test_ref = node.get("test_ref", "")
            negate = node.get("negate", "false").lower() == "true"
            if comment:
                text = comment
            elif test_ref:
                # Bare reference — at least surface the test id so a
                # downstream consumer can drill in later.
                text = f"test: {test_ref}"
            else:
                return lines  # nothing useful here
            if negate:
                text = f"NOT: {text}"
            lines.append(f"{indent}- {text}")
        return lines

    criteria = None
    for child in definition:
        if _local(child.tag) == "criteria":
            criteria = child
            break
    if criteria is None:
        return ""
    rendered = "\n".join(_walk(criteria, 0))
    return rendered.strip()
