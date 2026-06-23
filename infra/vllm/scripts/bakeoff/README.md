# Gemma 4 12B bake-off — serve scripts

Serving harness for the 4-arm bake-off in
[`ADR-0024`](../../../../docs/adr/0024-gemma4-12b-data-parallel-fleet-evaluation.md)
and the protocol in
[`futures/gemma4-12b-fleet-preflight.md`](../../../../docs/journal/futures/gemma4-12b-fleet-preflight.md).

## ⚠️ Read first: the 31B is a SHARED host service

`:8050` (the live 31B, unit `gemma4-31b-vllm.service`) is **shared** —
its unit declares clients beyond gemma-forge (**quantum-ai-orchestrator**,
…). Stopping it for a bake-off window takes inference down for all of
them. Confirm nothing else is mid-run before you start.

The unit is `enabled` (boot-start) and `Restart=on-failure`. Use the
provided `stop-31b.sh` / `restore-31b.sh` — **not** `docker stop` — so the
service goes inactive cleanly instead of auto-restarting and re-grabbing
the GPUs. Don't reboot mid-window (a reboot brings the 31B back).

## The arms

| Arm | Script | Config | GPUs | Port(s) |
|---|---|---|---|---|
| A (control) | *(none — the live `:8050` service)* | 31B bf16 TP=4 + MTP | 0,1,2,3 | 8050 |
| B | `serve-12b-fp8-tp1.sh` | 12B FP8 TP=1 + MTP | 1 each | 8051–8054 |
| C | `serve-12b-bf16-tp2.sh` | 12B bf16 TP=2 + MTP | 2 | 8055 / 8056 |
| D | `serve-12b-fp8-tp2.sh` | 12B FP8 TP=2 (no MTP) | 2 | 8057 / 8058 |

## Run order

```bash
cd infra/vllm/scripts/bakeoff

# 0. free all 4 GPUs (guarded; warns about shared clients)
./stop-31b.sh

# --- ARM B: 4-instance data-parallel fleet ---
GPU=0 PORT=8051 ./serve-12b-fp8-tp1.sh &   # one per GPU, ports 8051..8054
GPU=1 PORT=8052 ./serve-12b-fp8-tp1.sh &
GPU=2 PORT=8053 ./serve-12b-fp8-tp1.sh &
GPU=3 PORT=8054 ./serve-12b-fp8-tp1.sh &
#   ... run Tier 1/2/3, then: docker rm -f $(docker ps -q --filter name=bakeoff-12b)

# --- ARM C: two bf16 TP=2 instances ---
GPUS=0,1 PORT=8055 ./serve-12b-bf16-tp2.sh &
GPUS=2,3 PORT=8056 ./serve-12b-bf16-tp2.sh &

# --- ARM D: two FP8 TP=2 instances (long context) ---
GPUS=0,1 PORT=8057 ./serve-12b-fp8-tp2.sh &
GPUS=2,3 PORT=8058 ./serve-12b-fp8-tp2.sh &

# N. tear down arms, bring the shared 31B back
docker rm -f $(docker ps -q --filter name=bakeoff-12b) 2>/dev/null || true
./restore-31b.sh
```

Each serve script preflights that (a) the weights exist under
`/data/triton/weights` and (b) its target GPU(s) are free — so it refuses
to launch if the 31B is still up. Bake-off containers are all named
`bakeoff-12b-*` for easy bulk teardown.

## Before the first run (preflight checklist)

Pull into `/data/triton/weights/` and pin the image — see the
"Pre-flight checks" section of the preflight doc:

- `gemma-4-12B-it` (bf16) + `gemma-4-12B-it-assistant` (MTP drafter)
- `gemma-4-12B-it-FP8` (prefer a vendor-published checkpoint over a
  self-produced quant, for Federal defensibility — same reasoning as
  ADR-0015's NVFP4 choice)
- the `vllm/vllm-openai:gemma4-unified` nightly (override `VLLM_IMAGE` to
  pin a digest)

## Overridable env vars

`GPU`, `GPUS`, `PORT`, `VLLM_IMAGE`, `WEIGHTS_DIR`, `MODEL`, `DRAFTER`,
`MAX_MODEL_LEN` (Arm D). Defaults match the table above.
