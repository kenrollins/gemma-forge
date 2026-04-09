#!/usr/bin/env bash
#
# vm-down.sh — destroy the GemmaForge mission-app VM via OpenTofu.
#
# Removes the VM, its disk, and the cloud-init ISO. Does NOT remove
# the base Rocky 9 image, SSH keys, or snapshots.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TOFU_DIR="${REPO_ROOT}/infra/vm/tofu"
VM_ROOT="${VM_ROOT:-/data/vm/gemma-forge}"
STATE_DIR="${VM_ROOT}/state"

log() { printf "\033[1;33m[vm-down]\033[0m %s\n" "$*"; }

cd "${TOFU_DIR}"

if [[ ! -f "${STATE_DIR}/terraform.tfstate" ]]; then
    log "no state file found — nothing to destroy"
    exit 0
fi

log "running tofu destroy"
tofu destroy -auto-approve \
    -state="${STATE_DIR}/terraform.tfstate" \
    -var="vm_root=${VM_ROOT}" \
    2>&1

log "done. Base image and keys are preserved."
log "To re-provision: ./infra/vm/scripts/vm-up.sh"
