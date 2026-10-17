"""Export local READ-ONLY pentru un singur poligon TEST din MAIN.

Nu trimite nimic la cloud și nu include date personale. Fișierul rezultat
conține identificatorul tehnic intern, necesar corelării viitoare, dar nu
îl afișează utilizatorului aplicației.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from pathlib import Path


def normalized_points(raw):
    points = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(points, list) or len(points) < 3:
        raise ValueError("poligon invalid: minimum 3 puncte")
    for point in points:
        if (not isinstance(point, (list, tuple)) or len(point) < 2
                or any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in point[:2])):
            raise ValueError("coordonate invalide")
    return points


def extract(db_path: Path):
    if not db_path.is_file():
        raise FileNotFoundError("Baza SQLite MAIN nu există")
    conn = sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cols = {r['name'] for r in conn.execute('PRAGMA table_info(locuri_de_veci)')}
        required = {'uid', 'points'}
        if not required.issubset(cols):
            raise ValueError('Lipsesc coloanele uid/points')
        select = ['uid', 'points'] + [x for x in ('cod_loc','sector','parcela','rand','numar') if x in cols]
        rows = conn.execute('SELECT ' + ','.join(select) + ' FROM locuri_de_veci').fetchall()
        plots = []
        for row in rows:
            try:
                pts = normalized_points(row['points'])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not row['uid']:
                raise ValueError('Poligonul desenat nu are UID; export oprit')
            plots.append({k: (pts if k == 'points' else row[k]) for k in select})
        if len(plots) != 1:
            raise ValueError(f'Se așteaptă exact un poligon de test desenat; găsite: {len(plots)}')
        payload = {'format':'cimitir-mobile-v2-polygon-diagnostic-v1', 'source':'MAIN-local-test', 'plots':plots}
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        digest = hashlib.sha256(canonical).hexdigest()
        return {'manifest': {'format':payload['format'], 'count':1, 'sha256':digest}, 'payload':payload}
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description='Exportă un singur poligon TEST, fără upload în cloud')
    parser.add_argument('--db', required=True)
    parser.add_argument('--output', default='mobile-v2-polygon-test.json')
    args = parser.parse_args()
    try:
        doc = extract(Path(args.db))
        Path(args.output).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f'Export oprit: {exc}')
        return 2
    print(f"Export local OK: {doc['manifest']['count']} poligon; SHA-256: {doc['manifest']['sha256']}")
    print('Nu s-a transmis nimic în cloud. Nu încărca JSON-ul pe servicii publice.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
