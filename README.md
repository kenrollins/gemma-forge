# GemmaForge

> Air-gapped, agentic infrastructure remediation on the Dell PowerEdge XR7620.
> Gemma 4 + vLLM + Google ADK Ralph loops.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Status: Phase 0](https://img.shields.io/badge/status-Phase_0_scaffold-orange.svg)](#-current-status)
[![Compose v2](https://img.shields.io/badge/compose-v2_(podman_%26_docker)-informational.svg)](docs/adr/0003-podman-primary-docker-compatible.md)

> ⚠️ **Status: Phase 0 — repository scaffold.** This README documents
> the **target architecture**. Implementation lands phase by phase.
> See [Current status](#-current-status) and [Phases / Roadmap](#-phases--roadmap)
> below for what is actually wired up today versus what is being built
> toward.

---

## What this is

**GemmaForge is a Federal-leaning open-source reference build for
sovereign edge AI on the Dell PowerEdge XR7620** intended to accompany
an upcoming whitepaper. It combines:

- **Hardware** — A Dell PowerEdge XR7620 (2× Intel Xeon, 4× NVIDIA L4
  24GB), the only ruggedized 2U chassis built for this workload at the
  tactical edge. No NVLink — the inference architecture is designed
  for per-GPU fault isolation, not assumed connectivity.
- **Models** — [Gemma 4](https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/)
  (released 2026-04-02). Architect/Worker share a Gemma 4 31B-IT
  engine on `tp=2`; Auditor uses Gemma 4 E4B; Sentry uses Gemma 4 E2B.
  We follow the official [vLLM Gemma 4 recipe](https://docs.vllm.ai/projects/recipes/en/latest/Google/Gemma4.html)
  verbatim.
- **Inference** — [NVIDIA Triton Inference Server](https://github.com/triton-inference-server/server)
  with the [vLLM backend](https://github.com/triton-inference-server/vllm_backend)
  in EXPLICIT model control mode. Triton runs as a **shared host
  service** at `/data/triton/`, not bundled in this demo. Multiple
  demo projects can be clients of one common model director and the
  operator can swap mission model sets at runtime — *Ollama-style
  flexibility on top of vLLM-grade serving.*
- **Agent harness** — [Google ADK](https://github.com/google/adk-python)
  `LoopAgent` with four roles (Architect, Worker, Auditor, Sentry).
  The headline behavior is the **Ralph loop**: Fail → Revert → Retry.
  The loop terminates only when the mission succeeds *and* the
  mission app is still healthy.
- **First skill** — Autonomous DISA STIG remediation against a Rocky
  Linux 9 target VM running an Nginx + Postgres "mission app." The
  agent must keep the app alive while it remediates the host —
  failure to do so triggers an automatic revert.
- **Frontend** — Polished Next.js 14 dashboard backed by FastAPI +
  WebSockets, showing live GPU meters, the agent thought stream, and
  the Fail → Revert → Retry audit trail in real time.
- **Observability** — OpenTelemetry primary (the Federal observability
  standard), Langfuse secondary (the LLM-native UI on top). Spans are
  emitted once from the harness; both backends consume them.

We don't demo *success.* We demo *resilience.*

---

## 🎯 Why this exists

Three things, all at once, on one box:

1. **Show what the Dell XR7620 actually does** at the tactical edge
   when fed real models. Most edge AI demos run a single quantized
   chatbot. We run four roles concurrently across four L4s and put
   them through real autonomous infrastructure operations.
2. **Show what Ralph-loop persistence looks like** when an agent has
   to recover from its own mistakes — not first-try success, but
   real Fail → Revert → Retry on a target host where the mission app
   matters more than the mission.
3. **Show what a sovereign, air-gapped, accountable agentic harness
   looks like** when every model, every prompt, and every change is
   logged, reversible, and free of vendor phone-home.

This is a Federal-facing reference build. The choices, trade-offs, and
honest limits are documented as ADRs (see below) and feed an upcoming
whitepaper.

---

## 🏛 Key architectural decisions

The full set lives in [`docs/adr/`](docs/adr/). The decisions a
Federal evaluator will care about most:

| # | Decision | Why |
|---|---|---|
| [0014](docs/adr/0014-triton-vllm-director-shared-host-service.md) | **Triton-managed vLLM director as a shared host service** at `/data/triton/`, not bundled per demo | The XR7620 is a multi-demo host. Promoting the inference layer to a host-level shared service means one model catalog serves any demo as a client, and operators can swap mission model sets at runtime without redeploying containers. The L4 warm-up becomes part of the show, not a backstage step. (Supersedes ADR-0001.) |
| [0013](docs/adr/0013-one-triton-per-l4-no-nvlink.md) | **One Triton process per L4** (plus one wide Triton spanning GPUs 0+1 for the `tp=2` Gemma 4 31B-IT engine) | Forced by a real Triton vLLM-backend GPU-selection bug AND endorsed by NVIDIA's own Triton FAQ. Fault isolation by construction: a wedged GPU takes down only its own systemd unit. No NVLink dependency. |
| [0015](docs/adr/0015-gemma-4-model-lineup.md) | **Gemma 4 lineup follows the official vLLM recipe verbatim** | Architect and Worker share the 31B-IT engine (sequential in the loop), Auditor uses E4B, Sentry uses E2B. "We deploy Gemma 4 the way Google and vLLM ship it" is the strongest possible answer to a Federal evaluator. |
| [0002](docs/adr/0002-google-adk-loopagent.md) | **Google ADK `LoopAgent`** for orchestration | Native loop primitive matches the Ralph pattern; explicit termination predicates; multi-agent role split maps cleanly onto the four GPU-pinned models. Apache 2.0, self-hostable, no SaaS dependency. |
| [0004](docs/adr/0004-opentofu-not-terraform.md) | **OpenTofu (not Terraform)** with the libvirt provider for VM provisioning | Apache 2.0, Linux Foundation governance, sidesteps HashiCorp BSL friction with Federal legal teams. Drop-in compatible with Terraform if a customer prefers it. |
| [0005](docs/adr/0005-rocky-9-as-rhel-stand-in.md) | **Rocky Linux 9** as the target OS | Binary-compatible RHEL 9 stand-in. Free, mirrorable, and the [DISA RHEL 9 STIG](https://public.cyber.mil/stigs/) content applies bit-for-bit. |
| [0006](docs/adr/0006-disa-stig-profile-not-cis.md) | **DISA STIG profile** (not CIS) for the first remediation skill | The audience is DoD-adjacent. The OpenSCAP scan invocation pins the profile ID `xccdf_org.ssgproject.content_profile_stig` explicitly. |
| [0003](docs/adr/0003-podman-primary-docker-compatible.md) | **Compose v2 spec** so the same file works on Docker (reference host runtime) and Podman (Federal-recommended for customer hosts) | One file, two runtimes. No migration pain on the reference host; no compose-file duplication for downstream customers. |

---

## 🏗 Architecture (target)

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Dell PowerEdge XR7620 — 2× Xeon, 4× NVIDIA L4 24GB                     │
│                                                                          │
│  ┌────────────────────────────  HOST SERVICES  ────────────────────────┐│
│  │                                                                     ││
│  │  /data/triton/   ← shared inference director (NEW)                 ││
│  │     models/                                                          ││
│  │       gemma4-31b-it/   gemma4-e4b/   gemma4-e2b/   …more on disk…  ││
│  │                                                                     ││
│  │     systemd:                                                        ││
│  │       triton@wide-01     CUDA_VISIBLE_DEVICES=0,1   tp=2  ──┐      ││
│  │       triton@2           CUDA_VISIBLE_DEVICES=2     tp=1   │      ││
│  │       triton@3           CUDA_VISIBLE_DEVICES=3     tp=1   │      ││
│  │                                          (all EXPLICIT mode)        ││
│  │                                                                     ││
│  │  /data/vm/gemma-forge/   ← libvirt state for the target VM(s)      ││
│  │  /data/docker/           ← existing Docker daemon (untouched)       ││
│  │  /data/code/gemma-forge/ ← THIS REPO                                ││
│  └─────────────────────────────────────────────────────────────────────┘│
│                                                                          │
│  ┌─────────────────  GEMMAFORGE CLIENT-SIDE STACK  ─────────────────┐  │
│  │  (docker-compose.yml inside /data/code/gemma-forge/)              │  │
│  │                                                                   │  │
│  │  forge-api  (FastAPI + WebSockets)  ─┐                            │  │
│  │      │                                ├──→ Triton director        │  │
│  │      │                                └──→ Target VM via SSH      │  │
│  │      ▼                                                             │  │
│  │  forge-ui  (Next.js 14 dashboard)                                  │  │
│  │                                                                   │  │
│  │  jaeger    (OTel traces, Federal-standard)                         │  │
│  │  langfuse  (LLM-native trace UI)                                   │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌─────────────────────  TARGET VM (Phase 2)  ───────────────────────┐ │
│  │  Rocky Linux 9 + DISA STIG content + Nginx/Postgres mission app   │ │
│  │  Provisioned by OpenTofu + libvirt; snapshot-resettable           │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

The key insight: **the inference layer is a host service, not part of
this demo**. GemmaForge is a *client* of the Triton director. So is
every future demo on this box.

---

## 🚦 Current status

**Phase 0 — repository scaffold.** What exists today:

- ✅ Repository structure, license, package layout
- ✅ Architecture Decision Records 0001–0006, 0013–0015
- ✅ Compose v2 client-side skeleton (`docker-compose.yml`)
- ✅ CI workflow stub (lint, type-check, smoke test, compose validation)
- ✅ Issue / PR templates
- ✅ Python package skeleton (`gemma_forge.cli` placeholder)

What does **not** exist yet — and is being built phase by phase:

- ⏳ The Triton director under `/data/triton/` (Phase 0.5)
- ⏳ The four-GPU inference plane with Gemma 4 (Phase 1)
- ⏳ The target VM provisioned via OpenTofu (Phase 2)
- ⏳ The Ralph loop harness (Phase 3)
- ⏳ The skills system (Phase 4)
- ⏳ Observability backends wired in (Phase 5)
- ⏳ The web frontend (Phase 6)
- ⏳ Supply-chain hardening: SBOM, signing, OpenSSF Scorecard (Phase 7)
- ⏳ Air-gap CI test (Phase 8)
- ⏳ Demo polish, runbook, dress rehearsal (Phase 9)

---

## 🛣 Phases / roadmap

| Phase | Focus | Status |
|---|---|---|
| 0 | Repo scaffolding + initial ADRs + initial commit | 🔧 in progress |
| 0.5 | Host prep (libvirt, OpenTofu, Triton director under `/data/triton/`) | ⏳ pending |
| 1 | Inference plane: Triton + vLLM-backend + EXPLICIT, Gemma 4 loaded, day-one validation gates passed | ⏳ pending |
| 2 | Target VM via OpenTofu + libvirt + cloud-init + mission app | ⏳ pending |
| 3 | Ralph loop core: Architect/Worker/Auditor/Sentry, Fail → Revert → Retry first | ⏳ pending |
| 4 | Skills system extraction (folder-manifest + optional plugin escape hatch) | ⏳ pending |
| 5 | Observability: OTel primary, Langfuse secondary | ⏳ pending |
| 6 | Web frontend: FastAPI + Next.js 14 dashboard | ⏳ pending |
| 7 | Supply chain: SBOM (Syft), image signing (Cosign), OpenSSF Scorecard | ⏳ pending |
| 8 | Air-gap CI test (egress firewalled to localhost + libvirt subnet) | ⏳ pending |
| 9 | Demo polish, runbook, on-stage dress rehearsal | ⏳ pending |

---

## 🚀 Quick start

> **Today (Phase 0):** Cloning and exploring works. Nothing actually
> runs end-to-end yet — that lands in Phase 1+. The commands below
> are the *target* quick-start, included so contributors and
> evaluators can see where this is going.

```bash
# Clone (you are here)
git clone https://github.com/kenrollins/gemma-forge.git
cd gemma-forge

# Today: install the Python package and run the smoke test
make install
make lint
make test

# Phase 0.5+ (not yet wired):
# 1. Stand up the host's Triton director (one-time, host-level)
make triton-director-up        # systemd units under /data/triton/

# 2. Provision the target VM (per-demo)
make vm-up                     # OpenTofu + libvirt + cloud-init

# 3. Start the GemmaForge client-side stack
docker compose up -d           # observability + FastAPI + UI

# 4. Open the dashboard, pick a skill, watch the L4s warm up
open http://localhost:3000
```

---

## 📚 Documentation

- **Architecture Decision Records** — [`docs/adr/`](docs/adr/) — every
  non-obvious technical choice with rationale, alternatives, and
  consequences. Read these before reading code.
- **Demo runbook** *(Phase 9)* — `docs/demo-runbook.md` — the exact
  commands run on stage, with timing targets.
- **Host setup** *(Phase 0.5)* — `docs/host-setup.md` — how to bring
  up the Triton director, libvirt, and OpenTofu on a fresh XR7620.
- **Adding a skill** *(Phase 4)* — `docs/adding-a-skill.md` — how to
  drop a new demo into the `skills/` directory.

---

## 🤝 Contributing

This is an open reference build. Issues and PRs welcome. Two house
rules:

1. **Document non-obvious decisions as ADRs.** Use
   [`docs/adr/template.md`](docs/adr/template.md). Federal evaluators
   read ADRs before code; we want them to see serious engineering.
2. **Don't commit anything that belongs under `/data/<service>/`** —
   no qcow2 disks, no model weights, no host-specific paths.
   Configuration goes through environment variables.

See [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md)
for the checklist.

---

## ⚖ License

[Apache License 2.0](LICENSE) — matches Gemma 4's license and is the
Federal-preferred OSS license for shareable reference implementations.

---

## 🙏 Acknowledgments

- The [Gemma team at Google](https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/)
  for shipping Gemma 4 with Day-0 vLLM support.
- The [vLLM project](https://github.com/vllm-project/vllm) for the
  inference engine that makes this entire build possible.
- The [Triton Inference Server team at NVIDIA](https://github.com/triton-inference-server/server)
  for the vLLM backend and the EXPLICIT model control pattern.
- The [Google ADK team](https://github.com/google/adk-python) for
  the `LoopAgent` primitive that maps so cleanly onto the Ralph
  loop pattern.
- The [OpenSCAP](https://www.open-scap.org/) and
  [ComplianceAsCode](https://github.com/ComplianceAsCode/content)
  projects for keeping the DISA STIG content open and current.
- [Dell Federal](https://www.dell.com/en-us/dt/industry/federal/index.htm)
  for the XR7620 hardware platform.
