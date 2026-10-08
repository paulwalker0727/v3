"""Lansator de TEST pentru serverul FastAPI local (proces separat). argv: db, port, [fault_json]. Injectarea de erori (kill/delay după commit) există DOAR aici."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

import uvicorn  # noqa: E402

import cimitir_cloud_http as ch  # noqa: E402

if __name__ == "__main__":
    fault = json.loads(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else None
    uvicorn.run(ch.create_app(sys.argv[1], fault=fault), host="127.0.0.1", port=int(sys.argv[2]), log_level="warning", access_log=False)
