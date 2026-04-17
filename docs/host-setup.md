# Host setup — bringing up a gemma-forge XR7620 from scratch

This document walks through the **host-level prep** that has to happen
once on a Dell PowerEdge XR7620 (or equivalent multi-L4 host) before
gemma-forge can be cloned and run. It's the human-readable companion to
the scripts under [`infra/triton/`](../infra/triton/) and the
forthcoming `infra/vm/` from Phase 2.

This is **Phase 0.5** in the project roadmap. It is not part of any
single demo run — once it's done on a host, future demos consume the
services it stands up as clients.

> The "shared host service" pattern this document codifies is the
> single most important architectural insight in gemma-forge. See
> [ADR-0012](adr/0012-data-host-layout-convention.md) and
> [ADR-0014](adr/0014-triton-vllm-director-shared-host-service.md)
> for the full reasoning.

---

## What you should already have

| Thing | Why | How to verify |
|---|---|---|
| **Ubuntu 24.04 LTS** (or compatible) | Base OS for the reference host | `cat /etc/os-release` |
| **NVIDIA driver ≥ 565** with 4× L4 visible | Required for Gemma 4 + Triton 26.x | `nvidia-smi -L` should list 4 L4s |
| **Docker** with the **NVIDIA container runtime** registered | Triton runs as a container with `--gpus` | `docker info \| grep -E 'Runtimes\|nvidia'` should show `nvidia` |
| **`nvidia-container-toolkit`** package installed | Provides the container runtime above | `which nvidia-ctk && nvidia-ctk --version` |
| **Sudo access** (NOPASSWD or interactive) | Required for libvirt + systemd installs | `sudo -n true` or your password handy |

If any of these are missing, install them via your distro's normal
channels first. gemma-forge does not assume responsibility for
bringing up the base GPU + Docker stack.

---

## Step 1 — Install host packages

gemma-forge needs three things on top of the base GPU/Docker stack:

1. **libvirt + KVM + cloud-init tooling** — for the target VM(s) that
   the Ralph loop operates on (Phase 2). Installed now so Phase 2 can
   land cleanly.
2. **OpenTofu** — IaC for VM provisioning, per
   [ADR-0004](adr/0004-opentofu-not-terraform.md).
3. **The pinned Triton + vLLM container image** — pulled once, so the
   systemd units don't have to wait on a multi-GB download at first
   start.

```bash
# 1. libvirt + KVM + cloud-init
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    qemu-system-x86 libvirt-daemon-system libvirt-clients virtinst \
    cloud-image-utils bridge-utils libosinfo-bin ovmf genisoimage

# 2. OpenTofu via the official installer
curl --proto '=https' --tlsv1.2 -fsSL \
    https://get.opentofu.org/install-opentofu.sh -o /tmp/install-opentofu.sh
chmod +x /tmp/install-opentofu.sh
sudo /tmp/install-opentofu.sh --install-method deb
rm /tmp/install-opentofu.sh
tofu version  # verify

# 3. Pull the pinned Triton image (this is ~35 GB; do it in a screen/tmux)
docker pull nvcr.io/nvidia/tritonserver:26.03-vllm-python-py3
```

### Add yourself to libvirt and kvm groups

```bash
sudo usermod -aG libvirt,kvm "$USER"
```

You will need a **fresh login** for the new group membership to take
effect on your interactive shell. In the meantime, prefix libvirt
commands with `sudo` or run them under `sg libvirt -c '...'`.

### Verify the libvirt + KVM stack

```bash
sudo virt-host-validate qemu
sudo virsh list --all
systemctl is-active libvirtd  # should be: active
```

Every check from `virt-host-validate qemu` should be `PASS` except
possibly `Checking for secure guest support` (that's AMD SEV / Intel
TDX, not relevant for gemma-forge).

---

## Step 2 — Create the `/data/<service>/` host trees

gemma-forge follows the [`/data/<service>/` host layout convention
(ADR-0012)](adr/0012-data-host-layout-convention.md). Two trees need
to exist before the install scripts run:

```bash
# Triton director state (shared across all demos on this host)
mkdir -p /data/triton/{models,systemd,config,logs}

# gemma-forge VM state (scoped to this project; future demos get their
# own subdirectory under /data/vm/)
mkdir -p /data/vm/gemma-forge/{pool,seed,snapshots,keys,state}
```

These directories should be owned by your user (the host operator),
not root. If they end up root-owned, fix with
`sudo chown -R $USER:$USER /data/triton /data/vm/gemma-forge`.

---

## Step 3 — Install the Triton director

```bash
cd /data/code/gemma-forge
./infra/triton/scripts/install.sh
```

What this does (full detail in
[`infra/triton/README.md`](../infra/triton/README.md)):

1. Verifies prerequisites (Docker up, NVIDIA runtime registered,
   4 L4s visible, the pinned Triton image present locally).
2. Copies `infra/triton/config/triton-defaults.env` to
   `/data/triton/config/triton-defaults.env` (preserves local edits
   on re-runs).
3. **Symlinks** the systemd unit files into `/etc/systemd/system/`
   so edits in the repo flow through after a `daemon-reload`.
