"""
Launcher Cimitir — alege modul de pornire:

    💻  Local            → aplicația desktop + backend-ul local (backend/server.py) pe http://127.0.0.1:8000,
                           aceeași bază SQLite (CIMITIR_DATA_DIR). IMPLICIT; nu depinde de internet / Render / Supabase.
    🌐  Server local     → doar la cerere (opt-in, cu confirmare): portalul + administrarea web pe rețea (0.0.0.0),
                           adresă + cod QR; scanarea de pe telefon (web_server.py) rulează pe portul următor.

Sincronizarea cloud (HTTPS către Render) este un flux separat, în fundal (cloud_sync_auto.py).
Portul nu se schimbă în tăcere: dacă 8000 e ocupat de alt program, se afișează eroarea.

Portalul este același cod ca în start_local.py (un singur punct de pornire).
Executabilul Windows pornește portalul ca proces propriu: `Cimitir.exe --portal --host H --port P`.

Rulează:  python launcher.py
"""
import sys

if "--portal" in sys.argv:
    import start_local
    raise SystemExit(start_local.portal_cli(sys.argv[1:]))

import io
import os
import socket
import threading
import subprocess
import atexit

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QPixmap, QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QCheckBox, QMessageBox, QSpinBox,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import start_local as portal_boot  # noqa: E402
import cimitir_config  # noqa: E402
import cimitir_registry  # noqa: E402

DEFAULT_PORT = cimitir_config.LOCAL_PORT
LOCAL_HOST = cimitir_config.LOCAL_HOST
LAN_HOST = "0.0.0.0"
MOBILE_SCAN_PORT_OFFSET = 1


class _Prefs:
    """Preferințele launcherului stau în catalogul registrelor (nu într-un cimitir.db): launcherul nu deschide și nu creează
    nicio bază de cimitir înainte ca utilizatorul să aleagă registrul."""

    @staticmethod
    def get_setting(key):
        return cimitir_registry.RegistryCatalog(cimitir_config.data_dir()).get_state("launcher_" + key) or None

    @staticmethod
    def set_setting(key, value):
        cimitir_registry.RegistryCatalog(cimitir_config.data_dir()).set_state("launcher_" + key, str(value))


db = _Prefs


