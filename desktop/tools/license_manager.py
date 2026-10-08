"""
CIMITIR — Generator de Licențe (UNEALTA EMITENTULUI).

⚠️  ATENȚIE: acest program folosește CHEIA PRIVATĂ Ed25519.
    NU-l distribui clienților — el poate emite licențe valide!
    Rămâne DOAR la emitent (dezvoltator / administrator).

Rulare:
  python tools/license_manager.py           # GUI de emitere licențe
  python tools/license_manager.py --keygen  # generează perechea de chei (o singură dată)

Cheia privată e căutată (în această ordine):
  1. Variabila de mediu CIMITIR_PRIVATE_KEY (calea completă)
  2. tools/keys/private_key.pem
  3. keys/private_key.pem (rădăcina proiectului)

Flux:
  1. Rulezi `python tools/license_manager.py --keygen` O SINGURĂ DATĂ
     → generează tools/keys/private_key.pem + public_key.pem
     → copiezi conținutul public_key.pem în license/validator.py (câmpul PUBLIC_KEY_PEM)
  2. Pentru fiecare client:
     a. Clientul îți trimite HWID-ul (din dialogul de activare)
     b. Rulezi `python tools/license_manager.py`
     c. Lipești HWID-ul, scrii numele clientului, alegi durata
     d. „Generează licenta.lic" → trimiți fișierul clientului
"""
import os
import sys
import json
import base64
from pathlib import Path
from datetime import datetime, timezone, date, timedelta

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _find_private_key() -> Path:
    env = os.environ.get("CIMITIR_PRIVATE_KEY")
    if env and Path(env).exists():
        return Path(env)
    for cand in (
        HERE / "keys" / "private_key.pem",
        ROOT / "keys" / "private_key.pem",
        HERE / "private_key.pem",
    ):
        if cand.exists():
            return cand
    return HERE / "keys" / "private_key.pem"


def _canonical(payload: dict) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _semneaza(payload: dict, priv: Ed25519PrivateKey) -> dict:
    sig = priv.sign(_canonical(payload))
    return {"payload": payload, "semnatura": base64.b64encode(sig).decode("ascii")}


