"""
rapoarte_dialog.py — Dialog interactiv pentru generarea rapoartelor PDF.

Permite utilizatorului să aleagă tipul raportului și parametrii, apoi salvează
PDF-ul într-o locație aleasă (sau deschide direct în viewer-ul sistemului).
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, timedelta
from typing import Optional

from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit, QPushButton,
    QComboBox, QDateEdit, QSpinBox, QFileDialog, QMessageBox, QGroupBox, QStackedWidget,
    QWidget, QListWidget, QListWidgetItem, QFrame, QCheckBox,
)

import database as db
import pdf_reports as pr
from app_logging import get_logger

logger = get_logger(__name__)


class QrSettingsDialog(QDialog):
    """Setare URL de bază pentru QR-uri (persistat în DB.settings)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Setări QR code")
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        root.setSpacing(12)

        title = QLabel("🔗 URL public pentru QR code-uri")
        f = QFont(); f.setPointSize(13); f.setBold(True); title.setFont(f)
        root.addWidget(title)

        info = QLabel(
            "QR code-urile vor conține URL-uri de forma:\n"
            "  <BASE_URL>/contract/<numar_contract>\n"
            "  <BASE_URL>/plata/<chitanta_nr>\n"
            "  <BASE_URL>/loc/<cod_loc>\n\n"
            "Setează aici URL-ul portalului tău public (fără slash la sfârșit)."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #667085; font-size: 10pt;")
        root.addWidget(info)

        form = QFormLayout()
        self.txt_url = QLineEdit()
        self.txt_url.setPlaceholderText("ex: https://cimitir.primaria-ta.ro")
        self.txt_url.setText(pr.get_qr_base_url())
        self.txt_url.setMinimumHeight(34)
        form.addRow("URL de bază:", self.txt_url)
        root.addLayout(form)

        # exemplu live
        self.lbl_preview = QLabel()
        self.lbl_preview.setStyleSheet(
            "background-color: #f5f7fa; color: #0e5aa7; padding: 8px 12px; "
            "border-radius: 6px; font-family: monospace; font-size: 9pt;"
        )
        self.lbl_preview.setWordWrap(True)
        root.addWidget(self.lbl_preview)
        self.txt_url.textChanged.connect(self._update_preview)
        self._update_preview()

        btn_row = QHBoxLayout()
        btn_cancel = QPushButton("Anulează")
        btn_cancel.clicked.connect(self.reject)
        btn_save = QPushButton("💾  Salvează")
        btn_save.setDefault(True)
        btn_save.setStyleSheet(
            "background-color: #0e5aa7; color: white; font-weight: 600; "
            "padding: 6px 20px; border-radius: 6px;")
        btn_save.clicked.connect(self._save)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_save)
        root.addLayout(btn_row)

    def _update_preview(self):
        base = (self.txt_url.text() or pr.DEFAULT_QR_BASE_URL).rstrip("/")
        self.lbl_preview.setText(
            f"Exemplu:  {base}/contract/C-2026-001\n"
            f"          {base}/plata/CH-100\n"
            f"          {base}/loc/A-01-05"
        )

    def _save(self):
        url = (self.txt_url.text() or "").strip().rstrip("/")
        if url and not (url.startswith("http://") or url.startswith("https://")):
            QMessageBox.warning(self, "URL invalid",
                                "URL-ul trebuie să înceapă cu http:// sau https://")
            return
        try:
            db.set_setting("qr_base_url", url)
            logger.info("qr_base_url salvat: %s", url)
        except Exception:
            logger.exception("Nu am putut salva qr_base_url")
            QMessageBox.warning(self, "Eroare", "Nu am putut salva setarea.")
            return
        self.accept()


