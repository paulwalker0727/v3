"""Pornire: python -m tools.setup_manager [--port 8765] [--no-browser]. Ascultă exclusiv pe 127.0.0.1."""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="setup_manager", description="Cimitir Sync v2 — Setup Manager (TEST, local)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)
    if a.host not in ("127.0.0.1", "localhost"):
        print("REFUZ: Setup Manager ascultă doar pe 127.0.0.1 (conține chei/parole în memorie).", file=sys.stderr)
        return 2
    import uvicorn
    from .server import create_app
    app = create_app(port=a.port)
    url = f"http://127.0.0.1:{a.port}/"
    print(f"Setup Manager: {url}   (Mod: Dry Run / Check Only; Ctrl+C pentru oprire)")
    if not a.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning", server_header=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
