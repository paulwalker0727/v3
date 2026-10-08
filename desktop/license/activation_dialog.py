"""
Ecran de activare — afișat când licența lipsește, e invalidă sau expirată.

Afișează HWID-ul cu buton "Copiază", permite încărcarea unui fișier
licenta.lic (emis offline de emitent).

Salvează licența la:
  Windows: %APPDATA%\\CimitirDesktop\\license.key
"""
import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QMessageBox, QFrame, QApplication,
)

from .validator import (
    get_hwid,
    verifica_fisier,
    LICENSE_FILE,
    LICENSE_DIR,
)


DIALOG_STYLE = """
QDialog { background: #0f1620; color: #e6ecf3; }
QLabel { color: #e6ecf3; }
QLabel#title { font-size: 20px; font-weight: 800; color: #ffffff; }
QLabel#hwid { font-family: 'Consolas','DejaVu Sans Mono',monospace;
              background:#16212e; border:1px solid #2a3a4c; border-radius:6px;
              padding:10px; color:#d4a24e; font-size:12px; }
QPushButton { background: #1f2d3d; border: 1px solid #2a3a4c; border-radius: 6px;
              padding: 8px 14px; font-weight: 600; color: #e6ecf3; }
QPushButton:hover { border-color: #d4a24e; }
QPushButton#primary { background: #d4a24e; color: #10161f; border: none; }
QPushButton#primary:hover { background: #e0b45f; }
"""


class ActivationDialog(QDialog):
    def __init__(self, motiv: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Activare licență — Cimitir")
        self.setMinimumWidth(600)
        self.setStyleSheet(DIALOG_STYLE)
        self.hwid = get_hwid()
        self._activat = False
        self._build(motiv)

    def _build(self, motiv: str):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(14)

        titlu = QLabel("Activare licență")
        titlu.setObjectName("title")
        root.addWidget(titlu)

        if motiv:
            w = QLabel(f"⚠  {motiv}")
            w.setStyleSheet("color:#e79a9a; font-weight:600;")
            w.setWordWrap(True)
            root.addWidget(w)

        root.addWidget(QLabel(
            "ID-ul acestui calculator (HWID) — trimiteți-l pentru emiterea licenței:"
        ))
        self.hwid_lbl = QLabel(self.hwid)
        self.hwid_lbl.setObjectName("hwid")
        self.hwid_lbl.setWordWrap(True)
        self.hwid_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.hwid_lbl)

        copy_btn = QPushButton("📋  Copiază HWID")
        copy_btn.setObjectName("primary")
        copy_btn.clicked.connect(self._copy_hwid)
        root.addWidget(copy_btn, alignment=Qt.AlignLeft)

        root.addWidget(self._sep())

        root.addWidget(QLabel(
            "Încărcați fișierul de licență primit (licenta.lic sau license.key):"
        ))
        f = QHBoxLayout()
        load_btn = QPushButton("Alege fișier licență…")
        load_btn.clicked.connect(self._load_file)
        f.addWidget(load_btn)
        f.addStretch()
        root.addLayout(f)

        info = QLabel(f"Se va salva local în:\n{LICENSE_FILE}")
        info.setStyleSheet("color:#8497b0; font-size: 11px;")
        info.setWordWrap(True)
        root.addWidget(info)

        root.addWidget(self._sep())
        close_btn = QPushButton("Închide")
        close_btn.clicked.connect(self.reject)
        root.addWidget(close_btn, alignment=Qt.AlignRight)

    def _sep(self):
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#22303f;")
        return line

    def _copy_hwid(self):
        QApplication.clipboard().setText(self.hwid)
        QMessageBox.information(self, "Copiat", "HWID-ul a fost copiat în clipboard.")

    def _finalizeaza(self, rezultat):
        if rezultat.ok:
            self._activat = True
            QMessageBox.information(
                self, "Succes",
                f"Licență validă pentru: {rezultat.client}\n"
                f"Expiră: {rezultat.expira or 'permanent'}"
            )
            self.accept()
        else:
            QMessageBox.critical(self, "Licență invalidă", rezultat.motiv)

    def _load_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Alege licență",
            "", "Licență (*.lic *.key *.json);;Toate (*)",
        )
        if not path:
            return
        src = Path(path)
        rezultat = verifica_fisier(src, self.hwid)
        if rezultat.ok:
            try:
                if src.resolve() != LICENSE_FILE.resolve():
                    LICENSE_DIR.mkdir(parents=True, exist_ok=True)
                    shutil.copy(src, LICENSE_FILE)
            except Exception as e:
                QMessageBox.warning(
                    self, "Atenție",
                    f"Nu am putut copia licența la locația implicită:\n{e}"
                )
        self._finalizeaza(rezultat)

    @property
    def activat(self) -> bool:
        return self._activat
