#!/usr/bin/env bash
#
# install.sh — install GemmaForge vLLM inference units as systemd services.
#
# This is the active inference path while Triton 26.04 is pending.
# Each role (Architect, Auditor, Sentry) gets its own systemd unit
# that runs a vLLM container via gemma-forge/vllm:latest.
#
# Prerequisites:
#   - gemma-forge/vllm:latest built (docker build -t gemma-forge/vllm:latest -f infra/vllm/Dockerfile .)
#   - Model weights downloaded to /data/triton/weights/
#   - /data/triton/logs/ exists
#
# Idempotent: re-running is a no-op.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
INFRA_VLLM="${REPO_ROOT}/infra/vllm"
SYSTEMD_DIR="/etc/systemd/system"

log() { printf "\033[1;32m[vllm-install]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[vllm-install]\033[0m %s\n" "$*" >&2; }

# ---------- Pre-flight -------------------------------------------------------

log "pre-flight checks"

if ! docker image inspect gemma-forge/vllm:latest >/dev/null 2>&1; then
    err "gemma-forge/vllm:latest not found. Build it first:"
    err "    docker build -t gemma-forge/vllm:latest -f infra/vllm/Dockerfile ."
    exit 1
fi
log "  image present: gemma-forge/vllm:latest"

for model in Gemma-4-31B-IT-NVFP4 gemma-4-E4B-it gemma-4-E2B-it; do
    if [[ ! -d "/data/triton/weights/${model}" ]]; then
        err "model weights missing: /data/triton/weights/${model}"
        err "run: infra/triton/scripts/download-models.sh"
        exit 1
    fi
done
log "  all model weights present"

mkdir -p /data/triton/logs

# ---------- Make scripts executable ------------------------------------------

chmod +x "${INFRA_VLLM}/scripts/serve.sh"

# ---------- Disable old Triton units (if enabled) ----------------------------

log "disabling any old Triton units"
for unit in triton@wide-01.service triton@0.service triton@1.service triton@2.service triton@3.service; do
    if systemctl is-enabled --quiet "${unit}" 2>/dev/null; then
        log "  disabling ${unit}"
        sudo systemctl disable "${unit}" 2>&1 | sed 's/^/    /' || true
    fi
done

# ---------- Install vLLM units -----------------------------------------------

UNITS=(
    gemma-forge-architect.service
    gemma-forge-auditor.service
    gemma-forge-sentry.service
)

log "installing systemd unit files to ${SYSTEMD_DIR}/"
for unit in "${UNITS[@]}"; do
    src="${INFRA_VLLM}/systemd/${unit}"
    dst="${SYSTEMD_DIR}/${unit}"
    if [[ -L "${dst}" ]] && [[ "$(readlink -f "${dst}")" == "$(readlink -f "${src}")" ]]; then
        log "  ${unit} already symlinked"
    else
        log "  installing ${unit}"
        sudo ln -sf "${src}" "${dst}"
    fi
done

log "systemctl daemon-reload"
sudo systemctl daemon-reload

log "enabling units (but NOT starting — use 'make demo-up' to start)"
for unit in "${UNITS[@]}"; do
    sudo systemctl enable "${unit}" 2>&1 | sed 's/^/    /'
done

log ""
log "install complete. Use the Makefile targets to manage the demo:"
log ""
log "    make demo-up       start all 3 inference services"
log "    make demo-down     stop all 3 and free GPUs"
log "    make demo-status   show service status + GPU memory"
log "    make demo-logs     tail logs from all 3 services"
