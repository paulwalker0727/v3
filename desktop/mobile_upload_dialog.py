"""Dialog MAIN: cod QR pentru telefon (flux principal prin internet; URL-ul vine de la server din CIMITIR_PUBLIC_URL)."""
from __future__ import annotations

import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QVBoxLayout

import cloud_sync
import mobile_upload

POLL_MS = 3000


class PhoneUploadDialog(QDialog):
    """Afișează QR-ul, așteaptă încărcarea, apoi sincronizează media. `on_done()` reînprospătează lista de documente."""

    def __init__(self, parent, entity_type: str, entity_id, label: str = "", on_done=None, issued_by: str = "main"):
        super().__init__(parent)
        self.setWindowTitle("Continuă pe telefon")
        self.setMinimumWidth(380)
        self._on_done, self._tid, self._expires, self._busy = on_done, "", 0, False
        lay = QVBoxLayout(self)
        self.title = QLabel(f"<b>{label or entity_type}</b><br>Scanează codul cu telefonul (merge și pe 4G/5G).")
        self.title.setWordWrap(True)
        self.qr = QLabel(alignment=Qt.AlignCenter)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.copy_btn = QPushButton("Copiază linkul")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(self._url))
        close = QPushButton("Închide")
        close.clicked.connect(self.reject)
        for w in (self.title, self.qr, self.info, self.copy_btn, close):
            lay.addWidget(w)
        self._url = ""
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self._start(entity_type, entity_id, issued_by)

    def _start(self, entity_type, entity_id, issued_by):
        try:
            tok = mobile_upload.request_token(entity_type, entity_id, issued_by)
        except cloud_sync.Rejected as exc:
            self.info.setText(f"Serverul a refuzat: {exc.detail}")
            return
        except cloud_sync.RetryLater as exc:
            self.info.setText(f"Nu mă pot conecta la server: {exc}")
            return
        self._tid, self._url = tok["tid"], tok["url"]
        self._expires = time.time() + int(tok["expires_in"])
        png = mobile_upload.qr_png_bytes(tok.get("qr_png", ""))
        if png:
            pm = QPixmap()
            pm.loadFromData(png)
            self.qr.setPixmap(pm.scaled(260, 260, Qt.KeepAspectRatio))
        self.copy_btn.setEnabled(True)
        if tok.get("lan"):
            self.info.setText("Mod LAN (fallback): telefonul trebuie să fie în aceeași rețea.")
        self.timer.start(POLL_MS)

    def _tick(self):
        left = int(self._expires - time.time())
        if left <= 0:
            self.timer.stop()
            self.info.setText("Codul a expirat. Închide și generează unul nou.")
            return
        if self._busy:
            return
        self._busy = True
        try:
            st, msg = mobile_upload.fetch_uploaded(self._tid)
            if st.get("uses"):
                self.timer.stop()
                names = ", ".join(d["filename"] for d in st.get("documents", [])[-3:])
                self.info.setText(f"Document primit de pe telefon: {names}. {msg}")
                if self._on_done:
                    self._on_done()
                return
            self.info.setText(f"Aștept încărcarea de pe telefon… valabil încă {left // 60}:{left % 60:02d}")
        except (cloud_sync.Rejected, cloud_sync.RetryLater) as exc:
            self.info.setText(f"Verificare indisponibilă momentan: {exc}")
        finally:
            self._busy = False

    def reject(self):
        self.timer.stop()
        if self._tid and self._expires > time.time():
            try:
                mobile_upload.revoke(self._tid)         # un QR închis nu mai rămâne valabil
            except Exception:
                pass
        super().reject()


def open_phone_upload(parent, entity_type: str, entity_id, label: str = "", on_done=None, issued_by: str = "main"):
    PhoneUploadDialog(parent, entity_type, entity_id, label, on_done, issued_by).exec()