def get_lan_ip() -> str:
    """IP-ul din rețeaua locală (LAN)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def make_qr_pixmap(text: str, size: int = 220) -> QPixmap:
    try:
        import qrcode
        img = qrcode.make(text)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        pix = QPixmap()
        pix.loadFromData(buf.getvalue(), "PNG")
        return pix.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    except Exception:
        return QPixmap()


PAL = {
    "bg": "#0b1220", "panel": "#111a2b", "border": "#1e2b40",
    "text": "#e6edf6", "muted": "#8497b0", "primary": "#0ea5e9", "green": "#22c55e",
}


class LauncherWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cimitir — Pornire")
        self.setFixedSize(560, 420)
        self.setStyleSheet(f"background-color: {PAL['bg']}; color: {PAL['text']};")
        self._server_thread = None
        self._server = None
        self._portal_proc = None
        self._portal_message = ""
        self._portal_ok = False
        self._portal_status = None
        self._desktop_proc = None

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 28)
        root.setSpacing(18)

        title = QLabel("⚱  Cimitir")
        tf = QFont(); tf.setPointSize(22); tf.setBold(True); title.setFont(tf)
        title.setAlignment(Qt.AlignCenter)
        sub = QLabel("Cum vrei să pornești aplicația?")
        sub.setStyleSheet(f"color: {PAL['muted']};")
        sub.setAlignment(Qt.AlignCenter)
        root.addWidget(title); root.addWidget(sub)

        cards = QHBoxLayout(); cards.setSpacing(16)
        cards.addWidget(self._make_card(
            "💻", "Doar local", "Pe acest calculator", PAL["primary"], self.start_local))
        cards.addWidget(self._make_card(
            "🌐", "Acces telefon în rețea", "Opțional (LAN):\nPC-uri + telefoane", PAL["green"], self.start_server))
        root.addLayout(cards)

        port_row = QHBoxLayout()
        port_row.addStretch()
        port_row.addWidget(QLabel("Port server:"))
        self.port_spin = QSpinBox(); self.port_spin.setRange(1024, 65535); self.port_spin.setValue(DEFAULT_PORT)
        self.port_spin.setStyleSheet(f"background:{PAL['panel']};border:1px solid {PAL['border']};padding:4px;")
        port_row.addWidget(self.port_spin)
        port_row.addStretch()
        root.addLayout(port_row)

        self.remember = QCheckBox("Ține minte alegerea (pornește direct data viitoare)")
        self.remember.setStyleSheet(f"color:{PAL['muted']};")
        root.addWidget(self.remember, alignment=Qt.AlignCenter)

    def _make_card(self, icon, title, desc, accent, cb):
        card = QFrame()
        card.setCursor(Qt.PointingHandCursor)
        card.setStyleSheet(
            f"QFrame {{ background:{PAL['panel']}; border:1px solid {PAL['border']}; border-radius:14px; }}"
            f"QFrame:hover {{ border:2px solid {accent}; }}")
        lay = QVBoxLayout(card); lay.setContentsMargins(18, 22, 18, 22); lay.setSpacing(8)
        ic = QLabel(icon); icf = QFont(); icf.setPointSize(34); ic.setFont(icf); ic.setAlignment(Qt.AlignCenter)
        tl = QLabel(title); tf = QFont(); tf.setPointSize(14); tf.setBold(True); tl.setFont(tf); tl.setAlignment(Qt.AlignCenter)
        dl = QLabel(desc); dl.setStyleSheet(f"color:{PAL['muted']};"); dl.setAlignment(Qt.AlignCenter)
        btn = QPushButton("Pornește")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{ background:{accent}; color:#fff; border:none; border-radius:8px; padding:8px; font-weight:600; }}")
        btn.clicked.connect(cb)
        lay.addWidget(ic); lay.addWidget(tl); lay.addWidget(dl); lay.addStretch(); lay.addWidget(btn)
        card.mousePressEvent = lambda e: cb()
        return card

    # ------------------------------------------------------------------
    def _save_choice(self, mode):
        """Memorează alegerea; accesul din rețea (`lan_opt_in`) se memorează doar dacă a fost confirmat explicit."""
        if self.remember.isChecked():
            try:
                db.set_setting("startup_mode", mode)
                db.set_setting("startup_port", str(self.port_spin.value()))
                db.set_setting("lan_opt_in", "1" if mode == "server" else "0")
            except Exception:
                pass

    def _start_portal(self, host: str, port: int):
        """Pornește (sau reutilizează) backend-ul local; nu schimbă portul. La eșec desktopul pornește oricum."""
        status = portal_boot.ensure_portal_status(host, port)
        self._portal_status = status
        self._portal_proc, self._portal_message, self._portal_ok = status.proc, status.message, status.ok
        if status.proc is not None:
            atexit.register(portal_boot.stop_portal, status.proc)
        return status.ok

    def start_local(self):
        """Modul implicit: MAIN -> http://127.0.0.1:<port> -> SQLite + media locale. Fără internet, fără cloud."""
        self._save_choice("local")
        port = self.port_spin.value()
        if not self._start_portal(LOCAL_HOST, port):
            QMessageBox.warning(self, "Backend-ul local nu a putut porni",
                                f"{self._portal_message}\n\nAplicația desktop pornește oricum și lucrează pe SQLite local; "
                                f"doar portalul web local (http://{LOCAL_HOST}:{port}) nu este disponibil.")
        try:
            if getattr(sys, "frozen", False):
                # in .exe: pornim main() direct in acelasi proces
                self.close()
                import main as _main
                try:
                    _main.main()
                finally:
                    self.shutdown()
                return
            # in dev: pornim ca subprocess; launcherul rămâne în viață cât timp rulează desktopul (ca să nu oprească portalul)
            main_py = os.path.join(_HERE, "main.py")
            self._desktop_proc = subprocess.Popen([sys.executable, main_py], cwd=_HERE)
        except Exception as e:
            QMessageBox.warning(self, "Eroare", f"Nu am putut porni aplicația desktop:\n{e}")
            return
        self.close()

    def shutdown(self):
        """Așteaptă desktopul pornit de noi (dev), apoi oprește curat doar procesele pornite de launcher."""
        try:
            if self._desktop_proc is not None:
                self._desktop_proc.wait()
        except Exception:
            pass
        self._stop_lan_server()
        portal_boot.stop_portal(self._portal_proc)
        self._portal_proc = None

    def _confirm_lan(self) -> bool:
        warn = ""
        try:
            import cimitir_auth
            weak = cimitir_auth.default_password_accounts()
            if weak:
                warn = ("\n\n⚠ Aceste conturi au încă o parolă implicită: " + ", ".join(weak) +
                        ". Schimbă parolele (scripts/reset_admin_password.py) ÎNAINTE de a deschide accesul în rețea.")
        except Exception:
            pass
        answer = QMessageBox.question(
            self, "Acces telefon în rețeaua locală (opțional)",
            "Pornești portalul și administrarea web în REȚEA LOCALĂ (0.0.0.0)?\n\n"
            "Modul implicit și recomandat este «Doar local» (127.0.0.1:8000). În modul de rețea, orice dispozitiv "
            "din aceeași rețea poate ajunge la portal și la /admin (cu autentificare). Folosește-l doar în rețele de încredere. "
            "Nu face port forwarding și nu expune portul pe internet." + warn,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return answer == QMessageBox.Yes

    def start_server(self, confirmed: bool = False):
        if not confirmed and not self._confirm_lan():
            return
        self._save_choice("server")
        port = self.port_spin.value()
        if not self._start_portal(LAN_HOST, port):
            QMessageBox.critical(self, "Eroare server", f"Nu am putut porni portalul:\n{self._portal_message}")
            return
        scan_port = port + MOBILE_SCAN_PORT_OFFSET
        if not portal_boot.port_is_free(LAN_HOST, scan_port):
            QMessageBox.warning(self, "Scanare telefon indisponibilă",
                                f"Portul {scan_port} (scanare de pe telefon) este ocupat de alt program; nu pornesc pe alt port.")
            self._show_server_running(f"http://{get_lan_ip()}:{port}/admin/harta")
            return
        try:
            import web_server
            import uvicorn
            web_server.build_app()
            config = uvicorn.Config(web_server.app, host=LAN_HOST, port=scan_port, log_level="warning")
            self._server = uvicorn.Server(config)
            self._server_thread = threading.Thread(target=self._server.run, daemon=True)
            self._server_thread.start()
        except Exception as e:
            QMessageBox.warning(self, "Scanare telefon indisponibilă", f"Portalul rulează, dar scanarea de pe telefon nu a pornit:\n{e}")
        ip = get_lan_ip()
        url = f"http://{ip}:{port}/admin/harta"
        self._show_server_running(url)

    def _show_server_running(self, url):
        win = ServerWindow(url, self._stop_server)
        self._server_win = win
        win.show()
        self.hide()

    def _stop_lan_server(self):
        try:
            if self._server:
                self._server.should_exit = True
            if self._server_thread is not None and self._server_thread.is_alive():
                self._server_thread.join(timeout=5)
        except Exception:
            pass
        self._server = None
        self._server_thread = None

    def _stop_server(self):
        self._stop_lan_server()
        portal_boot.stop_portal(self._portal_proc)
        self._portal_proc = None
        self.close()


class ServerWindow(QWidget):
    def __init__(self, url, on_stop):
        super().__init__()
        self.setWindowTitle("Cimitir — Server local pornit")
        self.setFixedSize(420, 560)
        self.setStyleSheet(f"background-color:{PAL['bg']}; color:{PAL['text']};")
        lay = QVBoxLayout(self); lay.setContentsMargins(28, 24, 28, 24); lay.setSpacing(14)

        status = QLabel("✅  Server pornit")
        sf = QFont(); sf.setPointSize(16); sf.setBold(True); status.setFont(sf)
        status.setStyleSheet(f"color:{PAL['green']};"); status.setAlignment(Qt.AlignCenter)
        lay.addWidget(status)

        hint = QLabel("Scanează codul QR cu telefonul\nsau deschide adresa în browser:")
        hint.setStyleSheet(f"color:{PAL['muted']};"); hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(hint)

        qr = QLabel(); qr.setAlignment(Qt.AlignCenter)
        pix = make_qr_pixmap(url, 220)
        if not pix.isNull():
            qr.setPixmap(pix)
        else:
            qr.setText("(instalează pachetul 'qrcode' pentru QR)")
            qr.setStyleSheet(f"color:{PAL['muted']};")
        lay.addWidget(qr)

        url_lbl = QLabel(url)
        uf = QFont(); uf.setPointSize(14); uf.setBold(True); url_lbl.setFont(uf)
        url_lbl.setAlignment(Qt.AlignCenter); url_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        url_lbl.setStyleSheet(f"color:{PAL['primary']};")
        lay.addWidget(url_lbl)

        note = QLabel("Dispozitivele trebuie să fie în aceeași rețea (WiFi/LAN).\n"
                      "Pe telefon: autentifică-te cu un cont al personalului → Hartă → locul → Fă poză. Ce adaugi apare imediat în MAIN (aceeași bază și aceleași fișiere). "
                      "Cetățeni: /portal.")
        note.setStyleSheet(f"color:{PAL['muted']}; font-size:11px;"); note.setAlignment(Qt.AlignCenter)
        note.setWordWrap(True)
        lay.addWidget(note)

        lay.addStretch()
        btn_desktop = QPushButton("Deschide și aplicația desktop pe acest PC")
        btn_desktop.setStyleSheet(
            f"QPushButton {{ background:{PAL['primary']}; color:#fff; border:none; border-radius:8px; padding:10px; font-weight:600; }}")
        btn_desktop.clicked.connect(self._open_desktop)
        lay.addWidget(btn_desktop)

        btn_stop = QPushButton("Oprește serverul")
        btn_stop.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{PAL['muted']}; border:1px solid {PAL['border']}; border-radius:8px; padding:10px; }}"
            f"QPushButton:hover {{ background:#ef4444; color:#fff; border-color:#ef4444; }}")
        btn_stop.clicked.connect(on_stop)
        lay.addWidget(btn_stop)

    def _open_desktop(self):
        try:
            if getattr(sys, "frozen", False):
                # in .exe rulam main() pe un thread separat pentru a nu bloca serverul
                import threading as _th
                def _run():
                    try:
                        import main as _main
                        _main.main()
                    except Exception:
                        pass
                _th.Thread(target=_run, daemon=True).start()
                return
            subprocess.Popen([sys.executable, os.path.join(_HERE, "main.py")], cwd=_HERE)
        except Exception as e:
            QMessageBox.warning(self, "Eroare", str(e))


def main():
    app = QApplication(sys.argv)
    try:
        from theme import app_icon
        icon = app_icon()
        if not icon.isNull():
            app.setWindowIcon(icon)
    except Exception:
        pass

    # pornire directă dacă e memorată alegerea
    remembered = None
    try:
        remembered = db.get_setting("startup_mode")
    except Exception:
        remembered = None

    win = LauncherWindow()
    try:
        win.port_spin.setValue(int(db.get_setting("startup_port") or DEFAULT_PORT))
    except Exception:
        pass
    if remembered == "local":
        win.start_local()
        if not win.isVisible():
            win.shutdown()
            return
    elif remembered == "server" and (db.get_setting("lan_opt_in") or "") == "1":
        try:
            port = int(db.get_setting("startup_port") or DEFAULT_PORT)
            win.port_spin.setValue(port)
        except Exception:
            pass
        win.start_server(confirmed=True)
    else:
        win.show()

    code = app.exec()
    win.shutdown()
    sys.exit(code)


if __name__ == "__main__":
    main()
