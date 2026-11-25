#!/usr/bin/env bash
# Undo deploy_candidate.sh after a candidate fails the gate. EDIT for your setup.
set -euo pipefail
pkill -f "lora-modules candidate=" || true
echo "candidate server stopped"
