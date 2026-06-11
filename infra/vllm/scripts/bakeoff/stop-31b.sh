#!/usr/bin/env bash
# Cleanly stop the shared Gemma 4 31B host service to free all 4 L4s for the
# 12B bake-off (ADR-0024 / futures/gemma4-12b-fleet-preflight.md).
#
# WHY systemctl, not `docker stop`:
#   The unit is Restart=on-failure (15s). A manual `docker stop` that makes
#   vLLM exit non-zero would trigger a systemd restart within 15s and re-grab
#   the GPUs mid-test. `systemctl stop` runs the unit's ExecStop and marks the
#   unit inactive, so it stays down until we start it again.
#
# WHO ELSE IS AFFECTED:
#   :8050 is a SHARED host service. The unit declares clients beyond gemma-forge
#   (quantum-ai-orchestrator, ...). Stopping it takes inference down for ALL of
#   them. Confirm nothing else is mid-run before proceeding.
#
# The unit stays `enabled`, so a REBOOT during the bake-off window would bring
# the 31B back and fight for GPUs. Don't reboot mid-window. If a reboot is
# unavoidable, also: sudo systemctl disable gemma4-31b-vllm  (re-enable after).
#
# Restore with: ./restore-31b.sh

set -euo pipefail

UNIT="gemma4-31b-vllm.service"
CONTAINER="gemma4-31b-vllm"

if [[ "$(systemctl is-active "${UNIT}" 2>/dev/null || true)" != "active" ]]; then
    echo "[stop-31b] ${UNIT} is not active — nothing to stop."
    exit 0
fi

echo "=============================================================="
echo " About to stop the SHARED Gemma 4 31B inference service (:8050)."
echo " This affects every client of :8050 — per the unit, that includes"
echo " gemma-forge AND quantum-ai-orchestrator (and any other clients)."
echo "=============================================================="
if [[ "${1:-}" != "--yes" ]]; then
    read -r -p "Stop the shared 31B service now? [y/N] " reply
    [[ "${reply}" =~ ^[Yy]$ ]] || { echo "[stop-31b] Aborted."; exit 1; }
fi

echo "[stop-31b] systemctl stop ${UNIT} (ExecStop = docker stop --time=120) ..."
sudo systemctl stop "${UNIT}"

# Wait for the container to actually be gone and the GPUs to drain.
for _ in $(seq 1 30); do
    docker ps --format '{{.Names}}' | grep -qx "${CONTAINER}" || break
    sleep 2
done

echo "[stop-31b] GPU state after stop:"
nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv

used0="$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1 | tr -d ' ')"
if (( used0 > 2000 )); then
    echo "[stop-31b] WARNING: GPU0 still shows ${used0} MiB used — something else"
    echo "           is on the GPUs, or the 31B hasn't fully drained. Check before"
    echo "           launching any bake-off arm."
else
    echo "[stop-31b] GPUs drained. Ready for bake-off arms."
fi
