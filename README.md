# gemma-forge

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

![gemma-forge dashboard — live STIG run with reflexion loop visible](docs/assets/media/gemma-forge-screenshot.png)

---

## 🎥 See it run

**The harness, live, during a STIG remediation run** — Architect picks a rule, Worker applies the fix, Evaluator scans, Reflector distills a lesson on failure, repeat:

<video controls muted width="100%" poster="docs/assets/media/gemma-forge-screenshot.png">
  <source src="docs/assets/media/gemma-forge-stig-main.mp4" type="video/mp4">
  Your browser doesn't support embedded video —
  <a href="docs/assets/media/gemma-forge-stig-main.mp4">download the STIG run recording</a>.
</video>

**Cross-run memory in action** — the V2 tip store retrieving prior-run lessons, rule-prefix similarity firing, and per-(tip, rule) utility updating as evaluations come in:

<video controls muted width="100%">
  <source src="docs/assets/media/gemma-forge-stig-memory.mp4" type="video/mp4">
  Your browser doesn't support embedded video —
  <a href="docs/assets/media/gemma-forge-stig-memory.mp4">download the memory recording</a>.
</video>

---

## What this is

gemma-forge explores whether a smaller open-weights model on edge
hardware can solve real problems autonomously — if you give it the
right harness. It combines two architectures:

- [**Ralph loop persistence**](https://ghuntley.com/ralph/) — where an
  agent doesn't quit when it fails but keeps grinding, using external
  state to persist across context boundaries.
- [**Reflexion-style self-improvement**](https://arxiv.org/abs/2303.11366) —
  where each failure produces a self-critique that makes the next
  attempt smarter.

The harness is **skill-agnostic** — a core with abstract interfaces
that any use case can implement. Two skills ship today:

- **DISA STIG remediation** on Rocky Linux 9 — the hard case. 270
  rules, multi-attempt fixes, genuine reflexion cycles. 61.9% fix
  rate in Run 6 over 19 hours.
- **CVE Response** on Rocky Linux 9 — the easy case. Vuls scan +
  `dnf upgrade --advisory`, per-package-family reboot batching. 44
  of 44 advisories remediated in 35 minutes, every one first-try.

Both run on the same harness, same Gemma 4 deployment, same
four-agent reflexion loop, without modification between them.
Adding a third skill is a folder and five Python classes.

---

## 📖 Read this first

The published site is the primary entry point:

**→ [kenrollins.github.io/gemma-forge](https://kenrollins.github.io/gemma-forge/)**

It contains:

- **[Architecture Brief](https://kenrollins.github.io/gemma-forge/brief/)** —
  the one-document overview. Start here if you have 10 minutes.
- **[Journey](https://kenrollins.github.io/gemma-forge/journal/journey/)** —
  37 chronological field notes of how this was built, with honest
  failures and discoveries. Start at
  [00 — Origin](https://kenrollins.github.io/gemma-forge/journal/journey/00-origin/)
  or jump to
  [33 — The Second Skill: CVE Response](https://kenrollins.github.io/gemma-forge/journal/journey/33-second-skill-cve-pivot/) and
  [37 — Per-Family Reboot Batching Lands](https://kenrollins.github.io/gemma-forge/journal/journey/37-per-family-reboot-batching-landed/)
  for the latest work.
- **[Failure Modes](https://kenrollins.github.io/gemma-forge/journal/architecture/01-reflexive-agent-harness-failure-modes/)** —
  a project-agnostic taxonomy of seven failure modes in reflexive
  agent harnesses, with prescribed mechanisms for each.
- **[Gotchas](https://kenrollins.github.io/gemma-forge/journal/gotchas/)** —
  atomic "X breaks Y because Z" lessons that cost hours to discover.

---

## 🏗 The stack

| Layer | Component | Role |
|---|---|---|
| **5 — Application** | STIG Skill + CVE Response Skill + Dashboard | What the user sees |
| **4 — Orchestration** | Ralph Loop + ADK + Skills + V2 Memory (Postgres + Neo4j/Graphiti) | Agents, reflexion, cross-run learning via structured tips |
| **3 — Model** | Gemma 4 31B bf16 + vLLM 0.19.0 | Full precision, TP=4 across 4 L4s, ~14 tok/s |
| **2 — Platform** | OTel + Jaeger + Prometheus + Grafana | Observability, no commercial LLM proxy |
| **1 — Infrastructure** | Dell PowerEdge XR7620 + 4× NVIDIA L4 + libvirt | Rugged 2U edge server, air-gappable |

The harness operates on five abstract interfaces (WorkQueue,
Executor, Evaluator, Checkpoint, SkillRuntime) so the core loop is
completely decoupled from any specific use case. Memory is tiered:
working (per-attempt), episodic (per-item), semantic (per-run), and
persistent (cross-run as structured tips with per-(tip, rule)
utility tracking, rule-prefix similarity retrieval, and history-
based eviction — see
[ADR-0016](https://kenrollins.github.io/gemma-forge/adr/0016-graphiti-neo4j-postgres-memory-stack/)).
The harness's extension points have grown alongside the skill
portfolio: CVE added `FailureMode.NEEDS_REBOOT`,
`DeferredItemOutcome` for per-item post-loop attribution, and an
`EmitEvent` callback for long-running skill phases, all without
touching the Ralph loop itself.

---

## 🏃 Running it yourself

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

# Kick off a run — both skills are available
./bin/forge run stig-rhel9      # the hard case
./bin/forge run cve-response    # the easy case

# Watch it go
open http://localhost:3333     # dashboard
./bin/forge logs               # tail the current run
```

See [host-setup.md](docs/host-setup.md) for the full walkthrough and
[adding-a-skill](https://kenrollins.github.io/gemma-forge/adding-a-skill/)
for how to author a new skill.

---

## 🧪 Tests

212 property tests across the harness helpers, verdict parsing,
fault injection, task graph scheduling, Protocol interface contracts,
memory store (both V1 legacy and V2), ordering predicates, reflector
parser, retrieval, eviction, and integration.

```bash
# Fast tests (no LLM, no VM): ~13 seconds
pytest tests/ -v --ignore=tests/test_agent_behavior.py \
                 --ignore=tests/test_target_layer.py \
                 --ignore=tests/test_loop_integration.py
```

---

## 🛠 How this was built

An **agentic coding workflow** — a human operator making all
architectural and strategic decisions, with an AI coding assistant
contributing implementation velocity, test coverage, and documentation.
The pattern is vendor-neutral and described in
[journey/16](https://kenrollins.github.io/gemma-forge/journal/journey/16-agentic-coding-as-a-method/).

---

## ⚖ License

[Apache License 2.0](LICENSE) — matches Gemma 4's license.

## 📬 Contact

Discussion through
[GitHub issues](https://github.com/kenrollins/gemma-forge/issues).
For Dell hardware questions, please work through your Dell account
team — this project does not speak for Dell.

## 🙏 Acknowledgments

- The **Gemma team at Google** for Gemma 4 with native function calling
  and Day-0 vLLM support.
- The **vLLM project** for the inference engine that makes edge-AI work.
- The **Google ADK team** for the agent development kit.
- The **OpenSCAP and ComplianceAsCode** projects for DISA STIG content.
- The **OpenTelemetry** community for Federal-credible observability.
- **Dell Federal** for the hardware that made this exploration possible.
