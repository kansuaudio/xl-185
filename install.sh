#!/bin/zsh
# Usage: ./install.sh ["<path to the installed XL-185 Sequencer.amxd>"]
# Builds device/XL-185 Sequencer.amxd from the original device and the scripts. With a path it also
# installs the build there: the file it replaces is kept in backups/, the copy is atomic.
set -e
cd "$(dirname "$0")"
OUT="device/XL-185 Sequencer.amxd"
TMP="device/.building.amxd"
python3 -I -B build_ml185.py "ML-185 Sequencer (original).amxd" ml185scale.js "$TMP"
mv -f "$TMP" "$OUT"
if [ -n "$1" ]; then
  if [ -f "$1" ]; then mkdir -p backups && cp "$1" "backups/XL-185 Sequencer ($(date +%Y%m%d-%H%M%S)).amxd"; fi
  cp "$OUT" "$1.installing" && mv -f "$1.installing" "$1"
  ls -la "$1"
fi
