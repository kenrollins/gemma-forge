---
id: gotcha-adk-future-annotations
type: gotcha
title: "Gotcha: ADK FunctionTool fails with `from __future__ import annotations`"
date: 2026-04-09
tags: [L4-orchestration, tool-calling, discovery]
related:
  - journey/06-tool-calling
one_line: "from __future__ import annotations turns type hints into strings at runtime, which breaks ADK's FunctionTool parser. Omit the import from any module that defines tool functions."
---

# Gotcha: ADK FunctionTool fails with `from __future__ import annotations`

## Symptom
```
ValueError: Failed to parse the parameter fix_script: 'str' of function
apply_fix for automatic function calling.
```

Works in isolation (`FunctionTool(apply_fix)._get_declaration()` → OK)
but fails inside an ADK LoopAgent.

## Root cause
`from __future__ import annotations` (PEP 563) makes ALL type
annotations lazy strings instead of actual type objects. ADK's
`function_parameter_parse_util._parse_schema_from_parameter` checks
`_is_builtin_primitive_or_compound(param.annotation)` — which returns
False for the STRING `'str'` (only matches the TYPE `str`).

The function parses fine in isolation because the test script doesn't
import the future annotation. But inside the LoopAgent, the module
that defines the tool functions has the import, so annotations are
strings at runtime.

## Fix
Remove `from __future__ import annotations` from any module that
defines functions used as ADK FunctionTools. Use `Optional[X]` from
typing instead of `X | None` syntax if needed.

## How to prevent
Add a comment at the top of tool-function modules:
```python
# NOTE: Do NOT add `from __future__ import annotations` to this module.
# ADK's FunctionTool parser requires real type objects, not strings.
```

## Environment
- google-adk 0.4.x (pre-1.0)
- Python 3.12
