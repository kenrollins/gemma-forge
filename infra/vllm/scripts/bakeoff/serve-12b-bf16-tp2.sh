#!/usr/bin/env bash
# Bake-off ARM C — Gemma 4 12B bf16 (full precision), TP=2, 2 L4s + MTP.
# The quantization control: no FP8, so it isolates "is FP8 costing Architect
# quality?" and "how cheap is TP=2 all-reduce on the smaller 12B?"
# ADR-0024 / futures/gemma4-12b-fleet-preflight.md
#
# Two instances span the box:
#     GPUS=0,1 PORT=8055 ./serve-12b-bf16-tp2.sh
#     GPUS=2,3 PORT=8056 ./serve-12b-bf16-tp2.sh
#
# Run the 31B down first (./stop-31b.sh). Needs the nightly unified image for MTP.

set -euo pipefail

GPUS="${GPUS:-0,1}"
PORT="${PORT:-8055}"
VLLM_IMAGE="${VLLM_IMAGE:-vllm/vllm-openai:gemma4-unified}"
WEIGHTS_DIR="${WEIGHTS_DIR:-/data/triton/weights}"
MODEL="${MODEL:-/weights/gemma-4-12B-it}"
DRAFTER="${DRAFTER:-/weights/gemma-4-12B-it-assistant}"
CONTAINER_NAME="bakeoff-12b-bf16-tp2-gpu${GPUS//,/-}"

# --- preflight: weights present ---
for p in "${MODEL}" "${DRAFTER}"; do
    host="${WEIGHTS_DIR}${p#/weights}"
    [[ -e "${host}" ]] || { echo "[arm-C] missing weights: ${host}"; echo "         pull per the preflight checklist."; exit 1; }
done

# --- preflight: target GPUs free (31B must be stopped first) ---
IFS=',' read -r -a gpu_arr <<< "${GPUS}"
for g in "${gpu_arr[@]}"; do
    used="$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i "${g}" | tr -d ' ')"
    if (( used > 2000 )); then
        echo "[arm-C] GPU${g} shows ${used} MiB used — is the 31B still up? Run ./stop-31b.sh first."
        exit 1
    fi
done

docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

echo "[arm-C] launching 12B bf16 TP=2 on GPUs ${GPUS} -> :${PORT} (container ${CONTAINER_NAME})"
exec docker run --rm \
    --name "${CONTAINER_NAME}" \
    --gpus "\"device=${GPUS}\"" \
    -e CUDA_VISIBLE_DEVICES="${GPUS}" \
    -p "${PORT}:8000" \
    -v "${WEIGHTS_DIR}:/weights:ro" \
    --shm-size=4g \
    --ulimit memlock=-1 \
    --ulimit stack=67108864 \
    "${VLLM_IMAGE}" \
    --model "${MODEL}" \
    --tensor-parallel-size 2 \
    --dtype bfloat16 \
    --max-model-len 16384 \
    --max-num-batched-tokens 4096 \
    --gpu-memory-utilization 0.90 \
    --max-num-seqs 8 \
    --enable-auto-tool-choice \
    --tool-call-parser gemma4 \
    --speculative-config "{\"method\":\"mtp\",\"model\":\"${DRAFTER}\",\"num_speculative_tokens\":4}"
