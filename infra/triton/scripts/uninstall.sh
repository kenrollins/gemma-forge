#!/usr/bin/env bash
#
# uninstall.sh — remove the GemmaForge Triton director from this host.
#
# What this does:
#   1. systemctl stop on any running triton@ instances we manage.
#   2. systemctl disable on the units we previously enabled.
#   3. Removes the symlinked unit files from /etc/systemd/system/.
#   4. systemctl daemon-reload.
#
# What this DELIBERATELY DOES NOT do:
#   - Touch /data/triton/models/ (the model catalog is preserved).
#   - Touch /data/triton/logs/ (logs are preserved for forensics).
#   - Pull or remove the Triton container image.
#   - Delete /data/triton/config/triton-defaults.env.
#
# To fully wipe /data/triton/, do that manually after this script runs.
# That's a deliberate two-step to make accidental data loss harder.
#
# Idempotent: re-running this script after a successful uninstall is a no-op.

set -euo pipefail

SYSTEMD_DIR="/etc/systemd/system"
UNITS=(
    "triton@wide-01.service"
    "triton@0.service"
    "triton@1.service"
    "triton@2.service"
    "triton@3.service"
    "triton@.service"
)

log() { printf "\033[1;33m[uninstall]\033[0m %s\n" "$*"; }

log "stopping any running GemmaForge Triton instances"
for unit in "${UNITS[@]}"; do
    if systemctl is-active --quiet "${unit}" 2>/dev/null; then
        log "  stopping ${unit}"
        sudo systemctl stop "${unit}" || true
    fi
done

log "disabling enabled units"
for unit in "${UNITS[@]}"; do
    if systemctl is-enabled --quiet "${unit}" 2>/dev/null; then
        log "  disabling ${unit}"
        sudo systemctl disable "${unit}" 2>&1 | sed 's/^/    /' || true
    fi
done

log "removing symlinked unit files from ${SYSTEMD_DIR}/"
for unit in triton@.service triton@wide-01.service; do
    target="${SYSTEMD_DIR}/${unit}"
    if [[ -L "${target}" ]] || [[ -f "${target}" ]]; then
        log "  removing ${target}"
        sudo rm -f "${target}"
    fi
done

log "systemctl daemon-reload"
sudo systemctl daemon-reload

log "uninstall complete."
log ""
log "the following are PRESERVED and not touched by this script:"
log "    /data/triton/models/    (model catalog)"
log "    /data/triton/logs/      (historical logs)"
log "    /data/triton/config/    (defaults file)"
log ""
log "to wipe them: rm -rf /data/triton/  (be sure first)"
