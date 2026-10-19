"""Raport redactat (fără secrete) al sesiunii Setup Manager."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

from .redact import redact, redact_obj


def render(public_cfg: Dict[str, Any], state: Dict[str, Any]) -> str:
    safe_cfg = {k: v for k, v in public_cfg.items() if not isinstance(v, dict)}
    secrets_set = {k: ("setat" if v.get("set") else "nesetat") for k, v in public_cfg.items() if isinstance(v, dict)}
    lines = ["# Raport Setup Manager — Cimitir Sync v2 (TEST)", f"Generat: {time.strftime('%Y-%m-%d %H:%M:%S')}", f"Mod curent: {state.get('mode')}", "",
             "## Configurație (fără secrete)", "```json", json.dumps(safe_cfg, indent=2, ensure_ascii=False), "```",
             "Secrete: " + ", ".join(f"{k}={v}" for k, v in secrets_set.items()), ""]
    checks = state.get("checks") or []
    if checks:
        lines += [f"## Verificări conexiuni (rulate la {state.get('checks_at', '?')}; rulează din nou după orice modificare)", "| # | Verificare | Rezultat | Detalii |", "|---|---|---|---|"]
        lines += [f"| {i+1} | {c['title']} | **{c['status']}** | {str(c['detail']).replace('|', '/')} |" for i, c in enumerate(checks)]
        lines.append("")
    plan = state.get("plan")
    if plan:
        lines += [f"## Plan de migrare (generat la {state.get('plan_at', '?')})", f"Executabil: {plan['executable']}  |  Hash: `{plan['plan_hash']}`"]
        lines += [f"- {s['version']:03d} {s['name']}: {s['action']} — {s['why']}" for s in plan["steps"]]
        lines += [f"- BLOCAT: {b}" for b in plan["blocked"]]
        lines.append(f"- Stări 022+: {plan['summary']}")
        lines.append("")
    for title, key in (("Docker", "docker"), ("Sonde asociere", "pairing_probe"), ("Checklist manual", "checklist")):
        if state.get(key):
            lines += [f"## {title}", "```json", json.dumps(redact_obj(state[key]), indent=2, ensure_ascii=False, default=str)[:6000], "```", ""]
    return redact("\n".join(lines))


def save(home: Path, text: str) -> Path:
    d = Path(home) / "reports"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"report-{time.strftime('%Y%m%d-%H%M%S')}.md"
    p.write_text(text, encoding="utf-8")
    return p
