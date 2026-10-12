#!/bin/sh
# One-command readiness report for cli-anything-langlisto.
set -e
if command -v cli-anything-langlisto >/dev/null 2>&1; then
  cli-anything-langlisto --version
  cli-anything-langlisto doctor || exit 1
else
  echo "✗ cli-anything-langlisto is not on PATH."
  echo "  Run scripts/setup.sh first, or open a new terminal."
  exit 1
fi
echo
echo "✓ ready. If doctor says 'no session', run: cli-anything-langlisto login"
