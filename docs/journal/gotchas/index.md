---
title: Gotchas Overview
---

# Gotchas

Small, atomic "X breaks Y because Z" lessons that cost hours to
discover the first time. If you are building something similar, this
is where to look to save yourself the same pain.

Each gotcha is scoped to a single problem, names the symptom, the
root cause, and the fix. Most of them are one page. None of them
require reading the full journey.

## Organized by layer

### L1 — Data / Infrastructure
Target VM, libvirt, hardware, hypervisor.

- [**AppArmor + libvirt**](apparmor-libvirt.md) — virt-aa-helper
  capability requirements for custom pool paths.
- [**GRUB + ACPI/APIC**](grub-acpi-apic.md) — libvirt provider
  v0.9.7 doesn't enable these by default; the VM hangs at GRUB
  without them.
- [**Libvirt provider v0.9 API migration**](libvirt-provider-v09-migration.md)
  — every resource attribute changed from v0.7/0.8; use
  `tofu providers schema -json`, not old examples.

### L2 — Platform / MLOps
Observability, lifecycle, monitoring.

- [**OTel spanmetrics connector**](otel-spanmetrics-connector.md) —
  moved from `processor` to `connector` in recent collector
  versions; the old config silently produces no metrics.

### L3 — Model
Inference, model selection, quantization, parallelism.

- [**NVFP4 VRAM math**](nvfp4-vram-math.md) — the naive 15.5 GB
  estimate is wrong; the real footprint is 22 GB because attention
  stays in bf16.
- [**Triton + vLLM version gap**](triton-vllm-version.md) — Triton
  26.03 ships vLLM 0.17.1; Gemma 4 needs 0.19.0; upgrading inside
  the container breaks the backend.
- [**transformers + Gemma 4**](transformers-gemma4.md) — the
  `gemma4` model type isn't in transformers 4.57.6. Bake 4.58+
  into the inference container.
- [**vLLM tool-call parser**](vllm-tool-call-parser.md) — vLLM
  needs `--enable-auto-tool-choice --tool-call-parser gemma4` or
  tool calls are rejected with a 400.
- [**Nemotron tool parser**](nemotron-tool-parser.md) — Nemotron
  needs a specific parser flag set that is different from what
  the generic Hermes or llama3_json options provide.
- [**Nemotron TP tiling error**](nemotron-tp-tiling-error.md) —
  Nemotron TP=2 fails with Marlin kernel tiling errors; PP=2
  works around it.

### L4 — Orchestration
Agents, harness, Ralph loop, tool calling, memory.

- [**ADK `from __future__ import annotations`**](adk-future-annotations.md)
  — turns type hints into strings at runtime, which breaks ADK's
  FunctionTool parser. Omit the import from any module defining
  tool functions.
- [**Agent instructions and tool calling**](agent-instructions-tool-calling.md)
  — the system prompt must explicitly tell the agent to call its
  tools; otherwise the model describes what it would do in prose
  and never actually invokes them.
- [**Context window edge**](context-window-edge.md) — per-turn
  ADK session history grows when the model makes multiple tool
  calls inside a single turn. This is where overflow comes from,
  not from between-turn state.

## Related

- [Journey overview](../journey/index.md) — the chronological
  story each gotcha is a footnote to.
- [Architecture overview](../architecture/00-system-architecture.md) —
  where these gotchas fit in the layer stack.
