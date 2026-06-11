#!/usr/bin/env bash
# Bake-off ARM B — Gemma 4 12B FP8, TP=1, single L4 + MTP.
# The data-parallel candidate: one instance per GPU, zero all-reduce.
# ADR-0024 / futures/gemma4-12b-fleet-preflight.md
#
# Run the 31B down first (./stop-31b.sh). To stand up the full 4-instance
# fleet, launch this once per GPU:
#     GPU=0 PORT=8051 ./serve-12b-fp8-tp1.sh   # on GPUs 0..3, ports 8051..8054
#     GPU=1 PORT=8052 ./serve-12b-fp8-tp1.sh
#     GPU=2 PORT=8053 ./serve-12b-fp8-tp1.sh
#     GPU=3 PORT=8054 ./serve-12b-fp8-tp1.sh
#
# 12B MTP needs the nightly unified image. Pin a digest in CI; latest here.

set -euo pipefail

GPU="${GPU:-0}"
PORT="${PORT:-8051}"
VLLM_IMAGE="${VLLM_IMAGE:-vllm/vllm-openai:gemma4-unified}"
WEIGHTS_DIR="${WEIGHTS_DIR:-/data/triton/weights}"
MODEL="${MODEL:-/weights/gemma-4-12B-it-FP8}"
DRAFTER="${DRAFTER:-/weights/gemma-4-12B-it-assistant}"
CONTAINER_NAME="bakeoff-12b-fp8-tp1-gpu${GPU}"

# --- preflight: weights present ---
for p in "${MODEL}" "${DRAFTER}"; do
    host="${WEIGHTS_DIR}${p#/weights}"
    [[ -e "${host}" ]] || { echo "[arm-B] missing weights: ${host}"; echo "         pull per the preflight checklist."; exit 1; }
done

# --- preflight: target GPU is free (31B must be stopped first) ---
used="$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i "${GPU}" | tr -d ' ')"
if (( used > 2000 )); then
    echo "[arm-B] GPU${GPU} shows ${used} MiB used — is the 31B still up? Run ./stop-31b.sh first."
    exit 1
fi

docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

echo "[arm-B] launching 12B FP8 TP=1 on GPU${GPU} -> :${PORT} (container ${CONTAINER_NAME})"
exec docker run --rm \
    --name "${CONTAINER_NAME}" \
    --gpus "\"device=${GPU}\"" \
    -e CUDA_VISIBLE_DEVICES="${GPU}" \
    -p "${PORT}:8000" \
    -v "${WEIGHTS_DIR}:/weights:ro" \
    --shm-size=2g \
    --ulimit memlock=-1 \
    --ulimit stack=67108864 \
    "${VLLM_IMAGE}" \
    --model "${MODEL}" \
    --tensor-parallel-size 1 \
    --quantization fp8 \
    --kv-cache-dtype fp8 \
    --max-model-len 16384 \
    --max-num-batched-tokens 4096 \
    --gpu-memory-utilization 0.90 \
    --max-num-seqs 8 \
    --enable-auto-tool-choice \
    --tool-call-parser gemma4 \
    --speculative-config "{\"method\":\"mtp\",\"model\":\"${DRAFTER}\",\"num_speculative_tokens\":4}"
