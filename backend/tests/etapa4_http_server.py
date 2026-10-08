"""Lansator de TEST pentru serverul FastAPI local (proces separat). argv: db, port, [fault_json], [clock_offset_file]. Injectarea de erori (kill/delay după commit)
și ceasul decalat (Pasul 5.1: expirarea planului pe ceasul serverului) există DOAR aici."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

import uvicorn  # noqa: E402

import cimitir_cloud_http as ch  # noqa: E402


def _clock(offset_file):
    def now():
        try:
            return time.time() + float(Path(offset_file).read_text() or 0)
        except (OSError, ValueError):
            return time.time()
    return now


if __name__ == "__main__":
    fault = json.loads(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else None
    clock = _clock(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] else time.time
    uvicorn.run(ch.create_app(sys.argv[1], fault=fault, clock=clock), host="127.0.0.1", port=int(sys.argv[2]), log_level="warning", access_log=False)
