# ADR-0017: Web tier runs under docker compose, not systemd

- **Status:** Accepted
- **Date:** 2026-04-24
- **Deciders:** Ken Rollins

## Context

GemmaForge's dashboard tier has two components: a Next.js 16 UI and a
FastAPI replay server (`web/api/serve_replay.py`) that feeds the UI
run logs and dashboard config. Early in the project both were run by
hand (`npm run dev`, `uvicorn …`) with `nohup` after each host reboot.

That's fine on a developer's laptop, but GemmaForge is mirrored to
GitHub and the repo is expected to be cloned by other engineers
evaluating the architecture. "Start the dashboard" needs to be a
one-liner that works on any Linux host with Docker, not a sequence
of manual commands that leave orphaned processes.

There was an existing precedent for systemd on this project: the vLLM
inference plane runs as `gemma-forge-gemma.service` (see
[ADR-0014](0014-triton-vllm-director-shared-host-service.md)). It
would have been the path of least resistance to drop in matching units
for the UI and API.

## Decision

The web tier runs as two Docker Compose services — `forge-api` and
`forge-ui` — under the existing `app` profile in
[docker-compose.yml](../../docker-compose.yml). Bring-up is
`make app-up` (alias for `docker compose --profile app up -d`).
Reboot recovery is handled by Docker's `restart: unless-stopped`.
No systemd units on the host.

The UI uses Next.js 16's `output: "standalone"` build mode so the
runtime image contains only the pruned `server.js` + traced
dependencies — no full `node_modules`, no `next start` wrapper. The
API image is a 3-dependency slim Python image (fastapi, uvicorn,
pyyaml); it does **not** carry the rest of the gemma-forge runtime
(no vLLM client, no Graphiti, no NVML), because the replay server
only reads JSONL + YAML from disk.

## Alternatives considered

- **systemd units on the host (the vLLM pattern).** Mirrors the
  inference plane's deployment. Rejected because it requires root to
  install (`ln -sf` into `/etc/systemd/system`, `systemctl
  daemon-reload`), is Linux/systemd-only, and hard-codes host paths
  in unit files. A colleague cloning the repo on macOS or in WSL
  could not follow the same path. The vLLM service *earns* its
  systemd unit because it has 600 s startup, 4-GPU hardware coupling,
  and weights under `/data/triton/weights/`. The web tier has none
  of that: stateless, <1 s startup, standard runtime.

- **Makefile wrapping `nohup`.** Simplest possible scheme — `make
  app-up` runs `nohup uvicorn …` and `nohup npm run dev …`. Rejected
  because PID management is flaky (PID files go stale, no health
  checks, no log rotation, no automatic restart on crash) and the
  dev server is the wrong thing to run for anyone doing more than
  quickly evaluating the repo. `make dev` is kept as a separate
  target for the foreground hot-reload editing loop; it is not the
  "start the dashboard" path.

- **Fix the stale compose stubs minimally, no dockerization.** Keep
  the services running on the host, just write a wrapper. Rejected
  for the same portability reasons as systemd: anyone cloning the
  repo would still have to set up a Python venv and Node toolchain
  on the host before the dashboard works. Containerization is the
  point — the Dockerfiles are the portable install instructions.

## Consequences

### Positive

- Single bring-up command (`make app-up`) works on any Linux host
  with Docker + Compose v2, which matches the project's stated
  Compose-first convention (see [ADR-0003](0003-podman-primary-docker-compatible.md)).
- Reboot recovery via `restart: unless-stopped`, no sudo, no
  host-level config.
- Images are small and cacheable: Next.js standalone UI image is
  ~200 MB, replay-API image is ~150 MB. Rebuilds are fast because
  `runs/` and `config/` are bind-mounted read-only, not baked in.
- The `FORGE_API_URL` env var already in `next.config.ts` becomes
  the one knob that decouples UI from API — compose sets it to
  `http://forge-api:8080` over the internal network; local `make
  dev` leaves it at the default `http://localhost:8080`.
- Port layout moves off :3000 (UI now on host :3333) — avoids the
  common clash with other dev tools that squat on :3000.

### Negative / accepted trade-offs

- Editing the UI requires a rebuild (`make app-build` after code
  changes) before `app-up` picks them up. The `make dev` target
  exists for the tight edit loop, but it's a separate mode that
  runs the UI outside the container.
- Container startup is slower than raw `next start` + `uvicorn`
  (Docker + image pull + node startup ≈ 5–10 s vs <1 s). Acceptable
  for a demo dashboard that is brought up once per reboot.
- We now have two code paths (compose vs `make dev`) to keep
  coherent. Both read the same env vars and mount the same data,
  so divergence risk is low but non-zero.

## References

- [ADR-0003](0003-podman-primary-docker-compatible.md) — Compose-first
  convention (Podman-portable).
- [ADR-0014](0014-triton-vllm-director-shared-host-service.md) — Why
  the inference plane uses systemd (contrast).
- Next.js 16 `output: "standalone"` reference —
  `web/ui/node_modules/next/dist/docs/01-app/03-api-reference/05-config/01-next-config-js/output.md`.
- Vercel's `with-docker` example —
  <https://github.com/vercel/next.js/tree/canary/examples/with-docker>.