4. Runs `systemctl daemon-reload`.
5. Enables the **default gemma-forge layout (ADR-0015 Option A)**:
   - `triton@wide-01.service` — GPUs 0+1, Gemma 4 31B-IT, tp=2
   - `triton@2.service` — GPU 2, Gemma 4 E4B
   - `triton@3.service` — GPU 3, Gemma 4 E2B
6. Explicitly **disables** `triton@0` and `triton@1` because they
   collide with the wide instance.

### Verify

```bash
systemctl is-enabled triton@wide-01.service triton@2.service triton@3.service
# expected: enabled / enabled / enabled

systemctl is-enabled triton@0.service triton@1.service 2>/dev/null
# expected: disabled / disabled  (or error: no such unit instance)
```

The units are **enabled but not started.** Phase 1 is responsible for
placing model directories under `/data/triton/models/` and starting
the services for the first time.

---

## Step 4 — Verify nothing existing got disturbed

gemma-forge is being added to a host that's already running other
workloads. The strict rule from
[`feedback_dont_touch_docker.md`](../#) is that **existing Docker
workloads must never be disturbed**. Verify after each install step:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
```

Every container that was running before you started Phase 0.5 should
still be running with its uptime intact. If anything changed, stop
and investigate before proceeding.

---

## What you have after Step 3

```
┌─────────────────────────────────────────────────────────────────────┐
│  Dell XR7620                                                         │
│                                                                      │
│  /data/triton/                                                       │
│  ├── models/         <- (empty; Phase 1 populates this)              │
│  ├── config/         <- triton-defaults.env (TRITON_IMAGE pinned)    │
│  ├── logs/           <- per-instance Triton logs                     │
│  └── systemd/        <- (reserved)                                   │
│                                                                      │
│  /etc/systemd/system/                                                │
│  ├── triton@.service          <- symlink to repo                     │
│  └── triton@wide-01.service   <- symlink to repo                     │
│                                                                      │
│  systemd state:                                                      │
│  ├── triton@wide-01.service   ENABLED, not started                   │
│  ├── triton@2.service         ENABLED, not started                   │
│  └── triton@3.service         ENABLED, not started                   │
│                                                                      │
│  /data/vm/gemma-forge/                                               │
│  ├── pool/  seed/  snapshots/  keys/  state/   (all empty)           │
│                                                                      │
│  Existing Docker workloads:                                          │
│  └── (untouched — langfuse, traefik, supabase, etc. all still up)    │
└─────────────────────────────────────────────────────────────────────┘
```

Phase 0.5 is complete. Phase 1 starts here: model placement and the
first Triton service starts.

---

## Uninstall

To reverse Phase 0.5 (e.g., to migrate to a different host or to
clean up after testing):

```bash
./infra/triton/scripts/uninstall.sh
```

This is **deliberately conservative**: it stops + disables the units
and removes the symlinks from `/etc/systemd/system/`, but it does
**not** touch:

- `/data/triton/models/` (the model catalog)
- `/data/triton/logs/` (historical logs)
- `/data/triton/config/triton-defaults.env`
- The Triton container image
- Anything outside `/data/triton/`

To fully wipe `/data/triton/`, do that manually after the uninstall
script runs. The two-step is intentional — accidental deletion of a
multi-GB model catalog is too easy.

---

## Troubleshooting

**`install.sh` says the Triton image is missing.** Pull it:
```bash
docker pull nvcr.io/nvidia/tritonserver:26.03-vllm-python-py3
```

**`install.sh` says NVIDIA runtime is not registered with Docker.**
Install `nvidia-container-toolkit` and restart Docker:
```bash
sudo apt-get install nvidia-container-toolkit
sudo systemctl restart docker
```
(Be aware this restart may briefly disturb other Docker workloads.
Coordinate with anyone else using the host first.)

**`virt-host-validate qemu` fails on `Checking if IOMMU is enabled
by kernel`.** Add `intel_iommu=on iommu=pt` to your kernel command
line via `/etc/default/grub`, then `sudo update-grub` and reboot.
This is required for any future PCI passthrough scenarios but not
strictly required for the Phase 2 target VM.

**`systemctl start triton@2.service` fails immediately.** Check the
log file:
```bash
tail -100 /data/triton/logs/triton-gpu2.log
journalctl -u triton@2.service --no-pager | tail -50
```
The most common cause in Phase 0.5 → Phase 1 transition is "no model
files in `/data/triton/models/` yet" — populate the model catalog
first.

---

## See also

- [`infra/triton/README.md`](../infra/triton/README.md) — Triton director technical reference
- [ADR-0012](adr/0012-data-host-layout-convention.md) — host layout convention
- [ADR-0014](adr/0014-triton-vllm-director-shared-host-service.md) — why Triton is a shared host service
- [ADR-0013](adr/0013-one-triton-per-l4-no-nvlink.md) — one Triton per L4 + one wide
- [ADR-0015](adr/0015-gemma-4-model-lineup.md) — Gemma 4 model lineup
- [ADR-0007](adr/0007-otel-jaeger-prometheus-grafana.md) — OTel + Jaeger + Prometheus + Grafana (no Langfuse)
