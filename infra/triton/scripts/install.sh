#!/usr/bin/env bash
#
# install.sh — host-level install of the GemmaForge Triton director.
#
# What this does:
#   1. Verifies prerequisites: docker daemon up, NVIDIA runtime registered,
#      4 L4 GPUs visible, the pinned Triton image present locally.
#   2. Creates /data/triton/{models,systemd,config,logs} if missing.
#   3. Copies infra/triton/config/triton-defaults.env to /data/triton/config/.
#   4. Symlinks the systemd unit files into /etc/systemd/system/.
#   5. systemctl daemon-reload.
#   6. Enables the default GemmaForge layout per ADR-0015 Option A:
#      - triton@wide-01.service  (GPUs 0+1, Gemma 4 31B-IT, tp=2)
#      - triton@2.service        (GPU 2, Gemma 4 E4B)
#      - triton@3.service        (GPU 3, Gemma 4 E2B)
#      Explicitly does NOT enable triton@0 or triton@1 — they would
#      collide with the wide instance.
#   7. Does NOT start the units. Phase 1 is responsible for placing model
#      directories under /data/triton/models/ and starting the services.
#
# Idempotent: re-running this script after a successful install is a no-op.
#
# Requires: sudo (uses systemctl + writes to /etc/systemd/system/).
# See ADR-0014, ADR-0013, ADR-0015, docs/host-setup.md.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
INFRA_TRITON="${REPO_ROOT}/infra/triton"
DATA_TRITON="/data/triton"
SYSTEMD_DIR="/etc/systemd/system"

log() { printf "\033[1;32m[install]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[install]\033[0m %s\n" "$*" >&2; }

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        err "missing required command: $1"
        exit 1
    fi
}

# ---------- 1. Pre-flight checks --------------------------------------------

log "pre-flight checks"
require_cmd docker
require_cmd systemctl
require_cmd nvidia-smi

if ! systemctl is-active --quiet docker; then
    err "docker daemon is not active. Run: sudo systemctl start docker"
    exit 1
fi

if ! docker info 2>/dev/null | grep -qE '^\s*Runtimes:.*\bnvidia\b'; then
    err "NVIDIA container runtime not registered with docker. Install nvidia-container-toolkit."
    exit 1
fi

GPU_COUNT="$(nvidia-smi -L | wc -l)"
if [[ "${GPU_COUNT}" -lt 4 ]]; then
    err "expected at least 4 NVIDIA GPUs, found ${GPU_COUNT}"
    exit 1
fi
log "found ${GPU_COUNT} GPU(s):"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader | sed 's/^/  /'

# Source the defaults to learn TRITON_IMAGE.
# shellcheck disable=SC1091
source "${INFRA_TRITON}/config/triton-defaults.env"

if ! docker image inspect "${TRITON_IMAGE}" >/dev/null 2>&1; then
    err "pinned Triton image not present locally: ${TRITON_IMAGE}"
    err "pull it first: docker pull ${TRITON_IMAGE}"
    exit 1
fi
log "Triton image present: ${TRITON_IMAGE}"

# ---------- 2. Directory tree -----------------------------------------------

log "ensuring ${DATA_TRITON}/ tree exists"
for d in models systemd config logs; do
    if [[ ! -d "${DATA_TRITON}/${d}" ]]; then
        log "  creating ${DATA_TRITON}/${d}"
        mkdir -p "${DATA_TRITON}/${d}"
    fi
done

# ---------- 3. Defaults file -----------------------------------------------

log "installing default config to ${DATA_TRITON}/config/triton-defaults.env"
if [[ -f "${DATA_TRITON}/config/triton-defaults.env" ]]; then
    log "  already present (preserving local edits)"
else
    cp "${INFRA_TRITON}/config/triton-defaults.env" "${DATA_TRITON}/config/triton-defaults.env"
fi

# ---------- 4. systemd units ------------------------------------------------

log "installing systemd unit files to ${SYSTEMD_DIR}/"
for unit in triton@.service triton@wide-01.service; do
    src="${INFRA_TRITON}/systemd/${unit}"
    dst="${SYSTEMD_DIR}/${unit}"
    if [[ -L "${dst}" ]] && [[ "$(readlink -f "${dst}")" == "$(readlink -f "${src}")" ]]; then
        log "  ${unit} already symlinked"
    else
        log "  installing ${unit}"
        sudo ln -sf "${src}" "${dst}"
    fi
done

# Make the runner scripts executable (idempotent).
chmod +x "${INFRA_TRITON}/scripts/triton-narrow.sh" "${INFRA_TRITON}/scripts/triton-wide.sh"

# ---------- 5. systemctl daemon-reload --------------------------------------

log "systemctl daemon-reload"
sudo systemctl daemon-reload

# ---------- 6. Enable default layout (ADR-0015 Option A) --------------------

log "enabling default GemmaForge layout (ADR-0015 Option A)"

# Wide instance gets enabled first; the Conflicts= directive in its unit
# would refuse to start alongside triton@0 / triton@1 anyway.
ENABLE_UNITS=(
    "triton@wide-01.service"
    "triton@2.service"
    "triton@3.service"
)
DISABLE_UNITS=(
    "triton@0.service"
    "triton@1.service"
)

for unit in "${ENABLE_UNITS[@]}"; do
    log "  enabling ${unit}"
    sudo systemctl enable "${unit}" 2>&1 | sed 's/^/    /'
done

for unit in "${DISABLE_UNITS[@]}"; do
    if systemctl is-enabled --quiet "${unit}" 2>/dev/null; then
        log "  disabling ${unit} (collides with wide instance)"
        sudo systemctl disable "${unit}" 2>&1 | sed 's/^/    /'
    fi
done

# ---------- 7. Done ---------------------------------------------------------

log "install complete."
log ""
log "the units are ENABLED but NOT STARTED. Phase 1 is responsible for"
log "placing model directories under ${DATA_TRITON}/models/ and starting"
log "the services. To start them now (only after model files are in place):"
log ""
log "    sudo systemctl start triton@wide-01.service"
log "    sudo systemctl start triton@2.service"
log "    sudo systemctl start triton@3.service"
log ""
log "to inspect:"
log "    systemctl status 'triton@*.service'"
log "    journalctl -u 'triton@*.service' -f"
log "    tail -f ${DATA_TRITON}/logs/triton-*.log"
