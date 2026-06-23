#!/usr/bin/env bash
#
# pull-12b-weights.sh — fetch the official Gemma 4 12B bf16 weights for the
# 12B-vs-31B capability experiment (ADR-0024).
#
# Only the official Google bf16 instruction-tuned checkpoint:
#   google/gemma-4-12B-it  (~24 GB, Apache-2.0, ungated)
# No FP8/QAT/community quants — see ADR-0024 (Federal needs official weights;
# no official FP8 12B exists). No MTP drafter (no confirmed official 12B
# drafter, and MTP is throughput-only, irrelevant to the capability metric).
#
# Idempotent: hf resumes interrupted downloads and skips complete files.
# Mirrors infra/triton/scripts/download-models.sh.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
WEIGHTS_DIR="${WEIGHTS_DIR:-/data/triton/weights}"
REPO="google/gemma-4-12B-it"

# Prefer the project venv's hf; fall back to whatever is on PATH.
HF_BIN="${REPO_ROOT}/.venv/bin/hf"
[[ -x "${HF_BIN}" ]] || HF_BIN="$(command -v hf || true)"
if [[ -z "${HF_BIN}" || ! -x "${HF_BIN}" ]]; then
    echo "[pull-12b] huggingface CLI not found (.venv/bin/hf or hf on PATH)" >&2
    exit 1
fi

target="${WEIGHTS_DIR}/${REPO##*/}"
mkdir -p "${WEIGHTS_DIR}"

# hf_transfer = parallel multi-connection downloads (pulled in by [infra]).
export HF_HUB_ENABLE_HF_TRANSFER="${HF_HUB_ENABLE_HF_TRANSFER:-1}"

echo "[pull-12b] ${REPO} -> ${target}"
echo "[pull-12b] using ${HF_BIN}"
echo "[pull-12b] (re-running after success is a no-op)"

"${HF_BIN}" download "${REPO}" --local-dir "${target}"

echo "[pull-12b] done. disk usage:"
du -sh "${target}" 2>&1 | sed 's/^/    /'
echo "[pull-12b] sanity — config + safetensors present:"
ls -1 "${target}"/config.json "${target}"/*.safetensors 2>&1 | sed 's/^/    /' | head
