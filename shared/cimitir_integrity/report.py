"""Export raport: JSON, CSV, Markdown. Fără date sensibile (vezi model.redact)."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Optional

from .model import CATEGORY_RO, SEVERITIES, SEVERITY_RO, Report, redact

_CSV_COLS = ["code", "severity", "severity_ro", "category_ro", "entity", "record_id", "uid", "cod_loc", "title", "description", "evidence",
             "recommendation", "auto_fixable_future"]


def _cell(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def to_json(rep: Report) -> str:
    return json.dumps(rep.to_dict(), ensure_ascii=False, indent=2, sort_keys=False)


def to_csv(rep: Report) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(_CSV_COLS)
    for d in rep.to_dict()["issues"]:
        d = dict(d, evidence=json.dumps(d["evidence"], ensure_ascii=False, sort_keys=True))
        w.writerow([_cell(d.get(c)) for c in _CSV_COLS])
    return buf.getvalue()


def to_markdown(rep: Report, limit_per_severity: Optional[int] = 200) -> str:
    d = rep.to_dict()
    s, m = d["summary"], d["meta"]
    out = ["# Raport verificare evidențe", "",
           f"- Bază: `{m.get('database', '')}` · data de referință {m.get('reference_date')} · durată {m.get('duration_s')} s · versiune {m.get('tool_version')}",
           f"- Mod: {m.get('mode')}", f"- Bază nemodificată (SHA-256 înainte = după): **{m.get('db_unchanged', 'necontrolat')}**",
           f"- {m.get('privacy')}", "", "## Sumar", "", "| Severitate | Număr |", "|---|---:|"]
    out += [f"| {SEVERITY_RO[k]} | {s['by_severity'][k]} |" for k in SEVERITIES]
    out += [f"| **Total** | **{s['total']}** |", "", "| Categorie | " + " | ".join(SEVERITY_RO[k] for k in SEVERITIES) + " |", "|---|" + "---:|" * 4]
    for cat, v in s["by_category"].items():
        out.append(f"| {CATEGORY_RO.get(cat, cat)} | " + " | ".join(str(v[k]) for k in SEVERITIES) + " |")
    if m.get("check_errors"):
        out += ["", "## Erori interne ale verificărilor", ""] + [f"- {e}" for e in m["check_errors"]]
    for sev in SEVERITIES:
        items = [i for i in d["issues"] if i["severity"] == sev]
        if not items:
            continue
        out += ["", f"## {SEVERITY_RO[sev].capitalize()} ({s['by_severity'][sev]})", ""]
        for i in items[:limit_per_severity] if limit_per_severity else items:
            out += [f"### {i['code']} · {i['entity']} #{i['record_id']}" + (f" · loc {i['cod_loc']}" if i.get("cod_loc") else ""),
                    f"- **Problemă:** {i['description']}", f"- **Dovezi:** `{json.dumps(i['evidence'], ensure_ascii=False, sort_keys=True)}`",
                    f"- **Recomandare:** {i['recommendation']}", f"- **Remediabil automat într-o etapă viitoare:** {'da' if i['auto_fixable_future'] else 'nu'}", ""]
        if limit_per_severity and len(items) > limit_per_severity:
            out.append(f"_… încă {len(items) - limit_per_severity} nelistate aici; vezi JSON/CSV._")
    return redact("\n".join(out)) + "\n"


def write_all(rep: Report, out_dir: str, stem: str = "verificare_evidente") -> dict:
    p = Path(out_dir)
    p.mkdir(parents=True, exist_ok=True)
    paths = {"json": p / f"{stem}.json", "csv": p / f"{stem}.csv", "md": p / f"{stem}.md"}
    paths["json"].write_text(to_json(rep), encoding="utf-8")
    paths["csv"].write_text(to_csv(rep), encoding="utf-8-sig")
    paths["md"].write_text(to_markdown(rep), encoding="utf-8")
    return {k: str(v) for k, v in paths.items()}
