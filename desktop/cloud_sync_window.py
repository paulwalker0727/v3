"""
cloud_sync_window.py — Fereastra „Sincronizare cloud".

Permite operatorului să configureze serverul cloud (adresă + cheia cimitirului),
să testeze conexiunea și să ruleze sincronizarea (hartă → cloud, cereri → desktop,
decizii → cloud). Sincronizarea rulează pe un thread separat, ca UI-ul să nu înghețe.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QPlainTextEdit, QGroupBox, QMessageBox,
)

import cloud_sync


class _SyncThread(QThread):
    done = Signal(dict)

    def run(self):
        self.done.emit(cloud_sync.sync_all())


class CloudSyncWindow(QDialog):
    """Configurare + rulare sincronizare cu serverul cloud."""

    synced = Signal()  # emis după o sincronizare (pentru badge-ul „Cereri online")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sincronizare cloud")
        self.setMinimumSize(620, 480)
        self._thread = None

        root = QVBoxLayout(self)

        box_cfg = QGroupBox("Conexiune server")
        form = QFormLayout(box_cfg)
        cfg = cloud_sync.get_config()

        self.txt_url = QLineEdit(cfg["url"])
        self.txt_url.setPlaceholderText("ex: https://server-cimitir.ro")
        self.txt_key = QLineEdit(cfg["key"])
        self.txt_key.setPlaceholderText("cheia MAIN din /admin/setari (forma id_cimitir.secret)")
        self.chk_auto = QCheckBox("Sincronizează automat la fiecare 5 minute")
        self.chk_auto.setChecked(cfg["auto"])
        self.chk_burials = QCheckBox("Permite căutarea publică după numele persoanelor înhumate")
        self.chk_burials.setToolTip(
            "Publică în portal doar NUMELE și ANII (naștere – deces) + codul locului.\n"
            "NU se publică CNP, adresă sau alte date. Debifat = nimic nu apare public."
        )
        self.chk_burials.setChecked(cfg["burials"])

        form.addRow("Adresă server:", self.txt_url)
        form.addRow("Cheie sincronizare:", self.txt_key)
        self.chk_entities = QCheckBox("Sincronizează registrul (locuri, concesionari, contracte, înhumați, plăți) prin server")
        self.chk_entities.setToolTip("Serverul (PostgreSQL) devine sursa principală când există internet; fără internet "
                                     "lucrezi local și modificările se trimit automat la reconectare.")
        try:
            import entity_sync
            self.chk_entities.setChecked(entity_sync.enabled())
        except Exception:
            self.chk_entities.setChecked(True)
        form.addRow("", self.chk_auto)
        form.addRow("", self.chk_entities)
        form.addRow("", self.chk_burials)
        root.addWidget(box_cfg)

        btns = QHBoxLayout()
        self.btn_save = QPushButton("Salvează")
        self.btn_test = QPushButton("Testează conexiunea")
        self.btn_sync = QPushButton("Sincronizează acum")
        self.btn_sync.setDefault(True)
        for b in (self.btn_save, self.btn_test, self.btn_sync):
            btns.addWidget(b)
        btns.addStretch(1)
        root.addLayout(btns)

        btns2 = QHBoxLayout()
        self.btn_preview = QPushButton("🌐 Previzualizează cum arată în portal")
        self.btn_preview.setToolTip("Deschide în browser pagina publică cu harta cimitirului")
        self.btn_republish = QPushButton("♻ Republicare hartă din MAIN")
        self.btn_republish.setToolTip("Retrimite harta și disponibilitatea din MAIN către server, după confirmare. "
                                      "Datele locale, cererile, contractele și înhumările nu sunt modificate.")
        self.btn_demo = QPushButton("🗑 Șterge datele demo din portal")
        self.btn_demo.setToolTip("Șterge locurile fictive folosite pentru demonstrație. "
                                 "Datele reale, sincronizate din program, NU sunt atinse.")
        self.btn_conflicts = QPushButton("⚠ Conflicte")
        self.btn_conflicts.setToolTip("Obiecte modificate pe două calculatoare: alege versiunea corectă.")
        for b in (self.btn_preview, self.btn_republish, self.btn_demo, self.btn_conflicts):
            btns2.addWidget(b)
        btns2.addStretch(1)
        root.addLayout(btns2)

        self.lbl_state = QLabel(self._state_text())
        self.lbl_state.setWordWrap(True)
        root.addWidget(self.lbl_state)

        root.addWidget(QLabel("Jurnal:"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        root.addWidget(self.log, 1)

        info = QLabel(
            "Portalul public primește doar harta (poligoane, cod loc, sector, status, suprafață) — "
            "fără nume, CNP sau alte date personale (GDPR). Cererile cetățenilor sunt aduse în "
            "fereastra „Cereri online”, iar deciziile tale (validat / respins) urcă înapoi în cloud. "
            "Dacă nu există internet, totul rămâne în coadă și se reia automat."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #94a3b8; font-size: 11px;")
        root.addWidget(info)

        self.btn_save.clicked.connect(self._save)
        self.btn_test.clicked.connect(self._test)
        self.btn_sync.clicked.connect(self._sync)
        self.btn_preview.clicked.connect(self._preview)
        self.btn_demo.clicked.connect(self._delete_demo)
        self.btn_republish.clicked.connect(self._republish)
        self.btn_conflicts.clicked.connect(self._conflicts)

    def _conflicts(self):
        from cloud_conflicts_window import CloudConflictsDialog
        CloudConflictsDialog(self).exec()
        self.lbl_state.setText(self._state_text())

    def _preview(self):
        if not self._save():
            return
        url = cloud_sync.portal_url()
        if not url:
            QMessageBox.warning(self, "Adresă lipsă", "Completează mai întâi adresa serverului.")
            return
        QDesktopServices.openUrl(QUrl(url))
        self.log.appendPlainText(f"Deschis în browser: {url}")

    def _republish(self):
        if not self._save():
            return
        if not cloud_sync.is_configured():
            QMessageBox.warning(self, "Configurare incompletă", "Completează adresa serverului și cheia de sincronizare.")
            return
        try:
            info = cloud_sync.republish_preview()
        except Exception as exc:
            QMessageBox.warning(self, "Eroare", f"Nu pot compara cu serverul: {exc}")
            return
        if info["in_sync"] and not info["differences"]:
            QMessageBox.information(self, "Republicare", "Harta de pe server este identică cu cea din MAIN.")
            return
        lines = info["differences"] or ["Harta de pe server diferă de cea din MAIN."]
        if QMessageBox.question(
            self, "Republicare hartă din MAIN",
            "Diferențe găsite:\n• " + "\n• ".join(lines) +
            "\n\nMAIN rămâne sursa oficială. Se trimite către server doar harta și disponibilitatea "
            "(proiecția publică). Cererile, documentele, contractele și înhumările NU sunt modificate, "
            "iar modificările locale nepublicate rămân neatinse.\n\nContinui republicarea?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) != QMessageBox.Yes:
            return
        try:
            cloud_sync.force_republish()
        except Exception as exc:
            QMessageBox.warning(self, "Eroare", str(exc))
            return
        self.log.appendPlainText("Republicare confirmată de operator.")
        self._sync()

    def _delete_demo(self):
        if not self._save():
            return
        if QMessageBox.question(
            self, "Șterge datele demo",
            "Se șterg din portalul public locurile și sectoarele DEMO (fictive).\n\n"
            "Datele reale, sincronizate din acest program, NU sunt atinse.\n\nContinui?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) != QMessageBox.Yes:
            return
        ok, msg = cloud_sync.delete_demo_data()
        self.log.appendPlainText(msg)
        QMessageBox.information(self, "Date demo" if ok else "Eroare", msg)

    def _state_text(self) -> str:
        cfg = cloud_sync.get_config()
        try:
            pending = cloud_sync.pending_count()
        except Exception:
            pending = 0
        last = cfg["last"] or "niciodată"
        text = f"Ultima sincronizare reușită: {last} · Operații în coadă: {pending}"
        try:
            import entity_sync
            summary = entity_sync.status_summary()
            text += f" · {'ONLINE' if summary['online'] else 'OFFLINE'} · conflicte: {summary['conflicts']}"
        except Exception:
            pass
        if cfg.get("last_error"):
            text += f"\nUltima eroare: {cfg['last_error']}"
        return text

    def _save(self) -> bool:
        url, key = self.txt_url.text().strip(), self.txt_key.text().strip()
        if url or key:
            errors = cloud_sync.validate_config(url, key)
            if errors:
                self.log.appendPlainText("Configurația NU a fost salvată: " + " ".join(errors))
                QMessageBox.warning(self, "Configurație invalidă", "\n".join(errors))
                return False
        cloud_sync.save_config(self.txt_url.text(), self.txt_key.text(), self.chk_auto.isChecked(),
                               self.chk_burials.isChecked())
        cloud_sync.ensure_tables()
        try:
            import entity_sync
            if entity_sync.enabled() != self.chk_entities.isChecked():
                entity_sync.set_enabled(self.chk_entities.isChecked())
        except Exception as exc:
            self.log.appendPlainText(f"Sincronizare registru: {exc}")
        self.log.appendPlainText("Configurație salvată.")
        return True

    def _test(self):
        errors = cloud_sync.validate_config(self.txt_url.text(), self.txt_key.text())
        if errors:
            self.log.appendPlainText("Test oprit: " + " ".join(errors))
            QMessageBox.warning(self, "Configurație invalidă", "\n".join(errors))
            return
        ok, msg = cloud_sync.check_connection(self.txt_url.text(), self.txt_key.text())
        if ok:
            if not self._save():
                return
            ok, msg2 = cloud_sync.ping()
            msg = msg if ok else msg2
        self.log.appendPlainText(msg)
        self.log.appendPlainText(msg)
        QMessageBox.information(self, "Test conexiune" if ok else "Conexiune eșuată", msg)

    def _sync(self):
        if not self._save():
            return
        if not cloud_sync.is_configured():
            QMessageBox.warning(self, "Configurare incompletă",
                                "Completează adresa serverului și cheia de sincronizare.")
            return
        self.btn_sync.setEnabled(False)
        self.btn_sync.setText("Se sincronizează…")
        self.log.appendPlainText("— Pornire sincronizare —")
        self._thread = _SyncThread(self)
        self._thread.done.connect(self._on_done)
        self._thread.start()

    def _on_done(self, report: dict):
        for line in report.get("lines", []):
            self.log.appendPlainText(line)
        self.log.appendPlainText("— Sincronizare încheiată —" if report.get("ok")
                                 else "— Sincronizare parțială (se reia automat) —")
        self.btn_sync.setEnabled(True)
        self.btn_sync.setText("Sincronizează acum")
        self.lbl_state.setText(self._state_text())
        self.synced.emit()
