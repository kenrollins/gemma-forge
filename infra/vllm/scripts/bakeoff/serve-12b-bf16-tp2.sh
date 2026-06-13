#!/usr/bin/env bash
# Gemma 4 12B bf16 (full precision), TP=2 on 2 L4s — the 12B engine for the
# 12B-vs-31B capability experiment (ADR-0024 / futures/gemma4-12b-fleet-preflight.md).
#
# Default is NO MTP: there is no confirmed official 12B drafter, and MTP is a
# throughput-only optimization irrelevant to the capability metric. One
# instance on :8055 is enough for a capability run.
#
#   GPUS=0,1 PORT=8055 ./serve-12b-bf16-tp2.sh           # capability run (no MTP)
#
# Optional second instance for the throughput kicker:
#   GPUS=2,3 PORT=8056 ./serve-12b-bf16-tp2.sh
#
# Optional MTP (only if an official drafter is obtained) — set DRAFTER to its
# path and use the nightly unified image:
#   DRAFTER=/weights/gemma-4-12B-it-assistant \
#   VLLM_IMAGE=vllm/vllm-openai:gemma4-unified GPUS=0,1 ./serve-12b-bf16-tp2.sh
#
# Run the 31B down first (./stop-31b.sh) — the 31B holds all 4 GPUs.

set -euo pipefail

GPUS="${GPUS:-0,1}"
PORT="${PORT:-8055}"
VLLM_IMAGE="${VLLM_IMAGE:-vllm/vllm-openai:latest}"
WEIGHTS_DIR="${WEIGHTS_DIR:-/data/triton/weights}"
MODEL="${MODEL:-/weights/gemma-4-12B-it}"
DRAFTER="${DRAFTER:-}"   # empty = no MTP (default for capability runs)
CONTAINER_NAME="bakeoff-12b-bf16-tp2-gpu${GPUS//,/-}"

# --- preflight: model weights present ---
host="${WEIGHTS_DIR}${MODEL#/weights}"
[[ -e "${host}" ]] || { echo "[12b] missing weights: ${host}"; echo "      run ./pull-12b-weights.sh first."; exit 1; }

# --- preflight: target GPUs free (31B must be stopped first) ---
IFS=',' read -r -a gpu_arr <<< "${GPUS}"
for g in "${gpu_arr[@]}"; do
    used="$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i "${g}" | tr -d ' ')"
    if (( used > 2000 )); then
        echo "[12b] GPU${g} shows ${used} MiB used — is the 31B still up? Run ./stop-31b.sh first."
        exit 1
    fi
done

# --- assemble vLLM args; MTP only if a drafter is explicitly provided ---
vllm_args=(
    --model "${MODEL}"
    --tensor-parallel-size 2
    --dtype bfloat16
    --max-model-len 16384
    --max-num-batched-tokens 4096
    --gpu-memory-utilization 0.90
    --max-num-seqs 8
    --enable-auto-tool-choice
    --tool-call-parser gemma4
)
mode="no-MTP"
if [[ -n "${DRAFTER}" ]]; then
    dhost="${WEIGHTS_DIR}${DRAFTER#/weights}"
    [[ -e "${dhost}" ]] || { echo "[12b] DRAFTER set but missing: ${dhost}"; exit 1; }
    vllm_args+=( --speculative-config "{\"method\":\"mtp\",\"model\":\"${DRAFTER}\",\"num_speculative_tokens\":4}" )
    mode="MTP (drafter ${DRAFTER})"
fi

docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

echo "[12b] launching 12B bf16 TP=2 on GPUs ${GPUS} -> :${PORT} [${mode}] (container ${CONTAINER_NAME})"
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
    "${vllm_args[@]}"
