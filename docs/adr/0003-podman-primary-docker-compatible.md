# ADR-0003: Compose v2 spec — Podman-portable, Docker as the runtime on the reference host

- **Status:** Accepted
- **Date:** 2026-04-08
- **Deciders:** Ken Rollins

## Context

gemma-forge is a Federal reference build. In RHEL-based Federal
environments, **Podman** is the default container runtime: it's
daemonless (smaller attack surface), rootless-capable, FIPS-friendly,
ships in RHEL by default, and is what most Federal customers will
already be running on the production hosts they'll deploy this onto.

At the same time, the **reference XR7620 Ken builds this on already
runs Docker** with multiple unrelated production workloads. Migrating
that host to Podman would risk breaking unrelated services for no
demonstrable benefit to gemma-forge itself — the cost of an outage on
those workloads is much higher than the marginal Federal-alignment
benefit of switching runtimes on this single host.

We need a story that:

1. Tells Federal customers "this works on your Podman/RHEL hosts."
2. Doesn't force the reference-build host to migrate runtimes.
3. Doesn't require maintaining two compose files in parallel.

## Decision

gemma-forge ships a **single `docker-compose.yml` written to the plain
Compose v2 specification**. Compose v2 is the format that **both**
`docker compose` (v2 plugin) and `podman compose` consume natively, so
the same file is the source of truth on both runtimes.

On the reference XR7620, we run the stack via **`docker compose`**
because Docker is already installed and serving production workloads
there. We do not install Podman on this host.

In documentation (README and `docs/host-setup.md`), we recommend
**Podman** as the runtime for Federal customer deployments, and provide
both `docker compose up` and `podman compose up` invocation examples in
the runbook.

### Hard rules

- No `version:` field (deprecated in Compose v2).
- No Docker-only extensions (`x-docker-*`, `extends`, etc.).
- No `network_mode: host` unless absolutely required and documented.
- All bind mounts use environment variables (e.g., `${VM_ROOT}`,
  `${MODELS_DIR}`) so paths are not hardcoded.
- Cleanup commands always scope to this project, never global:
  `docker compose down` or `--filter
  label=com.docker.compose.project=gemma-forge`. **Never** run
  `docker system prune` or `docker network prune` on this host.

## Alternatives considered

- **Migrate the XR7620 to Podman** — The maximally Federal-aligned
  choice on paper. Rejected because the host has unrelated production
  Docker workloads that must not be disturbed. The benefit (one extra
  runtime alignment point) does not justify the risk.

- **Maintain parallel `docker-compose.yml` and `podman-compose.yml`** —
  Doubles the maintenance burden and creates drift bugs. Compose v2
  makes this unnecessary.

- **Ship only `podman-compose.yml`** — Would require installing Podman
  on the reference host, hitting the same migration objection as above,
  with the additional downside of deviating from the more familiar
  Docker invocation in the demo runbook.

- **Use Kubernetes / Helm instead of Compose** — Overkill for a
  single-node demo and adds a control plane the audience doesn't care
  about. KubeVirt + OpenShift Virtualization is the right pattern at
  fleet scale; this repo is the single-node reference build.

## Consequences

### Positive

- One file, two runtimes. Federal customers running Podman bring it up
  unmodified; Ken runs it on Docker on the reference host with zero
  migration.
- Honest engineering story for Federal evaluators: we're not pretending
  to run Podman where we don't, and we're not making customers run
  Docker where they shouldn't. The compose-file format is the
  portability mechanism.
- Existing Docker workloads on the XR7620 are completely undisturbed.

### Negative / accepted trade-offs

- We sacrifice some Docker-specific compose conveniences (`extends`,
  certain healthcheck shorthands). This has been a non-issue in the
  Phase 0 skeleton and is unlikely to bite later.
- The runbook has to show both `docker compose` and `podman compose`
  invocations. We accept the slightly longer docs in exchange for
  not having to maintain two compose files.
- Anyone contributing future compose changes must remember the "no
  Docker-only extensions" rule. CI's `docker compose config` validation
  job catches syntax errors but not portability regressions; we will
  add a periodic `podman compose config` smoke check in CI when Phase 7
  hardening lands.

## References

- [Compose Specification (v2)](https://github.com/compose-spec/compose-spec)
- [Podman Compose compatibility notes](https://docs.podman.io/en/latest/markdown/podman-compose.1.html)
- `docs/host-setup.md` (created in Phase 0.5)
