"""F-01 — funcțiile de dată din Mobile (mobile_ext.js) rulate sub Node, fără browser: aceleași reguli ca shared/cimitir_dates.py."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import cimitir_dates as cd

EXT = Path(__file__).resolve().parents[1] / "static" / "mobile_ext.js"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="Node.js indisponibil")

CASES = ["05.03.2026", "5.3.2026", "2026-03-05", "05/03/2026", "05-03-2026", "05032026", " 05.03.2026 ", "29.02.2024", "29.02.2025", "31.02.2026",
         "00.01.2026", "2026-13-01", "2026-02-30", "circa 1940", "05.03.26", "2026/03/05", "", None, "2026-03-05T10:00:00", "20260305", "1.1"]


def _node(expr):
    code = f"import * as e from {json.dumps(EXT.as_uri())}; const C={json.dumps(CASES)}; console.log(JSON.stringify({expr}));"
    out = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr[-1500:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_js_isoOf_matches_python_to_iso_for_every_case():
    got = _node("C.map(v => e.isoOf(v))")
    for raw, js in zip(CASES, got):
        py = cd.parse_date(raw)
        assert js == (py.isoformat() if py else None), raw


def test_js_dateState_never_drops_a_value():
    for raw, st in zip(CASES, _node("C.map(v => e.dateState(v))")):
        if raw is None or str(raw).strip() == "":
            assert st["kind"] == "empty"
        elif cd.parse_date(raw):
            assert st["kind"] == "ok" and st["iso"] == cd.parse_date(raw).isoformat()
        else:
            assert st["kind"] == "keep" and st["raw"] == str(raw), raw      # neinterpretabil: păstrat, nu golit


def test_js_validation_and_today_are_local_not_utc():
    assert _node("[e.isValidIso('2026-03-05'), e.isValidIso('2026-02-30'), e.isValidIso('05.03.2026'), e.isValidIso('')]") == [True, False, False, False]
    assert _node("e.todayLocal(new Date(2026, 0, 1, 0, 30))") == "2026-01-01"       # la 00:30 local nu devine ziua precedentă (toISOString ar fi dat UTC)
