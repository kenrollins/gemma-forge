# GemmaForge

> An exploration of Ralph loop architecture and Gemma 4 at the edge — building your own agentic harness, from scratch.
>
> By **Ken Rollins**, Chief AI Technology Strategist in Dell Federal.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Site](https://img.shields.io/badge/site-kenrollins.github.io/gemma--forge-blue.svg)](https://kenrollins.github.io/gemma-forge/)
[![Built with](https://img.shields.io/badge/built_with-agentic_coding_workflow-indigo.svg)](https://kenrollins.github.io/gemma-forge/journal/journey/16-agentic-coding-as-a-method/)

> [!IMPORTANT]
> **This is a personal exploration project, not an official Dell product, reference
> architecture, or supported offering.** Views and technical findings are the author's
> own. Read the full [disclaimer](https://kenrollins.github.io/gemma-forge/about/)
> before extracting anything as Dell policy.

---

## What this is

GemmaForge is a personal exploration into two things in combination:

- **Ralph loop architecture** as a pattern for autonomous-but-accountable agent
  systems that grind through problems with persistence, learning from each failure.
- **Running Gemma 4 models at the edge** on commodity Dell hardware, without
  commercial agentic frameworks sitting between the harness and the infrastructure.

**DISA STIG remediation** was chosen as the anchor use case because it exercises
the interesting parts of the architecture — persistence, revert-on-failure,
verifiable outcomes, real target-system side effects — but the patterns
documented here apply to a wide range of problem spaces. STIG is the witness,
not the point.

**Goal**: share what we learned so other presales engineers, SI partners, and
technical evaluators can build similar systems faster on their own hardware of
choice.

## What this is **not**

- **Not a product.** Nothing is for sale. Nothing requires a subscription.
- **Not a Dell reference architecture.** No official review, no Dell Marketing
  endorsement, no commercial warranty. The author works at Dell Federal and
  uses Dell hardware because that is the lab environment; this does not
  represent a Dell position.
- **Not a vendor-lock-in framework.** Every component is open source. Every
  line of code is in this repo. The patterns transfer to whatever hardware,
  model, and tooling you prefer.
- **Not a showcase demo.** We don't hide failures. The
  [failure modes document](docs/journal/architecture/01-reflexive-agent-harness-failure-modes.md)
  exists specifically to name the things that went wrong and what we did
  about them.

---

## 📖 Read this first

The published site is the primary entry point:

**→ [kenrollins.github.io/gemma-forge](https://kenrollins.github.io/gemma-forge/)**

It contains:

- **[System Architecture](https://kenrollins.github.io/gemma-forge/journal/architecture/00-system-architecture/)** —
  the 5-layer enterprise AI stack map with GemmaForge's components at each
  layer, industry alternatives (open-source and enterprise) for anyone who
  can't use what we used, and the key architectural patterns at each layer.
- **[Journey](https://kenrollins.github.io/gemma-forge/journal/journey/)** —
  26 first-person field notes of how this was built, chronologically.
  Start with [00 — Origin](https://kenrollins.github.io/gemma-forge/journal/journey/00-origin/)
  or jump to whatever catches your eye.
- **[Failure Modes](https://kenrollins.github.io/gemma-forge/journal/architecture/01-reflexive-agent-harness-failure-modes/)** —
  a project-agnostic taxonomy of six failure modes in reflexive agent
  harnesses, each with a prescribed harness mechanism. The contribution
  artifact that came out of this work.
- **[Gotchas](https://kenrollins.github.io/gemma-forge/journal/gotchas/)** —
  small atomic "X breaks Y because Z" lessons that cost hours to discover.
- **[ADRs](https://kenrollins.github.io/gemma-forge/adr/0001-vllm-not-nim-or-ollama/)** —
  architecture decision records for every non-obvious technical choice.

If you only have 15 minutes, read:
[14 — The Overnight Run](https://kenrollins.github.io/gemma-forge/journal/journey/14-overnight-run-findings/)
and the [Failure Modes](https://kenrollins.github.io/gemma-forge/journal/architecture/01-reflexive-agent-harness-failure-modes/)
document. That's the load-bearing pair.

---

## 🏗 What it actually is, technically

| Layer | Component | Role |
|---|---|---|
| **5 — Application** | STIG Remediation Skill + GemmaForge Dashboard | What the user sees |
| **4 — Orchestration** | Ralph Loop Harness + Google ADK | Agents, reflexion loop, memory, tool calling |
| **3 — Model** | Gemma 4 31B bf16 + vLLM 0.19.0 | Full precision, TP=4 across 4 L4s |
| **2 — Platform/MLOps** | OpenTelemetry + Jaeger + Prometheus + Grafana | Observability, no commercial LLM proxy |
| **1 — Infrastructure** | Dell PowerEdge XR7620 + 4× NVIDIA L4 + libvirt + Rocky 9 | The lab. Other Dell edge platforms also apply. |

The harness is about ~3000 lines of Python. It uses Google ADK for the
per-agent-turn machinery but drives the outer reflexion loop directly.
Memory is tiered (per-rule episodic, cross-rule semantic, per-attempt
working). Revert is snapshot-based at the hypervisor layer, not
script-based. Context is token-budgeted with priority-ordered prompt
assembly. The architect re-engages on a schedule or on plateau. Every
event is logged to a structured JSONL stream that doubles as the
test-harness ground truth.

See the [System Architecture](https://kenrollins.github.io/gemma-forge/journal/architecture/00-system-architecture/)
page for the full picture with component details and pattern drill-downs.

---

## 🎯 The Ralph loop in one sentence

**Fail → diagnose → restore → reflect → retry — and keep going until either
the rule is remediated or the wall-clock budget runs out.** Unlike the
textbook Reflexion implementations that use a fixed retry count, this loop
uses time budgets per rule and a semantic plateau detector to know when the
reflector is genuinely stuck versus when it's still learning. The architect
re-engages periodically to re-evaluate the strategy with full failure history.
When the loop gives up, it gives up with a structured reason (`time_budget`,
`retry_ceiling`, or `architect_preemptive`) that downstream tools can reason
about.

The underlying philosophy is in
[journey/13](https://kenrollins.github.io/gemma-forge/journal/journey/13-ralph-persistence-retry-budget/)
and the full v3 architecture story is in
[journey/17](https://kenrollins.github.io/gemma-forge/journal/journey/17-v3-fix-pass/).

---

## 🏃 Running it yourself

The short version:

```bash
# On a host with NVIDIA GPUs and libvirt installed
git clone https://github.com/kenrollins/gemma-forge.git
cd gemma-forge
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

# Bring up vLLM (edit infra/vllm/scripts/ for your GPU layout)
sudo systemctl start gemma-forge-gemma

# Bring up the target VM (requires libvirt + OpenTofu)
./infra/vm/scripts/vm-up.sh

# Start the services
./bin/forge up     # vLLM, FastAPI, UI

# Kick off a STIG remediation run
./bin/forge run stig-rhel9

# Watch it go
open http://localhost:3333     # dashboard
./bin/forge logs               # tail the current run
```

See [host-setup.md](docs/host-setup.md) for the full setup
walkthrough and
[adding-a-skill.md](https://kenrollins.github.io/gemma-forge/adding-a-skill/)
for how to author a new skill.

---

## 🧪 Running the tests

The test suite is organized as **property tests** across 7 tiers. See
[journey/15 — The Test as Architecture Discovery](https://kenrollins.github.io/gemma-forge/journal/journey/15-the-test-as-architecture-discovery/)
for the discipline and
[journey/15.5 — The Test Pass in Practice](https://kenrollins.github.io/gemma-forge/journal/journey/15.5-test-pass-in-practice/)
for the narrative of running all 99 of them.

```bash
# Fast tests (no LLM, no VM): ~10 seconds
pytest tests/test_harness_helpers.py tests/test_architect_verdict_parsing.py -v

# Target layer tests (needs the VM): ~4 minutes
pytest tests/test_target_layer.py -v

# Agent behavior tests (needs vLLM): ~1 minute
pytest tests/test_agent_behavior.py -v

# Fault injection (mostly mocks): ~15 seconds
pytest tests/test_harness_fault_paths.py -v

# Full integration test (real LLM + VM, ~2 minutes)
pytest tests/test_loop_integration.py -v -s --run-slow
```

---

## 🛠 How this was built

In short: an **agentic coding workflow** — a human operator paired with an
AI coding assistant, with the human making all architectural and strategic
decisions and the AI contributing implementation velocity, test coverage,
and documentation drafting. The pattern is vendor-neutral and described in
detail in
[journey/16 — Agentic Coding as a Method](https://kenrollins.github.io/gemma-forge/journal/journey/16-agentic-coding-as-a-method/).

This is worth knowing because the velocity on this project is real (~72
hours from empty scaffold to v3 with 99 property tests and a complete
journal) and the pattern that produced it is reproducible by other teams
that bring the same discipline.

---

## ⚖ License

[Apache License 2.0](LICENSE) — matches Gemma 4's license. Use it,
fork it, extract patterns from it, build on top of it. Credit where it
helps, but no obligation.

## 📬 Contact

Personal project; discussion through
[GitHub issues](https://github.com/kenrollins/gemma-forge/issues) on this
repo. For Dell hardware questions or anything that needs an official
Dell channel, please work through your existing Dell account team —
this project does not speak for Dell.

## 🙏 Acknowledgments

- The **Gemma team at Google** for shipping Gemma 4 with native function
  calling and Day-0 vLLM support.
- The **vLLM project** for the inference engine that makes edge-AI
  agent work feasible.
- The **Google ADK team** for the agent development kit that provides
  the per-agent-turn abstractions this project builds on.
- The **OpenSCAP and ComplianceAsCode** projects for keeping DISA STIG
  content open and current.
- The **OpenTelemetry** community for a Federal-credible observability
  standard that doesn't lock anyone into a vendor.
- **Dell Federal** for the hardware platform that made this exploration
  possible.
