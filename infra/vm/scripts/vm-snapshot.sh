#!/usr/bin/env bash
#
# vm-snapshot.sh — create, restore, or list snapshots of the mission-app VM.
#
# Uses libvirt's built-in snapshotting for sub-10-second demo resets.
#
# Usage:
#   vm-snapshot.sh create <name>     create a snapshot (e.g., "baseline")
#   vm-snapshot.sh restore <name>    restore to a named snapshot
#   vm-snapshot.sh list              list all snapshots
#   vm-snapshot.sh delete <name>     delete a named snapshot

set -euo pipefail

VM_NAME="gemma-forge-mission-app"
ACTION="${1:?usage: vm-snapshot.sh <create|restore|list|delete> [name]}"
SNAP_NAME="${2:-}"

log() { printf "\033[1;32m[snapshot]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[snapshot]\033[0m %s\n" "$*" >&2; }

case "${ACTION}" in
    create)
        [[ -z "${SNAP_NAME}" ]] && { err "usage: vm-snapshot.sh create <name>"; exit 1; }
        log "creating snapshot '${SNAP_NAME}' of ${VM_NAME}"
        sudo virsh snapshot-create-as "${VM_NAME}" "${SNAP_NAME}" \
            --description "GemmaForge demo snapshot $(date -Is)" \
            --atomic 2>&1
        log "done."
        ;;
    restore)
        [[ -z "${SNAP_NAME}" ]] && { err "usage: vm-snapshot.sh restore <name>"; exit 1; }
        log "restoring ${VM_NAME} to snapshot '${SNAP_NAME}'"
        sudo virsh snapshot-revert "${VM_NAME}" "${SNAP_NAME}" 2>&1
        log "done. VM is back to '${SNAP_NAME}' state."
        ;;
    list)
        log "snapshots for ${VM_NAME}:"
        sudo virsh snapshot-list "${VM_NAME}" 2>&1
        ;;
    delete)
        [[ -z "${SNAP_NAME}" ]] && { err "usage: vm-snapshot.sh delete <name>"; exit 1; }
        log "deleting snapshot '${SNAP_NAME}' from ${VM_NAME}"
        sudo virsh snapshot-delete "${VM_NAME}" "${SNAP_NAME}" 2>&1
        log "done."
        ;;
    *)
        err "unknown action: ${ACTION}"
        err "usage: vm-snapshot.sh <create|restore|list|delete> [name]"
        exit 1
        ;;
esac
