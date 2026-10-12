#!/bin/sh
# One-command readiness report for cli-langlisto.
set -e
if command -v cli-langlisto >/dev/null 2>&1; then
  cli-langlisto --version
  cli-langlisto doctor || exit 1
else
  echo "✗ cli-langlisto is not on PATH."
  echo "  Run scripts/setup.sh first, or open a new terminal."
  exit 1
fi
echo
echo "✓ ready. If doctor says 'no session', run: cli-langlisto login"
