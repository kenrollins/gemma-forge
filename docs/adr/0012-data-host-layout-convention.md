# ADR-0012: `/data/<service>/` host layout convention

- **Status:** Accepted
- **Date:** 2026-04-09
- **Deciders:** Ken Rollins
- **Related:** [ADR-0014](0014-triton-vllm-director-shared-host-service.md)

## Context

The Dell PowerEdge XR7620 is being used as a multi-tenant demo host:
multiple distinct demo projects (GemmaForge being the first of several)
will run on the same hardware over the host's lifetime. Each project
has its own source repository, but the projects share infrastructure:
container runtime, model weights, VM hypervisor, observability backend.

Without an explicit convention, every demo project is tempted to bundle
its own copy of every backing service inside its own repo, leading to:

- **Wasted GPU memory and disk space** from duplicate model caches and
  duplicate inference servers.
- **Configuration drift** between "the demo" and "what a Federal
  customer will actually run on their host."
- **Risk of breaking unrelated workloads** when a project's `docker
  compose down` accidentally tears down a service another project
  depends on.
- **Demo repos that contain host-specific absolute paths**, making
  them un-shareable as reference builds.

The host already follows an informal `/data/<service>/` convention for
some things — `/data/code/` for source repos, `/data/docker/` for the
Docker daemon root — but this convention has not been formalized or
extended to new shared services as they come online.

## Decision

The Dell XR7620 host follows an explicit **`/data/<service>/` layout
convention**. Each top-level subdirectory under `/data/` is owned by
exactly one of two categories:

1. **Source / project trees** (`/data/code/`, `/data/code/<project>/`)
   — version-controlled repositories. Owned by the user, modified
   freely, contain only files small enough and portable enough to
   live in git.

2. **Shared host services** (`/data/<service>/`) — runtime state and
   configuration for services that multiple projects consume. Each
   service directory is owned by the **host**, not by any one
   project. Demo projects access these services as **clients** via
   well-defined endpoints (URLs, sockets, model repository paths).

### Currently established `/data/<service>/` directories

| Path | Purpose | Owned by |
|---|---|---|
| `/data/code/` | Source repositories (this repo lives here) | rollik |
| `/data/docker/` | Docker daemon root + container state | Docker daemon |
| `/data/triton/` | Shared Triton+vLLM model director (see ADR-0014) | host (rollik) |
| `/data/vm/` | libvirt/KVM VM state, scoped per project under `/data/vm/<project>/` | host (rollik) |

The exact subdirectory layout under each service directory is documented
where the service is defined (ADR-0014 for Triton, this ADR for the
overall convention). For GemmaForge specifically:

```
/data/triton/
  models/      <- shared Triton model repository
  systemd/     <- staged systemd unit files (canonical copies under /etc/systemd/system/)
  config/      <- shared environment defaults consumed by the runner scripts
  logs/        <- per-instance Triton logs

/data/vm/
  gemma-forge/
    pool/       <- libvirt storage pool for GemmaForge VMs
    seed/       <- cloud-init seed ISOs
    snapshots/  <- external snapshots for fast demo reset
    keys/       <- generated SSH keys (NEVER committed; gitignored at the source side)
    state/      <- OpenTofu local state file
```

### Rules

1. **Demo projects do not own services.** A `docker compose down`
   inside `/data/code/<project>/` must never affect anything outside
   that project's compose `name:` namespace. If a project needs a
   service that doesn't exist on the host, it either runs that
   service in its OWN compose (scoped to the project) or proposes
   adding it as a host service via a new ADR.

2. **Demo projects do not write to `/data/<service>/`** for shared
   services. They consume the service via its endpoint. The Triton
   model repository at `/data/triton/models/` is read-only for
   GemmaForge; weights are placed there by the host operator's
   model-download workflow, not by GemmaForge code.

3. **Demo projects read shared-service endpoints from environment
   variables**, not hardcoded paths. The convention is
   `<SERVICE>_URL` for HTTP endpoints (e.g., `TRITON_DIRECTOR_URL`),
   `<SERVICE>_*_KEY` for auth, and `<SERVICE>_*_PATH` for read-only
   filesystem references where applicable. Defaults can be sensible
   (`http://host.containers.internal:8000` for Triton from inside a
   container) but absolute paths must always be overridable.

