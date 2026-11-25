#!/usr/bin/env bash
# Serve the candidate adapter as "candidate" so examples/team-local.toml benchmarks it.
# EDIT for your setup. This version restarts a local vLLM server in the background.
set -euo pipefail
adapter="$1"
base="${COLONY_BASE_MODEL:-Qwen/Qwen3-8B}"
pkill -f "served-model-name candidate" || true
nohup vllm serve "$base" --served-model-name candidate-base \
  --enable-lora --lora-modules "candidate=$adapter" \
  --enable-auto-tool-choice --tool-call-parser hermes \
  --port "${COLONY_CANDIDATE_PORT:-8000}" > loop/vllm-candidate.log 2>&1 &
for _ in $(seq 1 120); do
  curl -sf "http://localhost:${COLONY_CANDIDATE_PORT:-8000}/v1/models" >/dev/null && exit 0
  sleep 5
done
echo "candidate server did not come up; see loop/vllm-candidate.log" >&2
exit 1
