"""
cereri_notifier.py — Notificare discretă la sosirea unei cereri online noi.

Afișează o fereastră mică, NEMODALĂ (nu blochează programul), în colțul din
dreapta-jos al ecranului, cu sunet de atenționare. Se închide singură după 20 secunde
sau la click pe „Deschide cererile”.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

AUTO_CLOSE_MS = 20000


class CerereToast(QDialog):
    def __init__(self, parent, count: int, on_open=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setModal(False)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedWidth(340)
        self.setStyleSheet(
            "QDialog { background:#0f172a; border:1px solid #1f6f4a; border-radius:10px; }"
            "QLabel { color:#e2e8f0; }"
        )

        root = QVBoxLayout(self)
        title = QLabel(f"📥 {count} cerere/cereri online noi")
        title.setStyleSheet("font-size:15px; font-weight:700; color:#4ade80;")
        root.addWidget(title)

        sub = QLabel("Au fost primite din portalul public și așteaptă validare.")
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#94a3b8; font-size:12px;")
        root.addWidget(sub)

        row = QHBoxLayout()
        btn_open = QPushButton("Deschide cererile")
        btn_open.setStyleSheet("background:#16a34a; color:#fff; font-weight:700; padding:6px 12px;")
        btn_close = QPushButton("Închide")
        row.addWidget(btn_open)
        row.addWidget(btn_close)
        root.addLayout(row)

        btn_close.clicked.connect(self.close)

        def _open():
            self.close()
            if callable(on_open):
                on_open()

        btn_open.clicked.connect(_open)
        QTimer.singleShot(AUTO_CLOSE_MS, self.close)

    def show_bottom_right(self):
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.adjustSize()
            self.move(geo.right() - self.width() - 24, geo.bottom() - self.height() - 24)
        self.show()
        QApplication.beep()


def notify_new_cereri(parent, count: int, on_open=None) -> CerereToast | None:
    """Afișează notificarea. Returnează fereastra (trebuie păstrată o referință)."""
    if count <= 0:
        return None
    toast = CerereToast(parent, count, on_open)
    toast.show_bottom_right()
    return toast