def generate_ed25519_keypair(out_dir: Path):
    """Generează pereche de chei Ed25519. Returnează (priv_path, pub_path, pub_pem_str)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()

    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    priv_path = out_dir / "private_key.pem"
    pub_path = out_dir / "public_key.pem"
    priv_path.write_bytes(priv_pem)
    pub_path.write_bytes(pub_pem)
    return priv_path, pub_path, pub_pem.decode()


# ============================================================================
# GUI
# ============================================================================
STYLE = """
QMainWindow, QWidget { background: #0f1620; color: #e6ecf3; font-family: 'Segoe UI','DejaVu Sans',sans-serif; }
QLabel#title { font-size: 22px; font-weight: 800; color: #ffffff; }
QLabel#sub { color: #9fb0c3; }
QLineEdit, QComboBox, QSpinBox, QDateEdit {
    background: #16212e; border: 1px solid #2a3a4c; border-radius: 6px; padding: 8px 10px; color: #e6ecf3;
}
QLineEdit:focus, QComboBox:focus { border: 1px solid #d4a24e; }
QPushButton { background: #1f2d3d; border: 1px solid #2a3a4c; border-radius: 6px; padding: 9px 16px; font-weight: 600; color:#e6ecf3; }
QPushButton:hover { border-color: #d4a24e; }
QPushButton#primary { background: #d4a24e; color: #10161f; border: none; }
QPushButton#primary:hover { background: #e0b45f; }
QLabel#key { font-family: 'Consolas','DejaVu Sans Mono',monospace; color:#7f8ea3; font-size: 11px; }
"""


def _run_gui():
    from PySide6.QtCore import Qt, QDate
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
        QLabel, QLineEdit, QComboBox, QSpinBox, QDateEdit, QPushButton, QFileDialog,
        QMessageBox, QFrame,
    )

    class LicenseManager(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("CIMITIR — Generator de Licențe (EMITENT)")
            self.setMinimumWidth(600)
            self.priv_path = _find_private_key()
            self._build()

        def _build(self):
            central = QWidget()
            self.setCentralWidget(central)
            root = QVBoxLayout(central)
            root.setContentsMargins(28, 26, 28, 26)
            root.setSpacing(14)

            title = QLabel("Generator de Licențe")
            title.setObjectName("title")
            root.addWidget(title)
            sub = QLabel("Emite licenta.lic pentru un client, pe baza HWID-ului primit.")
            sub.setObjectName("sub")
            root.addWidget(sub)

            form = QFormLayout()
            form.setSpacing(12)

            self.hwid = QLineEdit()
            self.hwid.setPlaceholderText("Lipiți aici HWID-ul primit de la client")
            hbox = QHBoxLayout()
            hbox.addWidget(self.hwid)
            paste = QPushButton("Lipește")
            paste.clicked.connect(self._paste_hwid)
            hbox.addWidget(paste)
            hw_wrap = QWidget(); hw_wrap.setLayout(hbox)
            form.addRow("HWID client", hw_wrap)

            self.client = QLineEdit()
            self.client.setPlaceholderText("Ex: Primăria Comunei Florești")
            form.addRow("Nume client", self.client)

            self.mod = QComboBox()
            self.mod.addItems(["Zile de la azi", "Dată expirare", "Permanent"])
            self.mod.currentIndexChanged.connect(self._mod_changed)
            form.addRow("Durată", self.mod)

            self.zile = QSpinBox(); self.zile.setRange(1, 36500); self.zile.setValue(365)
            self.zile_row_label = QLabel("Număr zile")
            form.addRow(self.zile_row_label, self.zile)

            self.data = QDateEdit()
            self.data.setCalendarPopup(True)
            self.data.setDate(QDate.currentDate().addYears(1))
            self.data_row_label = QLabel("Data expirării")
            form.addRow(self.data_row_label, self.data)

            root.addLayout(form)

            line = QFrame(); line.setFrameShape(QFrame.HLine); line.setStyleSheet("color:#22303f;")
            root.addWidget(line)

            gen = QPushButton("Generează licenta.lic")
            gen.setObjectName("primary")
            gen.clicked.connect(self._genereaza)
            root.addWidget(gen)

            key_lbl = QLabel(f"Cheie privată: {self.priv_path}")
            key_lbl.setObjectName("key"); key_lbl.setWordWrap(True)
            root.addWidget(key_lbl)
            if not self.priv_path.exists():
                warn = QLabel(
                    "⚠ Cheia privată nu a fost găsită!\n"
                    "Rulează întâi: python tools/license_manager.py --keygen"
                )
                warn.setStyleSheet("color:#e79a9a; font-weight:600;")
                warn.setWordWrap(True)
                root.addWidget(warn)

            self._mod_changed(0)

        def _mod_changed(self, idx):
            self.zile.setVisible(idx == 0); self.zile_row_label.setVisible(idx == 0)
            self.data.setVisible(idx == 1); self.data_row_label.setVisible(idx == 1)

        def _paste_hwid(self):
            self.hwid.setText(QApplication.clipboard().text().strip())

        def _expira(self):
            idx = self.mod.currentIndex()
            if idx == 2:
                return None
            if idx == 0:
                return (date.today() + timedelta(days=self.zile.value())).isoformat()
            qd = self.data.date()
            return date(qd.year(), qd.month(), qd.day()).isoformat()

        def _genereaza(self):
            hwid = self.hwid.text().strip()
            client = self.client.text().strip()
            if not hwid:
                QMessageBox.warning(self, "Atenție", "Introduceți HWID-ul clientului.")
                return
            if not client:
                QMessageBox.warning(self, "Atenție", "Introduceți numele clientului.")
                return
            if not self.priv_path.exists():
                QMessageBox.critical(self, "Eroare", f"Cheia privată lipsește:\n{self.priv_path}")
                return
            try:
                priv = serialization.load_pem_private_key(
                    self.priv_path.read_bytes(), password=None
                )
            except Exception as e:
                QMessageBox.critical(self, "Eroare cheie", f"Nu pot citi cheia privată:\n{e}")
                return

            expira = self._expira()
            payload = {
                "hwid": hwid,
                "client": client,
                "expira": expira,
                "emis_la": datetime.now(timezone.utc).isoformat(),
            }
            licenta = _semneaza(payload, priv)
            path, _ = QFileDialog.getSaveFileName(
                self, "Salvează licența", "licenta.lic",
                "Licență (*.lic);;JSON (*.json)",
            )
            if not path:
                return
            try:
                Path(path).write_text(
                    json.dumps(licenta, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception as e:
                QMessageBox.critical(self, "Eroare salvare", str(e))
                return
            QMessageBox.information(
                self, "Licență generată",
                f"Fișier salvat:\n{path}\n\nClient: {client}\nExpiră: {expira or 'permanent'}\n\n"
                f"Trimiteți fișierul clientului — îl încarcă din dialogul de activare."
            )

    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    win = LicenseManager()
    win.show()
    sys.exit(app.exec())


def _cli_keygen():
    out = HERE / "keys"
    priv, pub, pub_pem = generate_ed25519_keypair(out)
    print(f"Chei generate în {out}")
    print(f"  privat : {priv}   ⚠️  PĂSTREAZĂ SECRET, nu-l pune în git!")
    print(f"  public : {pub}\n")
    print("--- Cheia PUBLICĂ (copiază conținutul în license/validator.py -> PUBLIC_KEY_PEM) ---\n")
    print(pub_pem)
    print("\nGata! Poți emite acum licențe cu:  python tools/license_manager.py")


def main():
    if "--keygen" in sys.argv:
        _cli_keygen()
        return
    _run_gui()


if __name__ == "__main__":
    main()
