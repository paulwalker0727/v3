"""Ecran „⚙️ Setări AI" — configurare cheie Gemini fără a atinge fișiere/CLI.

Funcții:
  * câmp cheie API (mascat implicit) + buton „👁 Arată/Ascunde"
  * „📂 Importă din fișier" (citește cheia dintr-un .txt)
  * „🔍 Testează cheia" → apel scurt la Gemini (thread separat) → validă / eroare
  * selector mod de lucru (AI+rezervă offline / doar offline)
  * dropdown model: „Automat (recomandat)" + modelele detectate
  * „💾 Salvează" → scrie configul (UTF-8) și îl încarcă imediat, fără restart
  * „🗑 Șterge cheia / Revino la offline"
  * indicator de stare
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import config as ai_config
from . import google_engine


# ============ WORKER (test cheie / listare modele) ============

class _GeminiWorker(QObject):
    done = Signal(dict)   # {"ok", "model", "message", "models": [...]}
    fail = Signal(str)

    def __init__(self, api_key: str):
        super().__init__()
        self.api_key = api_key

    @Slot()
    def run(self):
        try:
            res = google_engine.test_key(self.api_key)
            if res.get("ok"):
                try:
                    res["models"] = google_engine.list_available_models(self.api_key)
                except Exception:
                    res["models"] = []
            self.done.emit(res)
        except Exception as e:  # noqa: BLE001
            self.fail.emit(f"{type(e).__name__}: {e}")


# ============ DIALOG ============

class AISettingsDialog(QDialog):
    """Fereastra de configurare AI."""

    settings_saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙️  Setări AI")
        self.setMinimumWidth(560)
        self.setStyleSheet(
            "QDialog { background:#020617; color:#e2e8f0; }"
            "QLabel { color:#cbd5e1; }"
            "QLineEdit, QComboBox { background:#0f172a; color:#e2e8f0; "
            "border:1px solid #1e293b; border-radius:6px; padding:8px; }"
            "QComboBox QAbstractItemView { background:#0f172a; color:#e2e8f0; "
            "selection-background-color:#0ea5e9; }"
        )
        self._thread = None
        self._worker = None
        self._detected_model = None

        cfg = ai_config.load_config()

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(14)

        # --- titlu + stare ---
        title = QLabel("⚙️  Setări AI (Gemini)")
        title.setStyleSheet("font-size:18pt; font-weight:800; color:#f1f5f9;")
        root.addWidget(title)

        self.status_lbl = QLabel()
        self.status_lbl.setObjectName("ai-status-label")
        root.addWidget(self.status_lbl)
        self._refresh_status()

        root.addWidget(self._hr())

        # --- cheie API ---
        root.addWidget(self._section("🔑  Cheia API Gemini (Google AI Studio)"))

        key_row = QHBoxLayout()
        self.key_edit = QLineEdit(cfg.get("api_key", ""))
        self.key_edit.setObjectName("ai-key-input")
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("Lipește cheia (AQ.… sau AIza…)")
        key_row.addWidget(self.key_edit, 1)

        self.btn_toggle = QPushButton("👁")
        self.btn_toggle.setObjectName("ai-key-toggle")
        self.btn_toggle.setCheckable(True)
        self.btn_toggle.setFixedWidth(46)
        self.btn_toggle.setToolTip("Arată / ascunde cheia")
        self.btn_toggle.clicked.connect(self._toggle_visibility)
        self._style_btn(self.btn_toggle, "#334155", "#475569")
        key_row.addWidget(self.btn_toggle)

        self.btn_import = QPushButton("📂  Importă din fișier")
        self.btn_import.setObjectName("ai-key-import")
        self.btn_import.clicked.connect(self._import_key)
        self._style_btn(self.btn_import, "#334155", "#475569")
        key_row.addWidget(self.btn_import)
        root.addLayout(key_row)

        # --- test ---
        test_row = QHBoxLayout()
        self.btn_test = QPushButton("🔍  Testează cheia")
        self.btn_test.setObjectName("ai-key-test")
        self.btn_test.clicked.connect(self._test_key)
        self._style_btn(self.btn_test, "#0ea5e9", "#0284c7")
        test_row.addWidget(self.btn_test)

        self.test_result = QLabel("")
        self.test_result.setObjectName("ai-test-result")
        self.test_result.setWordWrap(True)
        test_row.addWidget(self.test_result, 1)
        root.addLayout(test_row)

        root.addWidget(self._hr())

        # --- mod de lucru ---
        root.addWidget(self._section("🧭  Mod de lucru"))
        self.mode_combo = QComboBox()
        self.mode_combo.setObjectName("ai-mode-combo")
        self.mode_combo.addItem("AI (Gemini) + rezervă offline (recomandat)", ai_config.MODE_AI)
        self.mode_combo.addItem("Doar offline (Tesseract)", ai_config.MODE_OFFLINE)
        idx = self.mode_combo.findData(cfg.get("mode", ai_config.MODE_AI))
        self.mode_combo.setCurrentIndex(max(0, idx))
        root.addWidget(self.mode_combo)

        # --- model ---
        root.addWidget(self._section("🤖  Model Gemini"))
        self.model_combo = QComboBox()
        self.model_combo.setObjectName("ai-model-combo")
        self._populate_models(preferred=google_engine.PREFERRED_MODELS,
                              current=cfg.get("model", ai_config.MODEL_AUTO))
        root.addWidget(self.model_combo)

        hint = QLabel("„Automat” alege la salvare primul model valid pe contul tău "
                      "(fără erori 404/429).")
        hint.setStyleSheet("color:#64748b; font-size:9pt;")
        hint.setWordWrap(True)
        root.addWidget(hint)

        root.addWidget(self._hr())

        # --- butoane ---
        btns = QHBoxLayout()
        self.btn_clear = QPushButton("🗑  Șterge cheia / Revino la offline")
        self.btn_clear.setObjectName("ai-clear-key")
        self.btn_clear.clicked.connect(self._clear_key)
        self._style_btn(self.btn_clear, "#7f1d1d", "#991b1b")
        btns.addWidget(self.btn_clear)

        btns.addStretch()

        btn_close = QPushButton("Închide")
        btn_close.setObjectName("ai-close")
        btn_close.clicked.connect(self.reject)
        self._style_btn(btn_close, "#334155", "#475569")
        btns.addWidget(btn_close)

        self.btn_save = QPushButton("💾  Salvează")
        self.btn_save.setObjectName("ai-save")
        self.btn_save.clicked.connect(self._save)
        self._style_btn(self.btn_save, "#16a34a", "#15803d")
        btns.addWidget(self.btn_save)
        root.addLayout(btns)

    # ---------------------------------------------------------------
    # helpers UI
    def _section(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#f1f5f9; font-weight:700; font-size:11pt;")
        return lbl

    def _hr(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#1e293b; background:#1e293b; max-height:1px;")
        return line

    def _style_btn(self, btn: QPushButton, bg: str, hover: str) -> None:
        btn.setStyleSheet(
            f"QPushButton {{ background:{bg}; color:white; padding:9px 14px; "
            f"border:none; border-radius:6px; font-weight:600; }}"
            f"QPushButton:hover {{ background:{hover}; }}"
            "QPushButton:disabled { background:#1e293b; color:#64748b; }"
        )

    def _refresh_status(self) -> None:
        active = ai_config.is_ai_enabled()
        txt = ai_config.status_text()
        color = "#22c55e" if active else "#f59e0b"
        icon = "🟢" if active else "🟡"
        self.status_lbl.setText(f"{icon}  Stare curentă: <b>{txt}</b>")
        self.status_lbl.setStyleSheet(f"color:{color}; font-size:11pt;")

    def _populate_models(self, preferred, current: str) -> None:
        self.model_combo.clear()
        self.model_combo.addItem("Automat (recomandat)", ai_config.MODEL_AUTO)
        seen = set()
        for m in preferred:
            if m not in seen:
                self.model_combo.addItem(m, m)
                seen.add(m)
        if current and current != ai_config.MODEL_AUTO and current not in seen:
            self.model_combo.addItem(current, current)
        idx = self.model_combo.findData(current or ai_config.MODEL_AUTO)
        self.model_combo.setCurrentIndex(max(0, idx))

    # ---------------------------------------------------------------
    # actions
    def _toggle_visibility(self) -> None:
        if self.btn_toggle.isChecked():
            self.key_edit.setEchoMode(QLineEdit.Normal)
            self.btn_toggle.setText("🙈")
        else:
            self.key_edit.setEchoMode(QLineEdit.Password)
            self.btn_toggle.setText("👁")

    def _import_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Importă cheia din fișier", "",
            "Fișiere text (*.txt *.key *.env);;Toate fișierele (*.*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Eroare", f"Nu am putut citi fișierul:\n{e}")
            return
        key = self._extract_key_from_text(content)
        if not key:
            QMessageBox.warning(self, "Fără cheie",
                                "Nu am găsit o cheie (AQ.… / AIza…) în fișier.")
            return
        self.key_edit.setText(key)
        self.test_result.setText("Cheie importată. Apasă „Testează cheia”.")
        self.test_result.setStyleSheet("color:#94a3b8;")

    @staticmethod
    def _extract_key_from_text(text: str) -> str:
        import re
        # întâi caută în formatul KEY=... apoi orice token AQ./AIza
        for line in text.splitlines():
            line = line.strip()
            if "=" in line:
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val.startswith("AQ.") or val.startswith("AIza"):
                    return val
        m = re.search(r"(AQ\.[\w\-\.]+|AIza[\w\-]+)", text)
        return m.group(1) if m else ""

    def _set_busy(self, busy: bool) -> None:
        for b in (self.btn_test, self.btn_save, self.btn_import, self.btn_clear):
            b.setEnabled(not busy)

    def _test_key(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            self.test_result.setText("❌ Introdu mai întâi o cheie.")
            self.test_result.setStyleSheet("color:#f87171;")
            return
        self._set_busy(True)
        self.test_result.setText("⏳ Testez cheia la Gemini…")
        self.test_result.setStyleSheet("color:#94a3b8;")

        self._thread = QThread(self)
        self._worker = _GeminiWorker(key)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._on_test_done)
        self._worker.fail.connect(self._on_test_fail)
        self._worker.done.connect(self._thread.quit)
        self._worker.fail.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.start()

    @Slot(dict)
    def _on_test_done(self, res: dict) -> None:
        self._set_busy(False)
        if res.get("ok"):
            self._detected_model = res.get("model")
            self.test_result.setText(f"✅ {res.get('message', 'Cheie validă')}")
            self.test_result.setStyleSheet("color:#22c55e; font-weight:600;")
            models = res.get("models") or []
            if models:
                current = self.model_combo.currentData()
                self._populate_models(preferred=models, current=current)
        else:
            self.test_result.setText(f"❌ {res.get('message', 'Cheie invalidă')}")
            self.test_result.setStyleSheet("color:#f87171; font-weight:600;")

    @Slot(str)
    def _on_test_fail(self, msg: str) -> None:
        self._set_busy(False)
        self.test_result.setText(f"❌ Eroare: {msg}")
        self.test_result.setStyleSheet("color:#f87171; font-weight:600;")

    def _clear_key(self) -> None:
        if QMessageBox.question(
            self, "Confirmare",
            "Ștergi cheia și treci în mod offline (Tesseract)?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) != QMessageBox.Yes:
            return
        ai_config.clear_key()
        self.key_edit.clear()
        self.mode_combo.setCurrentIndex(
            max(0, self.mode_combo.findData(ai_config.MODE_OFFLINE)))
        self.model_combo.setCurrentIndex(
            max(0, self.model_combo.findData(ai_config.MODEL_AUTO)))
        self.test_result.setText("Cheie ștearsă. Aplicația e în mod offline.")
        self.test_result.setStyleSheet("color:#94a3b8;")
        self._refresh_status()
        self.settings_saved.emit()

    def _save(self) -> None:
        key = self.key_edit.text().strip()
        mode = self.mode_combo.currentData()
        model = self.model_combo.currentData() or ai_config.MODEL_AUTO

        if mode == ai_config.MODE_AI and not key:
            QMessageBox.warning(
                self, "Cheie lipsă",
                "Modul „AI (Gemini)” necesită o cheie. "
                "Adaugă una sau alege „Doar offline”."
            )
            return

        # Auto-detectare model la salvare (dacă e „automat" și avem cheie)
        if mode == ai_config.MODE_AI and key and model == ai_config.MODEL_AUTO:
            self.btn_save.setEnabled(False)
            self.test_result.setText("⏳ Detectez modelul disponibil…")
            self.test_result.setStyleSheet("color:#94a3b8;")
            try:
                res = google_engine.test_key(key)
            except Exception as e:  # noqa: BLE001
                res = {"ok": False, "message": str(e)}
            self.btn_save.setEnabled(True)
            if not res.get("ok"):
                if QMessageBox.question(
                    self, "Cheie netestată",
                    f"Testul cheii a eșuat ({res.get('message')}).\n"
                    "Salvez totuși (aplicația va folosi rezerva offline la nevoie)?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                ) != QMessageBox.Yes:
                    return
            else:
                self._detected_model = res.get("model")

        ai_config.save_config({
            "api_key": key,
            "mode": mode,
            "model": model,
            "provider": "google",
        })
        self._refresh_status()
        self.settings_saved.emit()
        QMessageBox.information(
            self, "Salvat",
            f"Setările AI au fost salvate.\nStare: {ai_config.status_text()}"
        )
        self.accept()


__all__ = ["AISettingsDialog"]
