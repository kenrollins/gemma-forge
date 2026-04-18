"""Tests for gemma_forge.memory.reflector_parser.

Parsing is the failure-tolerant boundary between Reflector LLM output
and the V2 tips table. Every "drop silently" branch has a test — a
malformed block must never break the loop.
"""
from gemma_forge.memory.reflector_parser import (
    extract_json_object,
    parse_tips_json,
)


# -- extract_json_object ------------------------------------------------


def test_extract_json_object_simple():
    assert extract_json_object('before {"a": 1} after') == '{"a": 1}'


def test_extract_json_object_nested():
    src = 'x {"a": {"b": 2}, "c": [1, 2]} y'
    assert extract_json_object(src) == '{"a": {"b": 2}, "c": [1, 2]}'


def test_extract_json_object_string_contains_braces():
    # A } inside a string must not close the object early.
    src = '{"text": "use augenrules }"}'
    assert extract_json_object(src) == src


def test_extract_json_object_escaped_quote_in_string():
    src = '{"text": "say \\"hi\\"", "n": 1}'
    assert extract_json_object(src) == src


def test_extract_json_object_no_brace():
    assert extract_json_object("no braces here") is None


def test_extract_json_object_start_offset():
    src = '{"first": 1} and then {"second": 2}'
    assert extract_json_object(src, start_offset=len('{"first": 1}')) == '{"second": 2}'


# -- parse_tips_json: happy path ----------------------------------------


SAMPLE_OUTPUT = """REFLECTION:
Pattern identified: Raw auditctl fails because kernel is in immutable mode.

BANNED: auditctl\\s+-a
PREFERRED: use augenrules --load + reboot
LESSON: Rocky 9 audit rules require a reboot when auditd is immutable.
DISTILLED: reboot required when auditd is immutable

TIPS_JSON: {"tips_to_save": [
  {"text": "On Rocky 9 with auditd in immutable mode, new rules require a reboot to take effect.",
   "tip_type": "strategy",
   "mechanism": "Immutable mode freezes the kernel audit subsystem; rules staged via augenrules only take effect after auditctl re-reads them at boot.",
   "trigger_conditions": ["audit rule modification", "auditd immutable"],
   "application_context": ["audit", "audit_rules_*"]},
  {"text": "auditctl -a ignored when auditd is immutable.",
   "tip_type": "warning",
   "mechanism": "Immutable mode rejects runtime audit rule additions; auditctl returns success-looking output but no rule is registered.",
   "trigger_conditions": ["auditd immutable"],
   "application_context": ["audit"]}
]}"""


def test_parse_tips_json_happy_path():
    tips = parse_tips_json(SAMPLE_OUTPUT)
    assert len(tips) == 2
    assert tips[0]["tip_type"] == "strategy"
    assert tips[0]["text"].startswith("On Rocky 9")
    assert tips[0]["mechanism"].startswith("Immutable mode freezes")
    assert tips[0]["trigger_conditions"] == ["audit rule modification", "auditd immutable"]
    assert tips[0]["application_context"] == ["audit", "audit_rules_*"]
    assert tips[1]["tip_type"] == "warning"
    assert "Immutable mode rejects" in tips[1]["mechanism"]


# -- parse_tips_json: tolerant to malformed / missing blocks -----------


def test_parse_tips_json_no_marker():
    assert parse_tips_json("just some analysis with no TIPS_JSON block") == []


def test_parse_tips_json_marker_but_no_object():
    assert parse_tips_json("TIPS_JSON: no braces here") == []


def test_parse_tips_json_empty_input():
    assert parse_tips_json("") == []
    assert parse_tips_json(None) == []  # type: ignore[arg-type]


def test_parse_tips_json_malformed_json():
    src = 'TIPS_JSON: {"tips_to_save": [{"text": "x", "tip_type": strategy,}]}'  # unquoted strategy
    assert parse_tips_json(src) == []


def test_parse_tips_json_empty_list_is_ok():
    tips = parse_tips_json('TIPS_JSON: {"tips_to_save": []}')
    assert tips == []


def test_parse_tips_json_missing_tips_to_save_key():
    tips = parse_tips_json('TIPS_JSON: {"other_key": 1}')
    assert tips == []


# -- parse_tips_json: per-tip validation ---------------------------------


def test_parse_tips_json_skips_invalid_tip_type():
    src = '''TIPS_JSON: {"tips_to_save": [
        {"text": "good one", "tip_type": "strategy", "mechanism": "because X"},
        {"text": "bad type", "tip_type": "totally-made-up", "mechanism": "because Y"},
        {"text": "also good", "tip_type": "warning", "mechanism": "because Z"}
    ]}'''
    tips = parse_tips_json(src)
    assert len(tips) == 2
    assert [t["tip_type"] for t in tips] == ["strategy", "warning"]


def test_parse_tips_json_skips_missing_text():
    src = '''TIPS_JSON: {"tips_to_save": [
        {"tip_type": "strategy", "mechanism": "m"},
        {"text": "", "tip_type": "strategy", "mechanism": "m"},
        {"text": "   ", "tip_type": "strategy", "mechanism": "m"},
        {"text": "real one", "tip_type": "recovery", "mechanism": "because valid"}
    ]}'''
    tips = parse_tips_json(src)
    assert len(tips) == 1
    assert tips[0]["text"] == "real one"


def test_parse_tips_json_skips_missing_mechanism():
    """Run 6 adds required mechanism field — tips missing it are dropped."""
    src = '''TIPS_JSON: {"tips_to_save": [
        {"text": "no mechanism", "tip_type": "strategy"},
        {"text": "empty mechanism", "tip_type": "strategy", "mechanism": ""},
        {"text": "whitespace mechanism", "tip_type": "strategy", "mechanism": "   "},
        {"text": "null mechanism", "tip_type": "strategy", "mechanism": null},
        {"text": "has mechanism", "tip_type": "strategy", "mechanism": "because valid"}
    ]}'''
    tips = parse_tips_json(src)
    assert len(tips) == 1
    assert tips[0]["text"] == "has mechanism"
    assert tips[0]["mechanism"] == "because valid"


def test_parse_tips_json_normalizes_string_list_fields():
    # Model sometimes emits a single string instead of a list
    src = '''TIPS_JSON: {"tips_to_save": [
        {"text": "x", "tip_type": "strategy", "mechanism": "m",
         "trigger_conditions": "audit modification", "application_context": "audit"}
    ]}'''
    tips = parse_tips_json(src)
    assert tips[0]["trigger_conditions"] == ["audit modification"]
    assert tips[0]["application_context"] == ["audit"]


def test_parse_tips_json_handles_null_trigger_conditions():
    src = '''TIPS_JSON: {"tips_to_save": [
        {"text": "x", "tip_type": "strategy", "mechanism": "m", "trigger_conditions": null}
    ]}'''
    tips = parse_tips_json(src)
    assert tips[0]["trigger_conditions"] is None


def test_parse_tips_json_lowercases_tip_type():
    src = 'TIPS_JSON: {"tips_to_save": [{"text": "x", "tip_type": "STRATEGY", "mechanism": "m"}]}'
    tips = parse_tips_json(src)
    assert tips[0]["tip_type"] == "strategy"


def test_parse_tips_json_tolerates_case_in_marker():
    src = 'tips_json: {"tips_to_save": [{"text": "x", "tip_type": "strategy", "mechanism": "m"}]}'
    tips = parse_tips_json(src)
    assert len(tips) == 1
