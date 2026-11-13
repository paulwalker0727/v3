#!/bin/bash
# Creează o singură dată mediul virtual .venv al proiectului și instalează dependențele (macOS arm64, Python 3.11).
# Nu folosește Homebrew, Docker sau instalări globale. Cache-urile pip/uv rămân în ~/CIMITIR-MAC/cache.
#
#   scripts/macos/setup-macos.sh                    caută Python 3.11 în ~/CIMITIR-MAC/python, apoi în PATH
#   PYTHON=/cale/python3.11 scripts/macos/setup-macos.sh
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
  echo "Acest script este doar pentru macOS." >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export CIMITIR_MAC_HOME="${CIMITIR_MAC_HOME:-$HOME/CIMITIR-MAC}"
mkdir -p "$CIMITIR_MAC_HOME/cache/pip" "$CIMITIR_MAC_HOME/cache/uv"
export PIP_CACHE_DIR="$CIMITIR_MAC_HOME/cache/pip"
export UV_CACHE_DIR="$CIMITIR_MAC_HOME/cache/uv"

PYTHON_BIN="${PYTHON:-}"
if [ -z "$PYTHON_BIN" ]; then
  for candidate in "$CIMITIR_MAC_HOME/python/bin/python3.11" "$CIMITIR_MAC_HOME/python/bin/python3" "$(command -v python3.11 || true)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then PYTHON_BIN="$candidate"; break; fi
  done
fi
if [ -z "$PYTHON_BIN" ]; then
  echo "Nu am găsit Python 3.11. Setează PYTHON=/cale/către/python3.11" >&2
  exit 1
fi
case "$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')" in
  3.11) ;;
  *) echo "Este necesar Python 3.11 (găsit: $("$PYTHON_BIN" --version))" >&2; exit 1 ;;
esac

UV_BIN=""
for candidate in "$CIMITIR_MAC_HOME/python/bin/uv" "$(command -v uv || true)"; do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then UV_BIN="$candidate"; break; fi
done

cd "$REPO_DIR"
if [ -n "$UV_BIN" ]; then
  "$UV_BIN" venv --python "$PYTHON_BIN" .venv
  "$UV_BIN" pip install --python .venv/bin/python -r desktop/requirements-macos.txt
else
  "$PYTHON_BIN" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r desktop/requirements-macos.txt
fi
echo "Gata. Verifică: ./start-macos.sh --check"
