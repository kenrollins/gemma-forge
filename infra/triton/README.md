# `infra/triton/` — GemmaForge Triton director

This directory contains the **host-level installation artifacts** for the
shared Triton + vLLM model director that GemmaForge consumes as a client.

The runtime service does **not** live in this directory or in this repo
at all — it lives at `/data/triton/` on the host (see ADR-0012 and
ADR-0014). What you find here is the canonical, version-controlled
copy of the systemd units, runner scripts, defaults config, and
install/uninstall tooling that put the runtime service in place.

## Layout

```
infra/triton/
├── README.md             ← you are here
├── config/
│   └── triton-defaults.env   ← shared defaults (image tag, paths)
├── scripts/
│   ├── install.sh            ← copies/symlinks units, enables defaults
│   ├── uninstall.sh          ← reverses install (preserves model catalog)
│   ├── triton-narrow.sh      ← runner for single-GPU instances
│   └── triton-wide.sh        ← runner for the wide tp=2 instance
└── systemd/
    ├── triton@.service           ← templated narrow unit (one per L4)
    └── triton@wide-01.service    ← wide instance, GPUs 0+1, tp=2
```

## What gets installed where

After running `scripts/install.sh`, the host has:

```
/data/triton/
├── config/
│   └── triton-defaults.env   ← copy from this repo (preserves local edits)
├── logs/
│   └── triton-*.log          ← per-instance logs (created on first start)
├── models/                   ← model catalog (Phase 1 populates this)
└── systemd/                  ← (reserved for future use)

/etc/systemd/system/
├── triton@.service           ← symlink → /data/code/gemma-forge/infra/triton/systemd/triton@.service
└── triton@wide-01.service    ← symlink → /data/code/gemma-forge/infra/triton/systemd/triton@wide-01.service
```

The systemd unit files are **symlinked** rather than copied so that
edits in this repo flow through to the running services after a
`systemctl daemon-reload`. Defaults config is **copied** so local host
overrides aren't blown away by a `git pull`.

## The default GemmaForge layout (ADR-0015 Option A)

`install.sh` enables three units by default, matching the model lineup
in ADR-0015:

| Unit                       | GPU(s) | Model            | Role(s)              |
|----------------------------|--------|------------------|----------------------|
| `triton@wide-01.service`   | 0+1    | Gemma 4 31B-IT (tp=2) | Architect + Worker (shared engine) |
| `triton@2.service`         | 2      | Gemma 4 E4B           | Auditor              |
| `triton@3.service`         | 3      | Gemma 4 E2B           | Sentry               |

`triton@0.service` and `triton@1.service` are **explicitly disabled**
(they would compete with the wide instance for the same physical GPUs).
The `Conflicts=` directive on the wide unit also enforces this at the
systemd level.

## Port allocation

Per-GPU narrow instances (`triton@N`) use ports computed from the GPU
index:

| Instance        | HTTP   | gRPC   | Metrics |
|-----------------|--------|--------|---------|
| `triton@0`      | 8000   | 8001   | 8002    |
| `triton@1`      | 8010   | 8011   | 8012    |
| `triton@2`      | 8020   | 8021   | 8022    |
| `triton@3`      | 8030   | 8031   | 8032    |

The wide instance lives above the narrow range so they cannot collide:

| Instance              | HTTP | gRPC | Metrics |
|-----------------------|------|------|---------|
| `triton@wide-01`      | 8040 | 8041 | 8042    |

## Use

### Install (one-time, requires sudo)

```bash
./infra/triton/scripts/install.sh
```

This is idempotent — re-running after a successful install is a no-op.

### Verify

```bash
systemctl status 'triton@*.service'
systemctl is-enabled triton@wide-01.service triton@2.service triton@3.service
```

### Start (only after Phase 1 has placed model files under /data/triton/models/)

```bash
sudo systemctl start triton@wide-01.service
sudo systemctl start triton@2.service
sudo systemctl start triton@3.service
```

### Inspect logs

```bash
journalctl -u 'triton@*.service' -f
tail -f /data/triton/logs/triton-*.log
```

### Smoke-test the management API

```bash
curl http://localhost:8020/v2/health/ready              # GPU 2 narrow
curl http://localhost:8040/v2/health/ready              # wide instance
curl -X POST http://localhost:8040/v2/repository/models/gemma4-31b-it/load
curl -X POST http://localhost:8040/v2/repository/models/gemma4-31b-it/unload
```

### Uninstall (preserves model catalog)

```bash
./infra/triton/scripts/uninstall.sh
```

## See also

- [ADR-0014](../../docs/adr/0014-triton-vllm-director-shared-host-service.md) — why Triton is a shared host service
- [ADR-0013](../../docs/adr/0013-one-triton-per-l4-no-nvlink.md) — why one Triton per L4 (plus a wide instance)
- [ADR-0015](../../docs/adr/0015-gemma-4-model-lineup.md) — why this exact model lineup
- [ADR-0012](../../docs/adr/0012-data-host-layout-convention.md) — `/data/<service>/` host layout convention
- [`docs/host-setup.md`](../../docs/host-setup.md) — full host bring-up guide
