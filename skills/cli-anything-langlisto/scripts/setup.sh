#!/bin/sh
# Install cli-anything-langlisto for a teacher's machine.
# No sudo required. Prefers uv, then pipx, then pip --user, then a private venv.
SELF_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SKILL_DIR=$(dirname "$SELF_DIR")
ASSETS="$SKILL_DIR/assets"

WHEEL=""
for f in "$ASSETS"/*.whl; do
  [ -f "$f" ] && WHEEL="$f" && break
done
if [ -z "$WHEEL" ]; then
  echo "ERROR: no .whl found in $ASSETS — download the release wheel into that folder first." >&2
  exit 1
fi

PY=""
for p in python3 python; do
  if command -v "$p" >/dev/null 2>&1; then PY="$p"; break; fi
done
if [ -z "$PY" ]; then
  echo "ERROR: python 3.9+ not found in PATH. Ask the user to install Python first." >&2
  exit 1
fi

echo "Installing cli-anything-langlisto from: $WHEEL"
ok=0
if command -v uv >/dev/null 2>&1; then
  uv tool install --force "$WHEEL" && ok=1 || echo "uv install failed, trying pipx..."
fi
if [ "$ok" = 0 ] && command -v pipx >/dev/null 2>&1; then
  pipx install --force "$WHEEL" && ok=1 || echo "pipx install failed, trying pip..."
fi
if [ "$ok" = 0 ]; then
  "$PY" -m pip install --user --upgrade "$WHEEL" && ok=1 || true
fi
if [ "$ok" = 0 ]; then
  "$PY" -m venv "$HOME/.langlisto/venv" \
    && "$HOME/.langlisto/venv/bin/pip" install --upgrade "$WHEEL" \
    && ok=1 \
    && echo "NOTE: installed into $HOME/.langlisto/venv — add $HOME/.langlisto/venv/bin to PATH."
fi
if [ "$ok" = 0 ]; then
  echo "ERROR: installation failed. See the messages above." >&2
  exit 1
fi

echo
CLI_BIN=""
if [ -x "$HOME/.local/bin/cli-anything-langlisto" ]; then
  CLI_BIN="$HOME/.local/bin/cli-anything-langlisto"
elif command -v cli-anything-langlisto >/dev/null 2>&1; then
  CLI_BIN="cli-anything-langlisto"
fi
if [ -n "$CLI_BIN" ]; then
  echo "Installed. Current state:"
  "$CLI_BIN" doctor || true
  echo
  echo "Next step — run this personally in a terminal (password stays with you):"
  echo "    cli-anything-langlisto login"
else
  echo "Installed, but 'cli-anything-langlisto' is not on PATH in this shell."
  echo "Open a new terminal and run: cli-anything-langlisto doctor"
fi
