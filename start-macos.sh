#!/bin/bash
# CIMITIR pe macOS (Apple Silicon): pornește aplicația din mediul virtual local (.venv).
# Nu instalează nimic, nu creează servicii, nu pornește nimic la login. Toate datele stau în ~/CIMITIR-MAC.
#
#   ./start-macos.sh                 pornește portalul local (127.0.0.1) + aplicația desktop (ca pe Windows: start_local.py)
#   ./start-macos.sh --desktop-only  doar aplicația desktop
#   ./start-macos.sh --check         afișează căile/mediul și verifică importurile, fără să pornească interfața
#   ./start-macos.sh --hwid          afișează amprenta dispozitivului (pentru emiterea licenței de Mac)
#   ./start-macos.sh --demo          creează o bază FICTIVĂ (admin_demo/user_demo, parole afișate o dată) în folderul de date; refuză dacă există deja una
#
# Variabile opționale: CIMITIR_MAC_HOME (implicit ~/CIMITIR-MAC), CIMITIR_DATA_DIR (implicit $CIMITIR_MAC_HOME/date/cimitir-data),
#   CIMITIR_MAC_USE_PROJECT_ENV=1 (permite citirea fișierelor .env din proiect; implicit sunt ignorate în etapa de test),
#   CIMITIR_DISABLE_UPDATE_CHECK=0 (reactivează verificarea versiunii noi pe GitHub).
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
  echo "Acest script este doar pentru macOS." >&2
  exit 1
fi
if [ "$(uname -m)" != "arm64" ]; then
  echo "Avertisment: arhitectura este $(uname -m), nu arm64 (Apple Silicon)." >&2
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CIMITIR_MAC_HOME="${CIMITIR_MAC_HOME:-$HOME/CIMITIR-MAC}"
PY="$REPO_DIR/.venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "Lipsește mediul virtual: $PY" >&2
  echo "Creează-l o singură dată cu:  $REPO_DIR/scripts/macos/setup-macos.sh" >&2
  exit 1
fi

mkdir -p "$CIMITIR_MAC_HOME/date/cimitir-data" "$CIMITIR_MAC_HOME/cache/tmp" "$CIMITIR_MAC_HOME/cache/pycache"

export CIMITIR_DATA_DIR="${CIMITIR_DATA_DIR:-$CIMITIR_MAC_HOME/date/cimitir-data}"
export TMPDIR="$CIMITIR_MAC_HOME/cache/tmp"
export XDG_CACHE_HOME="$CIMITIR_MAC_HOME/cache"
export PYTHONPYCACHEPREFIX="$CIMITIR_MAC_HOME/cache/pycache"
export CIMITIR_DISABLE_UPDATE_CHECK="${CIMITIR_DISABLE_UPDATE_CHECK:-1}"
if [ "${CIMITIR_MAC_USE_PROJECT_ENV:-0}" != "1" ]; then
  export CIMITIR_IGNORE_PROJECT_ENV=1
fi

cd "$REPO_DIR/desktop"

case "${1:-}" in
  --check)
    echo "Python:       $("$PY" --version) ($PY)"
    echo "Date:         $CIMITIR_DATA_DIR"
    echo "Cache/tmp:    $CIMITIR_MAC_HOME/cache"
    echo "Config/log:   $CIMITIR_MAC_HOME/date/{config,logs,audit}"
    QT_QPA_PLATFORM=offscreen "$PY" - <<'PYEOF'
import importlib, sys
sys.path.insert(0, "../shared")
bad = []
for name in ("PySide6.QtWidgets", "docx", "reportlab", "openpyxl", "qrcode", "PIL", "cryptography", "requests", "fastapi", "uvicorn", "psycopg", "httpx"):
    try:
        importlib.import_module(name)
    except Exception as exc:
        bad.append(f"{name}: {exc}")
import cimitir_config
print("Baza locală:  ", cimitir_config.db_path())
print("Importuri:    ", "OK" if not bad else "LIPSĂ -> " + "; ".join(bad))
sys.exit(1 if bad else 0)
PYEOF
    ;;
  --hwid)
    exec "$PY" -c "import sys; sys.path.insert(0, '.'); import macos_support; macos_support.apply(); from license import get_hwid, get_nume_pc; print('Nume PC:', get_nume_pc()); print('HWID:   ', get_hwid()); print('Licență:', __import__('license').LICENSE_FILE)"
    ;;
  --demo)
    exec "$PY" ../scripts/make_demo_db.py --data-dir "$CIMITIR_DATA_DIR"
    ;;
  *)
    exec "$PY" start_local.py "$@"
    ;;
esac
