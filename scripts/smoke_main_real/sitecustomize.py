"""Operator de test pentru MAIN REAL (Etapa 3.5). Se încarcă DOAR cu CIMITIR_SMOKE=1 și PYTHONPATH spre acest folder.

Rulează în procesul real `desktop/main.py` (pornit de launcher) și acționează ferestrele reale (selector, login, fereastra
MAIN) printr-un QTimer. Nu modifică codul aplicației; licența se simulează valid (doar în acest proces de test).
Scrie rezultatele în fișierul CIMITIR_SMOKE_REPORT (JSON).
"""
import os

if os.environ.get("CIMITIR_SMOKE") == "1":
    import json
    import socket
    import sys
    import threading
    import time
    import urllib.request

    SCENARIO = os.environ.get("CIMITIR_SMOKE_SCENARIO", "full")  # full | quick | auto
    OFFLINE = os.environ.get("CIMITIR_SMOKE_OFFLINE") == "1"
    REPORT = os.environ["CIMITIR_SMOKE_REPORT"]
    PORT = int(os.environ.get("CIMITIR_SMOKE_PORT", "8000"))
    OPERATOR = ("operator_smoke", "Smoke-Test-Parola-2026!")
    RES = {"scenario": SCENARIO, "offline": OFFLINE, "checks": {}, "log": [], "done": False}
    _lock = threading.Lock()

    def flush():
        with _lock, open(REPORT, "w", encoding="utf-8") as f:
            json.dump(RES, f, ensure_ascii=False, indent=1)

    def rec(name, ok, detail=""):
        RES["checks"][name] = {"ok": bool(ok), "detail": str(detail)[:300]}
        flush()

    def log(msg):
        RES["log"].append(f"{time.strftime('%H:%M:%S')} {msg}")
        flush()

    def http(path, timeout=5):
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")

    def portal_codes(q):
        status, body = http(f"/api/public/search?q={q}")
        return [x["cod_loc"] for x in json.loads(body)["results"]] if status == 200 else None

    if OFFLINE:  # procesul MAIN nu poate ieși pe internet; loopback rămâne permis
        _real_connect, _real_gai = socket.socket.connect, socket.getaddrinfo

        def _is_local(host):
            return str(host) in ("127.0.0.1", "localhost", "::1", "0.0.0.0")

        def _connect(self, addr):
            if isinstance(addr, tuple) and not _is_local(addr[0]):
                time.sleep(2)
                raise OSError("OFFLINE (smoke): fără internet")
            return _real_connect(self, addr)

        def _gai(host, *a, **k):
            if host is not None and not _is_local(host):
                time.sleep(3)  # ca un DNS real fără rețea: eșecul vine după câteva secunde
                raise socket.gaierror("OFFLINE (smoke): fără internet")
            return _real_gai(host, *a, **k)

        socket.socket.connect, socket.getaddrinfo = _connect, _gai

    import PySide6.QtWidgets as W
    from PySide6.QtCore import QTimer, QEventLoop
    from PySide6.QtGui import QAction

    S = {"i": 0, "ticks": 0, "uid": {}, "t0": time.time()}

    def cls(w):
        return type(w).__name__

    def fill_org(w, kind, official, display, county):
        w.type.setCurrentIndex(w.type.findData(kind))
        w.fields["official_name"].setText(official)
        w.fields["display_name"].setText(display)
        w.fields["county"].setText(county)
        w.fields["locality"].setText(county)
        w.accept()

    def pick(lst, text):
        for i in range(lst.count()):
            if text in lst.item(i).text():
                lst.setCurrentRow(i)
                return True
        return False

    def btn(w, name):
        return w.findChild(W.QPushButton, name)

    def ensure_operator():
        import cimitir_auth as auth
        import database as db
        auth.init_auth()
        auth.create_user(OPERATOR[0], OPERATOR[1], auth.ROLE_ADMIN)
        return db

    def do_login(w):
        ensure_operator()
        w.input_username.setText(OPERATOR[0])
        w.input_password.setText(OPERATOR[1])
        w.btn_login.click()

    def open_registry(w, org_text, cem_text):
        ok = pick(w.orgs, org_text) and pick(w.cems, cem_text)
        rec(f"selector_pick_{cem_text}", ok)
        w.auto.setChecked(True)
        btn(w, "btn-open").click()

    # ---- fotografii
    def make_jpg(path, color):
        from PIL import Image
        Image.new("RGB", (32, 32), color).save(path, "JPEG")
        return path

    def main_window_ops(win, which):
        import database as db
        import cimitir_registry as reg
        b = reg.require_binding()
        tag = which
        rec(f"{tag}_binding", db.get_setting("cemetery_uid") == b.cemetery_uid == S["uid"].get(which[0], b.cemetery_uid),
            f"{db.current_db_path()}")
        codes = [r["cod_loc"] for r in db.search_locuri({})]
        docs = db.list_documents("loc", "A-01-01")
        if which == "A":
            rec("A_empty_start", codes == [] and docs == [])
            db.save_plot_to_db("A-01-01", [(0, 0), (40, 0), (40, 30), (0, 30)], "#22c55e")
            db.save_loc_metadata("A-01-01", {"sector": "A", "parcela": "1", "rand": "1", "numar": "1", "disponibilitate": "Liber"})
            src_dir = os.path.join(os.environ["CIMITIR_DATA_DIR"], "_smoke_src_A")
            os.makedirs(src_dir, exist_ok=True)
            photo = make_jpg(os.path.join(src_dir, "foto.jpg"), (200, 30, 30))
            S["photo_A_sha"] = _sha(photo)
            did = db.add_document("loc", "A-01-01", "poza_loc", photo, caption="foto A")
            rec("A_loc_and_photo_created", bool(did) and db.search_locuri({})[0]["cod_loc"] == "A-01-01")
            try:
                rec("portal_shows_A", "A-01-01" in (portal_codes("A-01-01") or []), portal_codes("A-01-01"))
                st, body = http("/portal")
                rec("portal_page_200", st == 200 and len(body) > 500, st)
            except Exception as exc:
                rec("portal_shows_A", False, exc)
            if OFFLINE:
                offline_sync_check()
        elif which == "B":
            rec("B_no_A_leak_db", codes == [] and docs == [], f"{codes} {len(docs)}")
            try:
                rec("B_no_A_leak_portal", "A-01-01" not in (portal_codes("A-01-01") or []), portal_codes("A-01-01"))
            except Exception as exc:
                rec("B_no_A_leak_portal", False, exc)
            db.save_plot_to_db("A-01-01", [(0, 0), (50, 0), (50, 40), (0, 40)], "#2255cc")
            db.save_loc_metadata("A-01-01", {"sector": "B", "parcela": "9", "rand": "9", "numar": "9", "disponibilitate": "Ocupat"})
            src_dir = os.path.join(os.environ["CIMITIR_DATA_DIR"], "_smoke_src_B")
            os.makedirs(src_dir, exist_ok=True)
            photo = make_jpg(os.path.join(src_dir, "foto.jpg"), (30, 30, 200))
            S["photo_B_sha"] = _sha(photo)
            rec("B_same_cod_loc_same_photo_name", bool(db.add_document("loc", "A-01-01", "poza_loc", photo, caption="foto B")))
            try:
                rec("portal_shows_B", "A-01-01" in (portal_codes("A-01-01") or []), portal_codes("A-01-01"))
            except Exception as exc:
                rec("portal_shows_B", False, exc)
        elif which == "A2":
            row = db.search_locuri({})
            plot = db.search_locuri({"cod_loc": "A-01-01"}) if row else []
            docs = db.list_documents("loc", "A-01-01")
            ok_doc = len(docs) == 1 and docs[0]["caption"] == "foto A" and _sha(docs[0]["file_path"]) == S["photo_A_sha"] \
                and reg.require_binding().contains(docs[0]["file_path"])
            rec("A_data_intact_after_roundtrip", len(row) == 1 and ok_doc and row[0].get("sector") == "A",
                f"{len(row)} {len(docs)}")
            rec("A_no_B_leak", not any(d.get("caption") == "foto B" for d in docs))
        S["leak"] = S.get("leak", 0) + (1 if (which == "B" and (codes or docs)) else 0)

    def _sha(p):
        import hashlib
        return hashlib.sha256(open(p, "rb").read()).hexdigest()

    def offline_sync_check():
        import cloud_sync
        from cloud_sync_auto import AutoSync
        cloud_sync.save_config("https://cimitir-offline.invalid", "cimitir.offline-key-" + "x" * 24, True)
        auto = AutoSync()
        got = {}
        loop = QEventLoop()
        auto.finished.connect(lambda rep: (got.update(rep), loop.quit()))
        t_before = S["ticks"]
        QTimer.singleShot(25000, loop.quit)
        started = time.time()
        auto.run_once(force=True)
        loop.exec()
        elapsed = time.time() - started
        ui_alive = S["ticks"] - t_before >= max(2, int(elapsed * 2))  # tick la 250 ms: UI-ul a rămas responsiv
        rec("offline_sync_fails_controlled", bool(got) and got.get("ok") is False and elapsed < 25, f"ok={got.get('ok')} {elapsed:.1f}s {got.get('lines')}")
        rec("offline_ui_not_blocked", ui_alive, f"ticks={S['ticks'] - t_before} in {elapsed:.1f}s")
        cloud_sync.save_config("", "", False)

    def switch_registry(win):
        for a in win.findChildren(QAction):
            if "Schimbă registrul" in a.text():
                a.trigger()
                return True
        b = win.findChild(W.QPushButton, "user-switch-registry-btn")
        if b:
            b.click()
            return True
        return False

    # ---- scenariul
    def full_steps():
        def sel1(w):
            rec("selector_shown_first_run", True)
            rec("no_spurious_legacy_banner", not w.legacy_box.isVisible(), w.legacy_box.text())
            btn(w, "btn-add-org").click()

        def org1(w):
            fill_org(w, "parohie", "Parohia Sf. Gheorghe Banovița", "Parohia Banovița", "Suceava")

        def sel2(w):
            btn(w, "btn-add-org").click()

        def org2(w):
            fill_org(w, "primarie", "Primăria Exemplu", "Primăria Exemplu", "Cluj")

        def sel3(w):
            rec("two_orgs", w.orgs.count() == 2, w.orgs.count())
            pick(w.orgs, "Parohia")
            btn(w, "btn-add-cem").click()

        def cemA(w):
            w.fields["name"].setText("Cimitir A")
            w.accept()

        def sel4(w):
            pick(w.orgs, "Primăria")
            btn(w, "btn-add-cem").click()

        def cemB(w):
            w.fields["name"].setText("Cimitir B")
            w.accept()

        def sel5(w):
            cats = w.catalog
            S["uid"]["A"] = next(c["uid"] for c in cats.list_cemeteries() if c["name"] == "Cimitir A")
            S["uid"]["B"] = next(c["uid"] for c in cats.list_cemeteries() if c["name"] == "Cimitir B")
            rec("two_cemeteries", len(cats.list_cemeteries()) == 2)
            open_registry(w, "Parohia", "Cimitir A")

        def login(w):
            do_login(w)

        def mainA(win):
            main_window_ops(win, "A")
            rec("switch_action_found", switch_registry(win))

        def confirm(w):
            rec("switch_confirm_dialog", "registrul" in w.windowTitle().lower() or "registr" in w.text().lower(), w.windowTitle())
            w.button(W.QMessageBox.Yes).click()

        def selB(w):
            rec("selector_after_switch", True)
            open_registry(w, "Primăria", "Cimitir B")

        def mainB(win):
            main_window_ops(win, "B")
            switch_registry(win)

        def selA(w):
            open_registry(w, "Parohia", "Cimitir A")

        def mainA2(win):
            main_window_ops(win, "A2")
            RES["done"] = True
            RES["leakage"] = S.get("leak", 0)
            flush()
            win.close()

        return [("RegistrySelector", sel1), ("OrganizationDialog", org1), ("RegistrySelector", sel2), ("OrganizationDialog", org2),
                ("RegistrySelector", sel3), ("CemeteryDialog", cemA), ("RegistrySelector", sel4), ("CemeteryDialog", cemB),
                ("RegistrySelector", sel5), ("LoginWindow", login), ("MAIN", mainA), ("QMessageBox", confirm),
                ("RegistrySelector", selB), ("LoginWindow", login), ("MAIN", mainB), ("QMessageBox", confirm),
                ("RegistrySelector", selA), ("LoginWindow", login), ("MAIN", mainA2)]

    def quick_steps():
        def sel(w):
            rec("selector_shown", True, f"orgs={w.orgs.count()}")
            import cimitir_registry as reg
            cat = reg.RegistryCatalog(os.environ["CIMITIR_DATA_DIR"])
            if w.orgs.count():
                rec("catalog_active_is_A", cat.get_state("active_cemetery_uid") != "", cat.get_state("active_cemetery_uid"))
            try:
                st, body = http("/api/public/health")
                rec("portal_up_in_quick", st == 200, body[:80])
            except Exception as exc:
                rec("portal_up_in_quick", False, f"portal indisponibil: {exc}")
            RES["done"] = True
            flush()
            w.reject()
        return [("RegistrySelector", sel)]

    def auto_steps():
        def login(w):
            import cimitir_registry as reg
            b = reg.require_binding()
            rec("autostart_no_selector", not any(cls(x) == "RegistrySelector" for x in W.QApplication.topLevelWidgets()
                                                if x.isVisible()), "")
            rec("autostart_registry_is_A", b.cemetery_uid == os.environ["CIMITIR_SMOKE_EXPECT_UID"], b.cemetery_uid)
            import database as db
            rec("autostart_data_present", [r["cod_loc"] for r in db.search_locuri({})] == ["A-01-01"])
            try:
                rec("autostart_portal_reflects_A", "A-01-01" in (portal_codes("A-01-01") or []))
            except Exception as exc:
                rec("autostart_portal_reflects_A", False, exc)
            RES["done"] = True
            flush()
            w.reject()
        return [("LoginWindow", login)]

    STEPS = {"full": full_steps, "quick": quick_steps, "auto": auto_steps}[SCENARIO]()

    def active_widget():
        w = W.QApplication.activeModalWidget()
        if w is not None:
            return w
        for x in W.QApplication.topLevelWidgets():
            if x.isVisible() and cls(x) in ("CemeteryUI", "UserModeWindow"):
                return x
        return None

    def tick():
        S["ticks"] += 1
        if time.time() - S["t0"] > 240:
            rec("watchdog", False, "timeout 240s")
            os._exit(3)
        w = active_widget()
        if w is None:
            return
        name = "MAIN" if cls(w) in ("CemeteryUI", "UserModeWindow") else cls(w)
        if S["i"] < len(STEPS) and STEPS[S["i"]][0] == name:
            fn = STEPS[S["i"]][1]
            S["i"] += 1
            log(f"step {S['i']}/{len(STEPS)} {name}")
            def run(fn=fn, w=w, n=S["i"], name=name):
                try:
                    fn(w)
                except Exception:
                    import traceback
                    rec(f"step_{n}_{name}_error", False, traceback.format_exc()[-300:])
                    os._exit(4)
            QTimer.singleShot(0, run)  # fără reintrare în timer: acțiunile pot deschide dialoguri modale
        elif isinstance(w, W.QMessageBox):
            log(f"mesaj neașteptat: {w.windowTitle()} | {w.text()[:120]}")
            RES.setdefault("unexpected_messages", []).append(w.text()[:200])
            w.button(W.QMessageBox.Ok).click() if w.button(W.QMessageBox.Ok) else w.reject()

    def _install(app):
        try:
            import license.validator as v
            v.verifica_licenta_curenta = lambda: v.RezultatVerificare(True, "", "SMOKE", None, "smoke")
        except Exception as exc:
            log(f"licență: {exc}")
        t = QTimer(app)
        t.setInterval(250)
        t.timeout.connect(tick)
        t.start()
        app._smoke_timer = t
        log(f"operator instalat scenariu={SCENARIO} offline={OFFLINE}")

    _orig_init = W.QApplication.__init__

    def _init(self, *a, **k):
        _orig_init(self, *a, **k)
        _install(self)

    W.QApplication.__init__ = _init
