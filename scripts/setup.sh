#!/bin/bash
# Install or upgrade the skill's private yt-dlp in ../.venv.
# A private venv because Homebrew's python@3.14 can ship a pyexpat that fails to load on
# older macOS point releases ("Symbol not found: _XML_SetAllocTrackerActivationThreshold"),
# which breaks the Homebrew yt-dlp. Picks the first python3 whose pyexpat actually loads.
set -euo pipefail
venv="$(cd "$(dirname "$0")/.." && pwd)/.venv"

if [ ! -x "$venv/bin/python" ]; then
  py=""
  for cand in python3.13 python3.12 python3.11 python3 /usr/bin/python3; do
    p="$(command -v "$cand" 2>/dev/null)" || continue
    if "$p" -c 'import sys, pyexpat; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
      py="$p"; break
    fi
  done
  [ -n "$py" ] || { echo "No python3 >= 3.10 with a working pyexpat found" >&2; exit 1; }
  "$py" -m venv "$venv"
fi

"$venv/bin/python" -m pip install --quiet --upgrade pip
"$venv/bin/python" -m pip install --quiet --upgrade "yt-dlp[default]"
echo "yt-dlp $("$venv/bin/yt-dlp" --version) ready in $venv"