4. **Adding a new top-level `/data/<service>/` directory requires an
   ADR.** The bar is "this service will be consumed by more than
   one project, or it is genuinely host-scoped infrastructure." A
   one-off project state directory belongs under
   `/data/<project>/` or inside the project's own tree, not at the
   top level of `/data/`.

5. **Existing host workloads are sacred.** The Docker daemon and
   any containers Ken is already running on the host (langfuse,
   traefik, supabase, etc. — see `docker ps`) must never be
   disturbed by GemmaForge work. See `feedback_dont_touch_docker.md`
   in the project's memory.

## Alternatives considered

- **Bundle every service inside each demo project's repo** — Maximally
  isolated, easy to understand. Rejected because it duplicates model
  weights and inference state across projects and forces the host
  operator to spin up a new ~35GB Triton container per demo. Wastes
  GPU memory, disk space, and time.

- **Run shared services inside a "platform" project under
  `/data/code/platform/`** — Would put the systemd units, the
  Triton model repository, and the VM state inside a version-
  controlled repo. Rejected because (a) the model repository
  contains multi-GB weight files that don't belong in git, (b)
  systemd units installed under a project tree fight the standard
  Linux service-management conventions, and (c) the boundary
  between "what's source-controlled" and "what's runtime state"
  becomes muddy. The `/data/<service>/` separation makes that
  boundary structural.

- **Use `/srv/` instead of `/data/`** — `/srv/` is the FHS-blessed
  location for "data for services provided by the system." We use
  `/data/` because it's already established on this host
  (`/data/code/`, `/data/docker/`) and because Ken treats `/data/`
  as the dedicated mount point for everything mutable. Switching
  to `/srv/` now would invalidate existing conventions.

- **Use Kubernetes namespaces and persistent volumes** — Overkill
  for a single-node demo host. KubeVirt + OpenShift Virtualization
  is the right shape at fleet scale; this repo is the single-node
  reference build.

## Consequences

### Positive

- **Multi-demo coexistence is structural, not aspirational.** The
  next demo project to land on this host will follow the same
  pattern, share the same Triton, share the same VM hypervisor, and
  not have to invent its own conventions.
- **Reference build clarity.** A Federal customer cloning a
  GemmaForge-style repo can immediately see which parts they need to
  provide on their own host (the `/data/<service>/` services) versus
  which parts they get for free by cloning (the project tree under
  `/data/code/`).
- **No host-specific paths in repos.** All cross-references go
  through environment variables. Sharing the repo with a customer
  whose `/data/` layout differs is a `.env` change, not a code change.
- **Existing host workloads are protected.** The convention
  explicitly forbids touching shared state from inside a project's
  compose teardown.

### Negative / accepted trade-offs

- **Slightly higher friction for first-time setup.** A new operator
  has to install the host services (Triton director, VM tooling)
  before they can run any demo. Mitigated by the
  `infra/triton/scripts/install.sh` script and `docs/host-setup.md`
  which automate Phase 0.5 in one command.
- **Two sources of truth for "what runs on this box."** The
  `docker-compose.yml` shows the project-scoped containers; the
  systemd units show the host-scoped services. We address this in
  `docs/host-setup.md` by documenting both sides explicitly with a
  diagram.
- **`/data/` is now a load-bearing convention.** A future move of
  the `/data` mount point would require touching every shared
  service. We accept this; the reverse (everything bundled inside
  one project) would have its own load-bearing assumptions.

## References

- [Filesystem Hierarchy Standard — `/srv` and `/data` discussion](https://refspecs.linuxfoundation.org/FHS_3.0/fhs/ch03s17.html)
- ADR-0014: Triton-managed vLLM director (shared host service at `/data/triton/`)
- `feedback_dont_touch_docker.md` (project memory) — the "don't touch existing Docker workloads" rule that this convention codifies
