#!/usr/bin/env bash
#
# vm-up.sh — provision the GemmaForge mission-app VM via OpenTofu.
#
# Idempotent: if the VM already exists and is running, this is a no-op.
# Uses the default libvirt network and the gemma-forge storage pool.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TOFU_DIR="${REPO_ROOT}/infra/vm/tofu"
VM_ROOT="${VM_ROOT:-/data/vm/gemma-forge}"
STATE_DIR="${VM_ROOT}/state"
VM_NAME="gemma-forge-mission-app"

log() { printf "\033[1;32m[vm-up]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[vm-up]\033[0m %s\n" "$*" >&2; }

# ---------- Pre-flight -------------------------------------------------------

if [[ ! -f "${VM_ROOT}/pool/Rocky-9-GenericCloud.latest.x86_64.qcow2" ]]; then
    err "Rocky 9 image not found at ${VM_ROOT}/pool/"
    err "Download it: curl -fSL -o ${VM_ROOT}/pool/Rocky-9-GenericCloud.latest.x86_64.qcow2 \\"
    err "  https://download.rockylinux.org/pub/rocky/9/images/x86_64/Rocky-9-GenericCloud.latest.x86_64.qcow2"
    exit 1
fi

if [[ ! -f "${VM_ROOT}/keys/adm-forge.pub" ]]; then
    err "SSH key not found. Generate: ssh-keygen -t ed25519 -f ${VM_ROOT}/keys/adm-forge -N ''"
    exit 1
fi

if ! sudo virsh net-info default 2>/dev/null | grep -q "Active.*yes"; then
    log "starting libvirt default network"
    sudo virsh net-start default || true
fi

mkdir -p "${STATE_DIR}"

# ---------- Tofu init + apply ------------------------------------------------

# If the current shell doesn't have the libvirt group yet (added in
# Phase 0.5 but no re-login), wrap tofu via `sg libvirt` so the
# libvirt socket is accessible.
run_tofu() {
    if groups 2>/dev/null | grep -qw libvirt; then
        tofu "$@"
    else
        sg libvirt -c "tofu $*"
    fi
}

log "running tofu init"
cd "${TOFU_DIR}"
run_tofu init 2>&1 | tail -5

log "running tofu apply"
run_tofu apply -auto-approve \
    -state="${STATE_DIR}/terraform.tfstate" \
    -var="vm_root=${VM_ROOT}" \
    2>&1

# ---------- Discover VM IP via virsh -----------------------------------------

log "waiting for VM to get an IP address..."
VM_IP=""
for i in $(seq 1 30); do
    VM_IP=$(sudo virsh domifaddr "${VM_NAME}" 2>/dev/null \
        | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' \
        | head -1 || true)
    if [[ -n "${VM_IP}" ]]; then
        break
    fi
    echo "  attempt ${i}: waiting for DHCP lease..."
    sleep 5
done

if [[ -z "${VM_IP}" ]]; then
    err "could not determine VM IP after 150s"
    err "check: sudo virsh domifaddr ${VM_NAME}"
    exit 1
fi

SSH_KEY="${VM_ROOT}/keys/adm-forge"
SSH_CMD="ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=5 adm-forge@${VM_IP}"

log "VM IP: ${VM_IP}"

# ---------- Wait for SSH -----------------------------------------------------

log "waiting for SSH to come up..."
for i in $(seq 1 30); do
    if ${SSH_CMD} "echo 'SSH ready'" 2>/dev/null; then
        break
    fi
    echo "  attempt ${i}: not ready yet..."
    sleep 10
done

# ---------- Wait for cloud-init to finish ------------------------------------

log "waiting for cloud-init to complete (this installs nginx, postgres, etc.)..."
${SSH_CMD} "cloud-init status --wait" 2>&1 | tail -3

# ---------- Verify mission app -----------------------------------------------

log "running mission-app healthcheck..."
if ${SSH_CMD} "/usr/local/bin/mission-healthcheck.sh" 2>&1; then
    log "mission-app is HEALTHY"
else
    err "mission-app healthcheck FAILED"
    err "SSH in to debug: ${SSH_CMD}"
    exit 1
fi

# ---------- Create baseline snapshot (the "golden" copy) ---------------------

SNAP_SCRIPT="${REPO_ROOT}/infra/vm/scripts/vm-snapshot.sh"
log "creating baseline snapshot (the golden copy for demo resets)..."
"${SNAP_SCRIPT}" create baseline 2>&1

# ---------- Save VM IP for other scripts -------------------------------------

echo "${VM_IP}" > "${VM_ROOT}/state/vm-ip"

log ""
log "VM is up, mission-app is healthy, baseline snapshot created."
log ""
log "  IP:           ${VM_IP}"
log "  SSH:          ${SSH_CMD}"
log "  Healthcheck:  ${SSH_CMD} /usr/local/bin/mission-healthcheck.sh"
log "  Reset:        make vm-reset  (instant revert to golden baseline)"
log "  Destroy:      make vm-down"
log ""
log "Demo cycle:"
log "  1. Run the Ralph loop against the VM"
log "  2. make vm-reset   (sub-10s revert to golden baseline)"
log "  3. Repeat"