class RapoarteDialog(QDialog):
    """Dialog central pentru generarea rapoartelor PDF."""

    REPORTS = [
        ("📄 Contract de concesiune", "contract",
         "Generează un contract PDF pentru un anumit contract existent."),
        ("🧾 Chitanță plată", "receipt",
         "Chitanță (dovadă plată) pentru o plată din registru."),
        ("⚰ Adeverință de înhumare", "burial_cert",
         "Adeverință oficială pentru o persoană înhumată."),
        ("🔔 Situație contracte expirate", "expiring",
         "Contracte expirate sau care expiră în perioada aleasă."),
        ("💰 Raport financiar", "financial",
         "Totale încasări pe perioadă, opțional pe sector."),
        ("📖 Registru înhumați", "register",
         "Lista înhumaților dintr-o perioadă (după data înhumării)."),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Rapoarte PDF")
        self.setMinimumSize(720, 500)

        root = QHBoxLayout(self)

        # LEFT: listă rapoarte
        self.list = QListWidget()
        self.list.setFixedWidth(240)
        for label, key, _desc in self.REPORTS:
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, key)
            self.list.addItem(it)
        self.list.setCurrentRow(0)
        root.addWidget(self.list)

        # RIGHT: form + preview description + generate
        right = QVBoxLayout()
        right.setSpacing(12)

        self.desc = QLabel("")
        self.desc.setWordWrap(True)
        f = QFont(); f.setPointSize(10); self.desc.setFont(f)
        self.desc.setStyleSheet("color: #667085;")
        right.addWidget(self.desc)

        # ----- forms per report -----
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_contract_form())      # 0
        self.stack.addWidget(self._build_receipt_form())       # 1
        self.stack.addWidget(self._build_burial_cert_form())   # 2
        self.stack.addWidget(self._build_expiring_form())      # 3
        self.stack.addWidget(self._build_financial_form())     # 4
        self.stack.addWidget(self._build_register_form())      # 5
        right.addWidget(self.stack, 1)

        # ----- bloc QR (checkbox + buton setări URL) -----
        qr_box = QFrame()
        qr_box.setStyleSheet(
            "QFrame { background-color: rgba(14,90,167,0.08); "
            "border: 1px solid rgba(14,90,167,0.25); border-radius: 8px; padding: 4px; }"
        )
        qr_lay = QHBoxLayout(qr_box)
        qr_lay.setContentsMargins(12, 6, 8, 6)
        self.chk_qr = QCheckBox("🔲  Include QR code (verificare online)")
        self.chk_qr.setStyleSheet("font-weight: 600;")
        self.chk_qr.setToolTip(
            "Bifează pentru a adăuga un QR code în PDF care duce la o pagină\n"
            "web publică (fără date personale). Util pentru verificare instantă\n"
            "de către rude/vizitatori cu telefonul."
        )
        self.btn_qr_settings = QPushButton("⚙  Setări URL...")
        self.btn_qr_settings.setToolTip("Configurează URL-ul public al portalului cimitirului")
        self.btn_qr_settings.setCursor(Qt.PointingHandCursor)
        self.btn_qr_settings.clicked.connect(self._open_qr_settings)
        qr_lay.addWidget(self.chk_qr, 1)
        qr_lay.addWidget(self.btn_qr_settings)
        right.addWidget(qr_box)

        # buttons
        btn_row = QHBoxLayout()
        self.btn_generate = QPushButton("📄  Generează PDF")
        self.btn_generate.setMinimumHeight(38)
        self.btn_generate.setDefault(True)
        self.btn_generate.setStyleSheet(
            "background-color: #0e5aa7; color: white; font-weight: 600; "
            "padding: 0 20px; border-radius: 6px;"
        )
        self.btn_generate.clicked.connect(self._generate)

        btn_close = QPushButton("Închide")
        btn_close.setMinimumHeight(38)
        btn_close.clicked.connect(self.reject)

        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        btn_row.addWidget(self.btn_generate)
        right.addLayout(btn_row)

        root.addLayout(right, 1)

        self.list.currentRowChanged.connect(self._on_change)
        self._on_change(0)

    # -----------------------------------------------------------------------
    def _on_change(self, idx: int):
        self.stack.setCurrentIndex(idx)
        self.desc.setText(self.REPORTS[idx][2])
        # actualizează starea bifei QR din setările per-tip (memorat între rulări)
        key = self.REPORTS[idx][1]
        try:
            saved = db.get_setting(f"qr_include_{key}")
            self.chk_qr.setChecked(saved == "1")
        except Exception:
            self.chk_qr.setChecked(False)

    # -----------------------------------------------------------------------
    # FORMS
    # -----------------------------------------------------------------------
    def _build_contract_form(self) -> QWidget:
        w = QWidget()
        lay = QFormLayout(w)
        self.cmb_contract = QComboBox()
        self._reload_contracts()
        lay.addRow("Contract:", self.cmb_contract)
        return w

    def _reload_contracts(self):
        self.cmb_contract.clear()
        try:
            for c in db.load_contracte():
                nr = c.get("numar_contract") or f"id={c['id']}"
                owner = c.get("owner_fullname") or ""
                cod = c.get("cod_loc") or "—"
                self.cmb_contract.addItem(f"{nr} · {cod} · {owner}", c["id"])
        except Exception:
            logger.exception("Nu am putut încărca contractele în combo")

    def _build_receipt_form(self) -> QWidget:
        w = QWidget()
        lay = QFormLayout(w)
        self.cmb_plata = QComboBox()
        self._reload_plati()
        lay.addRow("Plată:", self.cmb_plata)
        return w

    def _reload_plati(self):
        self.cmb_plata.clear()
        try:
            for p in db.list_plati():
                nr = p.get("chitanta_nr") or f"id={p['id']}"
                self.cmb_plata.addItem(
                    f"{nr} · {p.get('data_plata','?')} · {p.get('cod_loc','—')}"
                    f" · {p.get('suma','0')} lei", p["id"])
        except Exception:
            logger.exception("Nu am putut încărca plățile")

    def _build_burial_cert_form(self) -> QWidget:
        w = QWidget()
        lay = QFormLayout(w)
        self.cmb_inhumat = QComboBox()
        self._reload_inhumati()
        lay.addRow("Înhumat:", self.cmb_inhumat)
        return w

    def _reload_inhumati(self):
        self.cmb_inhumat.clear()
        try:
            for i in db.load_all_inhumati():
                self.cmb_inhumat.addItem(
                    f"{i.get('nume','')} {i.get('prenume','')} · "
                    f"loc {i.get('cod_loc','—')} · deces {i.get('data_deces','?')}",
                    i["id"])
        except Exception:
            logger.exception("Nu am putut încărca înhumații")

    def _build_expiring_form(self) -> QWidget:
        w = QWidget()
        lay = QFormLayout(w)
        self.spin_days = QSpinBox()
        self.spin_days.setRange(0, 3650)
        self.spin_days.setValue(30)
        self.spin_days.setSuffix(" zile")
        lay.addRow("Perioadă (zile de la azi):", self.spin_days)
        note = QLabel("Vor apărea contractele deja expirate + cele care expiră "
                      "în intervalul specificat.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #667085; font-size: 9pt;")
        lay.addRow(note)
        return w

    def _build_financial_form(self) -> QWidget:
        w = QWidget()
        lay = QFormLayout(w)
        self.date_fin_start = QDateEdit()
        self.date_fin_start.setCalendarPopup(True)
        self.date_fin_start.setDate(QDate.currentDate().addYears(-1))
        self.date_fin_end = QDateEdit()
        self.date_fin_end.setCalendarPopup(True)
        self.date_fin_end.setDate(QDate.currentDate())
        self.cmb_fin_sector = QComboBox()
        self.cmb_fin_sector.addItem("Toate sectoarele", "")
        for c in range(ord("A"), ord("Z") + 1):
            self.cmb_fin_sector.addItem(chr(c), chr(c))
        lay.addRow("De la data:", self.date_fin_start)
        lay.addRow("Până la data:", self.date_fin_end)
        lay.addRow("Sector:", self.cmb_fin_sector)
        return w

    def _build_register_form(self) -> QWidget:
        w = QWidget()
        lay = QFormLayout(w)
        self.date_reg_start = QDateEdit()
        self.date_reg_start.setCalendarPopup(True)
        self.date_reg_start.setDate(QDate.currentDate().addYears(-1))
        self.date_reg_end = QDateEdit()
        self.date_reg_end.setCalendarPopup(True)
        self.date_reg_end.setDate(QDate.currentDate())
        lay.addRow("De la data:", self.date_reg_start)
        lay.addRow("Până la data:", self.date_reg_end)
        return w

    # -----------------------------------------------------------------------
    def _open_qr_settings(self):
        dlg = QrSettingsDialog(self)
        dlg.exec()

    # -----------------------------------------------------------------------
    # GENERATE
    # -----------------------------------------------------------------------
    def _generate(self):
        idx = self.list.currentRow()
        key = self.REPORTS[idx][1]
        include_qr = self.chk_qr.isChecked()

        # persistăm preferința per tip raport (memorăm între rulări)
        try:
            db.set_setting(f"qr_include_{key}", "1" if include_qr else "0")
        except Exception:
            logger.exception("Nu am putut salva preferința QR")

        default_name = {
            "contract": "contract.pdf",
            "receipt": "chitanta.pdf",
            "burial_cert": "adeverinta_inhumare.pdf",
            "expiring": "contracte_expirate.pdf",
            "financial": "raport_financiar.pdf",
            "register": "registru_inhumati.pdf",
        }[key]

        path, _ = QFileDialog.getSaveFileName(
            self, "Salvează PDF", default_name, "PDF (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"

        try:
            if key == "contract":
                cid = self.cmb_contract.currentData()
                if not cid:
                    raise ValueError("Alege un contract.")
                data = pr.render_contract_pdf(int(cid), include_qr=include_qr)
            elif key == "receipt":
                pid = self.cmb_plata.currentData()
                if not pid:
                    raise ValueError("Alege o plată.")
                data = pr.render_receipt_pdf(int(pid), include_qr=include_qr)
            elif key == "burial_cert":
                iid = self.cmb_inhumat.currentData()
                if not iid:
                    raise ValueError("Alege un înhumat.")
                data = pr.render_burial_certificate_pdf(int(iid), include_qr=include_qr)
            elif key == "expiring":
                data = pr.render_expiring_contracts_pdf(
                    days=int(self.spin_days.value()), include_qr=include_qr)
            elif key == "financial":
                s = self.date_fin_start.date().toString("yyyy-MM-dd")
                e = self.date_fin_end.date().toString("yyyy-MM-dd")
                sector = self.cmb_fin_sector.currentData() or None
                data = pr.render_financial_report_pdf(
                    start=s, end=e, sector=sector, include_qr=include_qr)
            elif key == "register":
                s = self.date_reg_start.date().toString("yyyy-MM-dd")
                e = self.date_reg_end.date().toString("yyyy-MM-dd")
                data = pr.render_burials_register_pdf(
                    start=s, end=e, include_qr=include_qr)
            else:
                raise ValueError(f"Raport necunoscut: {key}")

            pr.save_pdf(data, path)
            logger.info("Raport %s generat: %s (qr=%s)", key, path, include_qr)

            qr_msg = " (cu QR code)" if include_qr else ""
            reply = QMessageBox.question(
                self, "PDF generat",
                f"PDF salvat cu succes{qr_msg}:\n{path}\n\nDeschide fișierul acum?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                self._open_pdf(path)

        except ValueError as e:
            QMessageBox.warning(self, "Date insuficiente", str(e))
        except Exception as e:
            logger.exception("Eroare la generarea raportului %s", key)
            QMessageBox.critical(self, "Eroare",
                                 f"Nu am putut genera raportul:\n{e}")

    # -----------------------------------------------------------------------
    def _open_pdf(self, path: str):
        """Deschide PDF-ul cu aplicația implicită a OS-ului."""
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: SIM115
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            logger.exception("Nu am putut deschide PDF-ul %s", path)
