#!/usr/bin/env bash
# Restore the shared Gemma 4 31B host service after a 12B bake-off window.
# Brings :8050 back for gemma-forge + quantum-ai-orchestrator (+ any clients).
#
# Run this AFTER tearing down all bake-off arm containers (see
# `docker ps | grep bakeoff-12b` and `docker rm -f` them first), so the
# GPUs are free for the 31B's TP=4 launch.

set -euo pipefail

UNIT="gemma4-31b-vllm.service"

# Refuse to start the 31B if bake-off arms are still holding GPUs.
leftover="$(docker ps --format '{{.Names}}' | grep '^bakeoff-12b' || true)"
if [[ -n "${leftover}" ]]; then
    echo "[restore-31b] Bake-off arm containers still running:"
    echo "${leftover}" | sed 's/^/    /'
    echo "[restore-31b] Tear them down first:  docker rm -f ${leftover//$'\n'/ }"
    exit 1
fi

echo "[restore-31b] systemctl start ${UNIT} ..."
sudo systemctl start "${UNIT}"

echo "[restore-31b] Waiting for :8050 to answer ..."
for _ in $(seq 1 60); do
    if curl -sf http://localhost:8050/v1/models >/dev/null 2>&1; then
        echo "[restore-31b] 31B is up and serving on :8050."
        systemctl is-active "${UNIT}"
        exit 0
    fi
    sleep 5
done

echo "[restore-31b] WARNING: :8050 did not answer within 5 min. Check:"
echo "    systemctl status ${UNIT}"
echo "    tail -f /data/triton/logs/gemma4-31b-vllm.log"
exit 1
