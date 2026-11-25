#!/usr/bin/env bash
# Make the candidate the model your teams use day to day.
# EDIT for your setup. This version records it; point your production server at it.
set -euo pipefail
mkdir -p checkpoints
ln -sfn "$(realpath "$1")" checkpoints/current
echo "promoted $1 -> checkpoints/current"
