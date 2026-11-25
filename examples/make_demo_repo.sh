#!/usr/bin/env bash
# Create examples/calc-demo: a fresh git repo copied from calc-template (4 failing tests).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
dest="$here/calc-demo"
rm -rf "$dest"
cp -r "$here/calc-template" "$dest"
git -C "$dest" init -q -b main
git -C "$dest" add -A
git -C "$dest" -c user.name=demo -c user.email=demo@localhost commit -qm "Calc demo with bugs"
echo "created $dest"
