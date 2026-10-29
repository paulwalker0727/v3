"""cimitir_portable.py — backup PORTABIL al unui registru (export → verificare → previzualizare → import sigur → raport).

Arhiva `.cimitir-portabil` este un ZIP cu: `manifest.json` (versiune, identitate organizație/cimitir, numere pe entități, sha256 + dimensiune pentru fiecare fișier),
`database.sqlite` (copie consistentă, SANITIZATĂ), `media/…` (fotografii, documente, imaginea hărții în `media/_map/`).
Sanitizare la export (decizie de securitate/identitate): se elimină legătura Cloud (BINDING_SETTINGS, cheia/secretul NU sunt niciodată în arhivă), starea de sincronizare
(UNBOUND), coada de sincronizare și conturile locale (`users`, hash-uri de parole). UID-urile organizației, cimitirului și ale rândurilor se păstrează.
Importul NU suprascrie nimic: refuză un cimitir existent în catalog, cere confirmarea explicită a organizației și cimitirului destinație, detectează duplicate,
extrage într-un folder temporar, reverifică, apoi mută atomic; la orice eroare face rollback. Registrul importat rămâne UNBOUND: încărcarea în Cloud se face doar prin
Asocierea MAIN (Flux B: previzualizare, aprobare, checkpoint, reluare, idempotency). Nu se sincronizează nimic automat.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import cimitir_bootstrap as bs
import cimitir_manifest as mf
import cimitir_registry as reg

FORMAT, VERSION, EXT = "cimitir-portable", 1, ".cimitir-portabil"
DB_ARC, MAN_ARC, MEDIA_ARC = "database.sqlite", "manifest.json", "media/"
MAX_FILES, MAX_BYTES = 200_000, 64 * 1024 ** 3
SYNC_KEYS = ("sync_state", "cloud_assoc_plan", "cloud_assoc_unverified", "cloud_sync_last", "sync_map_version", "sync_queued_image", "sync_image_version")
CLEAR_TABLES = ("users", "sync_ops", "cloud_sync_outbox", "cloud_sync_conflicts", "cloud_sync_state")
Progress = Optional[Callable[[str, int, int], None]]


class PortableError(Exception):
    def __init__(self, message: str, kind: str = "error"):
        super().__init__(message)
        self.kind = kind


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _safe_name(name: str) -> str:
    n = name.replace("\\", "/")
    if n.startswith("/") or ".." in n.split("/") or ":" in n.split("/")[0] or "\x00" in n:
        raise PortableError(f"cale nesigură în arhivă: {name!r}", "unsafe")
    return n


def _tables(c) -> set:
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _p(progress: Progress, step: str, i: int, n: int) -> None:
    if progress:
        progress(step, i, n)


# =================================================================== EXPORT
def export_registry(binding, dest: Any, org: Dict[str, Any], cem: Dict[str, Any], progress: Progress = None) -> Dict[str, Any]:
    dest = Path(dest)
    if dest.exists():
        raise PortableError("Fișierul destinație există deja; nu se suprascrie.", "exists")
    if binding.contains(dest):
        raise PortableError("Arhiva nu poate fi creată în interiorul registrului exportat.", "dest")
    snap = mf.local_snapshot(binding)
    warnings: List[str] = []
    if snap.get("fk_check_violations"):
        warnings.append(f"{snap['fk_check_violations']} încălcări de chei străine în baza locală (datele se exportă așa cum sunt).")
    tmp = Path(tempfile.mkdtemp(prefix="cimitir-export-", dir=str(dest.parent) if dest.parent.is_dir() else None))
    try:
        # 1) copie consistentă (API backup SQLite, include WAL)
        db_tmp = tmp / DB_ARC
        src = sqlite3.connect(f"file:{binding.database}?mode=ro", uri=True, timeout=15)
        dst = sqlite3.connect(db_tmp)
        try:
            src.backup(dst)
        finally:
            src.close()
        files: List[Dict[str, Any]] = []
        media_rel: Dict[str, Path] = {}
        # 2) media: tot ce e referit (fotografii/documente) + imaginea hărții; ce e în afara folderului media primește cale relativă în _external/
        remap: List[tuple] = []
        for it in snap["media"]:
            if it.get("_path"):
                f = Path(it["_path"])
                rel = f.relative_to(Path(binding.media)).as_posix() if binding.media in f.parents else "_external/" + (_sha(f)[:16] + "-" + f.name)
                media_rel[rel] = f
                if binding.media not in f.parents:
                    remap.append((it["_local_ref"], rel))
            else:
                warnings.append(f"Fișier lipsit pe disc (nu se exportă): {it['name']} ({it['kind']}).")
        for f in Path(binding.media).rglob("*") if Path(binding.media).is_dir() else []:
            if f.is_file():
                media_rel.setdefault(f.relative_to(binding.media).as_posix(), f)
        c = sqlite3.connect(db_tmp)
        try:
            map_path = (c.execute("SELECT value FROM settings WHERE key='map_path'").fetchone() or [""])[0].strip()
            map_info = None
            if map_path and Path(map_path).is_file():
                mp = Path(map_path)
                rel = "_map/" + mp.name
                media_rel[rel] = mp
                map_info = {"file": rel, "sha256": _sha(mp)}
            elif map_path:
                warnings.append("Imaginea hărții din setări nu există pe disc și nu se exportă.")
            for ref, rel in remap:
                kind, _, rid = ref.partition(":")
                tbl = {"document": "documents", "photo": "photos"}.get(kind)
                if tbl:
                    c.execute(f"UPDATE {tbl} SET file_path=? WHERE id=?", (rel, int(rid)))
            # 3) sanitizare: legătura Cloud, starea de sync, coada, conturile locale
            tb = _tables(c)
            removed = [k for k in bs.BINDING_SETTINGS + SYNC_KEYS if c.execute("DELETE FROM settings WHERE key=?", (k,)).rowcount]
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('sync_state','UNBOUND')")
            cleared = []
            for t in CLEAR_TABLES:
                if t in tb:
                    c.execute(f"DELETE FROM {t}")
                    cleared.append(t)
            c.commit()
            c.execute("VACUUM")
        finally:
            c.close()
        # 4) manifest
        total = len(media_rel) + 1
        entries = [{"path": DB_ARC, "size": db_tmp.stat().st_size, "sha256": _sha(db_tmp)}]
        for i, (rel, f) in enumerate(sorted(media_rel.items()), 1):
            _p(progress, "hash", i, total)
            entries.append({"path": MEDIA_ARC + _safe_name(rel), "size": f.stat().st_size, "sha256": _sha(f)})
        counts = {e: len(rows) for e, rows in snap["entities"].items()}
        manifest = {"format": FORMAT, "version": VERSION, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "app_version": snap.get("app_version"),
                    "schema_version": snap.get("schema_version"),
                    "organization": {"uid": binding.organization_uid, "official_name": org.get("official_name", ""), "display_name": org.get("display_name", ""),
                                     "org_type": org.get("org_type", "")},
                    "cemetery": {"uid": binding.cemetery_uid, "name": cem.get("name", "")},
                    "entities": counts, "media": {"count": len(media_rel), "map": map_info},
                    "sanitized": {"removed_settings": removed, "cleared_tables": cleared, "sync_state": "UNBOUND"},
                    "files": entries, "warnings": warnings}
        (tmp / MAN_ARC).write_text(json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        # 5) ZIP (scriere pe .part, apoi redenumire; self-check)
        part = dest.with_name(dest.name + ".part")
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(tmp / MAN_ARC, MAN_ARC)
            z.write(db_tmp, DB_ARC)
            for i, (rel, f) in enumerate(sorted(media_rel.items()), 1):
                _p(progress, "write", i, len(media_rel))
                z.write(f, MEDIA_ARC + rel, compress_type=zipfile.ZIP_STORED)
        os.replace(part, dest)
        res = verify_archive(dest)
        if not res["ok"]:
            dest.unlink(missing_ok=True)
            raise PortableError("Autoverificarea arhivei a eșuat: " + "; ".join(res["errors"][:3]), "selfcheck")
        return {"path": str(dest), "bytes": dest.stat().st_size, "sha256": _sha(dest), "entities": counts, "media": len(media_rel), "warnings": warnings,
                "sanitized": manifest["sanitized"]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        dest.with_name(dest.name + ".part").unlink(missing_ok=True)


# =================================================================== VERIFICARE
def read_manifest(path: Any) -> Dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as z:
            return json.loads(z.read(MAN_ARC).decode("utf-8"))
    except (zipfile.BadZipFile, KeyError, ValueError, OSError):
        raise PortableError("Fișierul nu este o arhivă portabilă validă.", "format") from None


def verify_archive(path: Any, progress: Progress = None) -> Dict[str, Any]:
    errors: List[str] = []
    try:
        man = read_manifest(path)
    except PortableError as exc:
        return {"ok": False, "errors": [str(exc)], "manifest": None}
    if man.get("format") != FORMAT:
        errors.append("format necunoscut")
    if not isinstance(man.get("version"), int) or man["version"] > VERSION:
        errors.append(f"versiune de arhivă neacceptată ({man.get('version')}); actualizează aplicația")
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len(names) > MAX_FILES or sum(i.file_size for i in z.infolist()) > MAX_BYTES:
            errors.append("arhiva depășește limitele permise")
        declared = {e["path"]: e for e in man.get("files", [])}
        try:
            for n in names:
                _safe_name(n)
        except PortableError as exc:
            errors.append(str(exc))
        for n in names:
            if n != MAN_ARC and not n.endswith("/") and n not in declared:
                errors.append(f"fișier necunoscut în arhivă: {n}")
        for i, (n, e) in enumerate(sorted(declared.items()), 1):
            _p(progress, "verify", i, len(declared))
            if n not in names:
                errors.append(f"lipsește din arhivă: {n}")
                continue
            h, size = hashlib.sha256(), 0
            with z.open(n) as f:
                for b in iter(lambda: f.read(1 << 20), b""):
                    h.update(b)
                    size += len(b)
            if size != e["size"] or h.hexdigest() != e["sha256"]:
                errors.append(f"integritate: {n}")
        if DB_ARC in names and not errors:
            with tempfile.TemporaryDirectory() as td:
                z.extract(DB_ARC, td)
                c = sqlite3.connect(Path(td) / DB_ARC)
                try:
                    ic = c.execute("PRAGMA integrity_check").fetchone()[0]
                    if ic != "ok":
                        errors.append("baza SQLite din arhivă este coruptă")
                finally:
                    c.close()
    return {"ok": not errors, "errors": errors, "manifest": man}


# =================================================================== PREVIZUALIZARE (nu scrie nimic)
def preview(path: Any, catalog: reg.RegistryCatalog, progress: Progress = None) -> Dict[str, Any]:
    v = verify_archive(path, progress)
    if not v["ok"]:
        raise PortableError("Arhiva nu a trecut verificarea: " + "; ".join(v["errors"][:3]), "verify")
    man = v["manifest"]
    org_uid, cem_uid = man["organization"]["uid"], man["cemetery"]["uid"]
    reg.valid_uid(org_uid), reg.valid_uid(cem_uid)
    blockers: List[str] = []
    warnings: List[str] = list(man.get("warnings", []))
    org = catalog.get_organization(org_uid)
    cem = catalog.get_cemetery(cem_uid)
    if cem:
        blockers.append("Cimitirul cu acest UID există deja în catalogul local: importul nu suprascrie nimic (EXISTS).")
    if cem is None and org and not org["active"]:
        blockers.append("Organizația există dar este arhivată.")
    if org is None:
        same = [o for o in catalog.list_organizations(include_inactive=True) if o["official_name"].strip().lower() == man["organization"]["official_name"].strip().lower()]
        if same:
            warnings.append("Există deja o organizație cu aceeași denumire dar alt UID: importul ar crea o organizație SEPARATĂ (identitatea originală se păstrează). Confirmă explicit.")
    if cem is None:
        same_c = [x for x in catalog.list_cemeteries(include_archived=True) if x["name"].strip().lower() == man["cemetery"]["name"].strip().lower() and x["organization_uid"] == org_uid]
        if same_c:
            warnings.append("Există deja un cimitir cu același nume în această organizație (alt UID): posibil duplicat.")
    dup = _duplicate_plot_codes(path)
    if dup:
        warnings.append(f"Coduri de loc duplicate în arhivă: {', '.join(dup[:5])}{'…' if len(dup) > 5 else ''}.")
    return {"organization": man["organization"], "cemetery": man["cemetery"], "entities": man["entities"], "media": man["media"], "created_at": man["created_at"],
            "organization_exists": bool(org), "blockers": blockers, "warnings": warnings, "can_import": not blockers, "sanitized": man.get("sanitized")}


def _duplicate_plot_codes(path: Any) -> List[str]:
    with zipfile.ZipFile(path) as z, tempfile.TemporaryDirectory() as td:
        z.extract(DB_ARC, td)
        c = sqlite3.connect(Path(td) / DB_ARC)
        try:
            if "locuri_de_veci" not in _tables(c):
                return []
            return [r[0] for r in c.execute("SELECT cod_loc FROM locuri_de_veci GROUP BY cod_loc HAVING COUNT(*)>1")]
        finally:
            c.close()


# =================================================================== IMPORT
def cleanup_stale(catalog: reg.RegistryCatalog) -> int:
    base = catalog.data_dir / reg.REGISTRIES_DIR
    n = 0
    for d in base.glob(".import-*") if base.is_dir() else []:
        shutil.rmtree(d, ignore_errors=True)
        n += 1
    return n


def import_archive(path: Any, catalog: reg.RegistryCatalog, confirm_organization_uid: str, confirm_cemetery_uid: str, accept_warnings: bool = False,
                   progress: Progress = None) -> Dict[str, Any]:
    pv = preview(path, catalog, progress)
    man_org, man_cem = pv["organization"], pv["cemetery"]
    if (confirm_organization_uid, confirm_cemetery_uid) != (man_org["uid"], man_cem["uid"]):
        raise PortableError("Organizația/cimitirul destinație confirmate nu corespund arhivei; nimic nu a fost modificat.", "confirm")
    if pv["blockers"]:
        raise PortableError(pv["blockers"][0], "blocked")
    if pv["warnings"] and not accept_warnings:
        raise PortableError("Importul are avertismente care cer aprobare explicită: " + " | ".join(pv["warnings"][:3]), "warnings")
    cleanup_stale(catalog)
    base = catalog.data_dir / reg.REGISTRIES_DIR
    base.mkdir(parents=True, exist_ok=True)
    target = catalog.registry_root(man_cem["uid"])
    if target.exists() and any(target.iterdir()):
        raise PortableError("Folderul registrului destinație există și nu este gol; nu se suprascrie.", "target_exists")
    stage = base / (".import-" + uuid.uuid4().hex)
    created_org = created_cem = False
    steps: List[str] = []
    try:
        with zipfile.ZipFile(path) as z:
            members = [i for i in z.infolist() if not i.is_dir() and i.filename != MAN_ARC]
            for i, info in enumerate(members, 1):
                _p(progress, "extract", i, len(members))
                rel = _safe_name(info.filename)
                if rel == DB_ARC:
                    out = stage / DB_ARC
                elif rel.startswith(MEDIA_ARC):
                    out = stage / "media" / rel[len(MEDIA_ARC):]
                else:
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            man = json.loads(z.read(MAN_ARC).decode("utf-8"))
        steps.append("extras în staging")
        for e in man["files"]:                                           # reverificare după extragere
            rel = e["path"] if e["path"] == DB_ARC else "media/" + e["path"][len(MEDIA_ARC):]
            f = stage / rel
            if not f.is_file() or f.stat().st_size != e["size"] or _sha(f) != e["sha256"]:
                raise PortableError(f"Integritate după extragere: {e['path']}", "integrity")
        steps.append("integritate reverificată")
        if catalog.get_organization(man_org["uid"]) is None:
            catalog.create_organization(man_org.get("org_type") if man_org.get("org_type") in reg.ORG_TYPES else "altul", man_org["official_name"] or "Organizație importată", man_org.get("display_name", ""), uid=man_org["uid"])
            created_org = True
        catalog.create_cemetery(man_org["uid"], man_cem["name"] or "Cimitir importat", uid=man_cem["uid"])
        created_cem = True
        for d in (catalog.registry_root(man_cem["uid"]),):               # create_cemetery a creat folderul gol; îl înlocuim atomic cu cel verificat
            shutil.rmtree(d, ignore_errors=True)
        os.replace(stage, target)
        steps.append("registru mutat atomic")
        binding = catalog.binding_for(man_cem["uid"])
        binding.ensure_dirs()
        mp = man["media"].get("map")
        if mp:
            c = sqlite3.connect(binding.database)
            try:
                c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('map_path', ?)", (str(binding.media / mp["file"]),))
                c.commit()
            finally:
                c.close()
        reg.prepare_registry_db(binding)                                 # refuză o bază cu identitate străină
        snap = mf.local_snapshot(binding)
        counts = {e: len(r) for e, r in snap["entities"].items()}
        bad_media = [m["name"] for m in snap["media"] if m["status"] != "ok"]
        if counts != man["entities"]:
            raise PortableError(f"Numărul de înregistrări importate diferă de arhivă: {counts} != {man['entities']}", "count")
        if bs.LocalRegistry(binding).sync_state != "UNBOUND" or bs.LocalRegistry(binding).get("cloud_secret_ref"):
            raise PortableError("Registrul importat nu este în starea UNBOUND curată.", "state")
        steps.append("verificare finală (numere, media, identitate, UNBOUND)")
        report = {"import_id": uuid.uuid4().hex[:12], "when": time.strftime("%Y-%m-%dT%H:%M:%S"), "organization_uid": man_org["uid"], "cemetery_uid": man_cem["uid"],
                  "organization_created": created_org, "entities": counts, "media_files": len(snap["media"]), "media_problems": bad_media, "warnings": pv["warnings"],
                  "steps": steps, "sync_state": "UNBOUND",
                  "next_step": "Centru Cloud → Asociază acest calculator (Flux B): previzualizare, aprobare explicită, încărcare cu verificare și reluare. Nu se sincronizează automat."}
        (binding.logs).mkdir(parents=True, exist_ok=True)
        (binding.logs / f"import-{report['import_id']}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        return report
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)                         # rollback complet
        shutil.rmtree(target, ignore_errors=True)
        _rollback_catalog(catalog, man_org["uid"], man_cem["uid"], created_org, created_cem)
        raise


def _rollback_catalog(catalog: reg.RegistryCatalog, org_uid: str, cem_uid: str, org: bool, cem: bool) -> None:
    with catalog._conn() as c:
        if cem:
            c.execute("DELETE FROM cemeteries WHERE uid=?", (cem_uid,))
        if org:
            c.execute("DELETE FROM organizations WHERE uid=?", (org_uid,))
