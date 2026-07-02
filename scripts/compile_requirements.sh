#!/usr/bin/env bash
# Regenerate requirements.txt (locked, with hashes) from requirements.in.
#
# pip-compile resolves against the Python it runs on, so the lock file is only
# valid for that Python version. The Dockerfile base image MUST match — both
# use Python 3.13 (see Dockerfile FROM and venv). If you bump one, bump both
# and recompile.
#
# Usage:
#   scripts/compile_requirements.sh
#
# Edit requirements.in to add/upgrade a package, then run this script and
# commit both files. Never hand-edit requirements.txt.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$ROOT_DIR"

# Prefer the project venv if present, else fall back to PATH.
if [ -x "$ROOT_DIR/venv/bin/pip-compile" ]; then
  PIP_COMPILE="$ROOT_DIR/venv/bin/pip-compile"
else
  PIP_COMPILE="$(command -v pip-compile)" || {
    echo "Error: pip-compile not found. Install pip-tools (pip install pip-tools)." >&2
    exit 1
  }
fi

"$PIP_COMPILE" \
  --generate-hashes \
  --allow-unsafe \
  --resolver=backtracking \
  -r requirements.in \
  --output-file requirements.txt

echo "✓ requirements.txt regenerated from requirements.in"
