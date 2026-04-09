#!/usr/bin/env bash
#
# download-models.sh — populate /data/triton/weights/ with Gemma 4 weights.
#
# Idempotent: huggingface_hub's download tool resumes interrupted downloads
# and skips files that are already present and complete. Re-running this
# script after a successful run is essentially a no-op.
#
# The default model lineup matches ADR-0015 Option A:
#   - google/gemma-4-31B-it  -> Architect + Worker (shared, tp=2)
#   - google/gemma-4-E4B-it  -> Auditor
#   - google/gemma-4-E2B-it  -> Sentry
#
# Total disk usage: ~88 GB (31B is ~62 GB, E4B is ~16 GB, E2B is ~10 GB).
#
# All three Gemma 4 variants are released under Apache 2.0 with NO HF
# gating or license click-through, so this works for unauthenticated
# downloads. If you have an HF token (faster rate limits, fewer
# throttles), set HF_TOKEN in your environment before running.
#
# Requires: the project venv with the [infra] optional dependency group
# installed. From the repo root:
#   uv venv && uv pip install --python .venv/bin/python -e ".[dev,infra]"

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
WEIGHTS_DIR="${WEIGHTS_DIR:-/data/triton/weights}"
HF_BIN="${REPO_ROOT}/.venv/bin/hf"

log() { printf "\033[1;32m[download]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[download]\033[0m %s\n" "$*" >&2; }

if [[ ! -x "${HF_BIN}" ]]; then
    err "huggingface CLI not found at ${HF_BIN}"
    err "create the project venv first:"
    err "    cd ${REPO_ROOT} && uv venv && uv pip install --python .venv/bin/python -e \".[dev,infra]\""
    exit 1
fi

if [[ ! -d "${WEIGHTS_DIR}" ]]; then
    log "creating ${WEIGHTS_DIR}"
    mkdir -p "${WEIGHTS_DIR}"
fi

MODELS=(
    "google/gemma-4-31B-it"
    "google/gemma-4-E4B-it"
    "google/gemma-4-E2B-it"
)

# hf-transfer enables parallel multi-connection downloads; pulled in by
# the [infra] optional dep group as hf_transfer.
export HF_HUB_ENABLE_HF_TRANSFER=1

log "downloading ${#MODELS[@]} Gemma 4 models to ${WEIGHTS_DIR}/"
log "(re-running this script after success is a no-op)"

for repo in "${MODELS[@]}"; do
    name="${repo##*/}"
    target="${WEIGHTS_DIR}/${name}"
    log ""
    log "  ${repo} -> ${target}"
    "${HF_BIN}" download "${repo}" --local-dir "${target}"
done

log ""
log "downloads complete."
log ""
log "disk usage:"
du -sh "${WEIGHTS_DIR}"/* 2>&1 | sed 's/^/    /'
log ""
log "next: run infra/triton/scripts/install.sh (if you haven't already)"
log "to deploy the Triton model repository definitions, then start the"
log "Triton systemd units."
