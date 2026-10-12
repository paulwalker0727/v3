# ui_mainwindow.py
from typing import Optional, Dict
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QSplitter,
    QToolBar, QStatusBar, QDialog, QLabel, QVBoxLayout,
    QSlider, QMessageBox, QGraphicsSimpleTextItem
)
from PySide6.QtGui import QAction, QIcon, QPolygonF, QColor, QFont, QPen
from PySide6.QtCore import Qt, QSize, QPointF, Signal, QObject, QTimer

from map_view import CemeteryMapView
from panels import create_right_panel

from map_editor_tools import EditorTools
from map_editor_sectors import SectorManager
from map_editor_actions import EditorActions
from map_editor_events import EditorEvents
from map_editor_modes import EditorModes
from map_editor_advanced import AdvancedEditor

from PySide6.QtWidgets import QGraphicsPolygonItem

from app_logging import get_logger
import database as db

logger = get_logger(__name__)


class AppSignals(QObject):
    data_changed = Signal(str, str)   # entity, key


def show_message(parent, title: str, text: str, level: str = "info"):
    """
    Utility to show consistent message boxes.
    level: 'info', 'warning', 'error'
    """
    msg = QMessageBox(parent)
    msg.setWindowTitle(title)
    msg.setText(text)
    if level == "warning":
        msg.setIcon(QMessageBox.Warning)
    elif level == "error":
        msg.setIcon(QMessageBox.Critical)
    else:
        msg.setIcon(QMessageBox.Information)
    msg.exec()


class CemeteryUI(QMainWindow):
    logout_requested = Signal()
    registry_switch_requested = Signal()

    def __init__(self, current_user=None):
        super().__init__()

        self.current_user = current_user or {"username": "admin", "role": "admin"}

        username = self.current_user.get("username", "?")
        self.setWindowTitle(f"Cimitir — Mod Developer (utilizator: {username})")
        self.setMinimumSize(1500, 850)

        # semnale globale
        self.signals = AppSignals()
        self._cereri_last_count: Optional[int] = None

        # hartă
        self.map_view = CemeteryMapView()
        try:
            self.map_view.set_pan_mode(True)
        except Exception:
            pass

        # state editor
        self.sectors: Dict[str, Dict] = {}
        self.active_sector_id: Optional[str] = None
        self.current_points: list[tuple[float, float]] = []

        # module editor
        self.modes = EditorModes(self)
        self.tools = EditorTools(self)
        self.sector_mgr = SectorManager(self)
        self.actions = EditorActions(self)
        self.events = EditorEvents(self)
        self.adv = AdvancedEditor(self)
        from cemetery_features import CemeteryFeatures
        self.features = CemeteryFeatures(self)

        # panou dreapta
        self.right_panel = create_right_panel(self)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.map_view)
        splitter.addWidget(self.right_panel)
        splitter.setSizes([1100, 400])

        central = QWidget()
        layout = QHBoxLayout()
        layout.addWidget(splitter)
        central.setLayout(layout)
        self.setCentralWidget(central)

        # toolbars
        self.build_top_toolbar()
        self.build_editor_toolbar()
        self.build_map_toolbar()
        self.build_advanced_toolbar()

        self.setStatusBar(QStatusBar())
        self._build_sync_status_panel()

        # Cererile depuse în portalul local se văd fără import sau copiere de DB.
        self._cereri_timer = QTimer(self)
        self._cereri_timer.setInterval(15000)
        self._cereri_timer.timeout.connect(lambda: self.refresh_cereri_badge(announce=True))
        self._cereri_timer.start()
        from backup_window import start_scheduler
        self._backup_timer = start_scheduler(self)

        # sincronizare automată cu serverul cloud (fundal, la pornire + la fiecare 5 min)
        self._auto_sync = None
        try:
            from cloud_sync_auto import AutoSync
            self._auto_sync = AutoSync(self)
            self._auto_sync.finished.connect(self._on_auto_sync_done)
            self._auto_sync.start()
        except Exception:
            self._auto_sync = None

        # încărcare inițială (defensiv)
        fn = getattr(self.actions, "load_saved_map", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

        fn = getattr(self.actions, "load_all_plots", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

        fn = getattr(self.actions, "load_all_sectors", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

        # editor avansat: grid + ghidaje + rânduri
        try:
            self.adv.load_all()
        except Exception:
            pass

        # backup automat zilnic
        try:
            import reports_export as rex
            rex.auto_backup_daily()
        except Exception:
            pass

        # event filter pe viewport
        try:
            self.map_view.viewport().installEventFilter(self)
        except Exception:
            pass
        # keyboard focus + event filter pe view (pentru ESC/Backspace/Enter)
        try:
            self.map_view.installEventFilter(self)
            self.map_view.setFocusPolicy(Qt.StrongFocus)
            self.map_view.setFocus()
        except Exception:
            pass



        # mod default
        self.set_tool_mode("select")

        self.selected_cod_loc: Optional[str] = None

    # ---------------------------
    # click pe loc (legătură cu panourile)
    # ---------------------------
    def on_plot_clicked(self, item):
        data = item.data(0)
        if not isinstance(data, dict):
            return

        cod_loc = data.get("cod_loc")
        if not cod_loc:
            return

        self.selected_cod_loc = cod_loc
        try:
            self.statusBar().showMessage(f"Loc selectat: {cod_loc}")
        except Exception:
            pass

        # încărcăm datele complete (metadata + extra)
        try:
            fn = getattr(self.actions, "load_loc_by_cod", None)
            if callable(fn):
                loc_full = fn(cod_loc) or {}
            else:
                loc_full = {}
        except Exception:
            loc_full = {}

        # construim merged dict cu câmpuri relevante
        merged = {}
        try:
            extra_data = loc_full.get("extra_data", {}) if isinstance(loc_full.get("extra_data", {}), dict) else {}
            merged.update(extra_data)
            for k in (
                "sector",
                "parcela",
                "rand",
                "numar",
                "capacitate",
                "disponibilitate",
                "tip",
                "stare",
                "categorie",
            ):
                if k in loc_full and loc_full[k] is not None:
                    merged[k] = loc_full[k]
        except Exception:
            pass

        # actualizăm panoul din dreapta cu date complete
        if hasattr(self.right_panel, "update_loc_details"):
            try:
                self.right_panel.update_loc_details(cod_loc, merged)
            except Exception:
                pass

        # afișăm suprafața în status bar
        try:
            supr = loc_full.get("suprafata")
            if supr not in (None, "", 0):
                unit = "m²" if self.adv.get_ppm() else "px²"
                self.statusBar().showMessage(f"Loc selectat: {cod_loc}  |  suprafață ≈ {supr} {unit}")
        except Exception:
            pass

        # emit semnal global
        try:
            self.signals.data_changed.emit("loc", cod_loc)
        except Exception:
            pass

    # ---------------------------
    # toolbars
    # ---------------------------
    def build_top_toolbar(self):
        toolbar = QToolBar("Meniu principal")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(32, 32))
        self.addToolBar(Qt.TopToolBarArea, toolbar)

        act_locuri = QAction(QIcon("icons/locuri.png"), "Locuri de veci", self)
        act_locuri.triggered.connect(self.open_locuri_window)
        toolbar.addAction(act_locuri)

        act_inhumati = QAction(QIcon("icons/inhumati.png"), "Înhumați", self)
        act_inhumati.triggered.connect(self.open_inhumati_window)
        toolbar.addAction(act_inhumati)

        act_concesionari = QAction(QIcon("icons/concesionari.png"), "Concesionari", self)
        act_concesionari.triggered.connect(self.open_concesionari_window)
        toolbar.addAction(act_concesionari)

        # ---- Cereri de concesionare primite din portalul public (cu badge) ----
        self.act_cereri = QAction("📥 Cereri online", self)
        self.act_cereri.setToolTip("Cereri de concesionare trimise online de cetățeni — revizuiește și validează")
        self.act_cereri.triggered.connect(self.open_cereri_online_window)
        toolbar.addAction(self.act_cereri)
        self.refresh_cereri_badge()

        # ---- Sincronizare cu serverul cloud (portal public + cereri online) ----
        act_cloud = QAction("☁ Sincronizare cloud", self)
        act_cloud.setToolTip("Trimite harta în portalul public și adu cererile online din cloud")
        act_cloud.triggered.connect(self.open_cloud_sync_window)
        toolbar.addAction(act_cloud)
        act_backup = QAction("💾 Copii de rezervă", self)
        act_backup.setObjectName("act-backup")
        act_backup.setToolTip("Copie zilnică automată a bazei și restaurare dintr-un clic")
        act_backup.triggered.connect(self.open_backup_window)
        toolbar.addAction(act_backup)

        act_contracte = QAction(QIcon("icons/contracte.png"), "Contracte", self)
        act_contracte.triggered.connect(self.open_contracte_window)
        toolbar.addAction(act_contracte)

        act_plati = QAction(QIcon("icons/plati.png"), "Plăți", self)
        act_plati.triggered.connect(self.open_plati_window)
        toolbar.addAction(act_plati)

        act_amenajari = QAction(QIcon("icons/amenajari.png"), "Amenajări", self)
        act_amenajari.triggered.connect(self.open_amenajari_window)
        toolbar.addAction(act_amenajari)

        toolbar.addSeparator()

        act_rezumat = QAction("📊 Rezumat", self)
        act_rezumat.triggered.connect(lambda: self.features.show_summary())
        toolbar.addAction(act_rezumat)

        act_alerte = QAction("🔔 Alerte", self)
        act_alerte.triggered.connect(self.open_alerts_window)
        toolbar.addAction(act_alerte)

        self.act_status_color = QAction("🎨 Colorare status", self)
        self.act_status_color.setCheckable(True)
        self.act_status_color.triggered.connect(lambda: self.features.toggle_status_coloring())
        toolbar.addAction(self.act_status_color)

        act_export = QAction("📤 Export", self)
        act_export.triggered.connect(self.open_export_dialog)
        toolbar.addAction(act_export)

        act_import = QAction("📥 Import locuri", self)
        act_import.triggered.connect(self.import_locuri_dialog)
        toolbar.addAction(act_import)

        act_backup = QAction("💾 Backup", self)
        act_backup.triggered.connect(self.backup_now)
        toolbar.addAction(act_backup)

        act_map_export = QAction("🗺 Export hartă", self)
        act_map_export.triggered.connect(self.export_map_dialog)
        toolbar.addAction(act_map_export)

        act_rapoarte = QAction("📄 Rapoarte PDF", self)
        act_rapoarte.triggered.connect(self.open_rapoarte_dialog)
        toolbar.addAction(act_rapoarte)

        act_qr = QAction("🔲 QR / Etichete", self)
        act_qr.setToolTip("Generează QR code sau etichete tipăribile pentru locurile de veci")
        act_qr.triggered.connect(self.open_qr_labels_dialog)
        toolbar.addAction(act_qr)

        act_qr_custom = QAction("🎫 QR Personalizat", self)
        act_qr_custom.setToolTip("Generează QR cu date EMBEDATE (concesionar, CNP, contract etc.) — selectezi tu ce apare în cod")
        act_qr_custom.triggered.connect(self.open_qr_custom_dialog)
        toolbar.addAction(act_qr_custom)

        toolbar.addSeparator()

        # ---- AI Scanare (buletin & certificat deces) ----
        act_ai_scanare = QAction("🤖 AI Scanare", self)
        act_ai_scanare.setToolTip("Scanare AI documente: buletin CI (creare concesionar) sau certificat deces (creare inhumat) — Powered by Gemini 3 Flash")
        act_ai_scanare.triggered.connect(self.open_ai_scanare_window)
        toolbar.addAction(act_ai_scanare)

        act_ai_settings = QAction("⚙️ Setări AI", self)
        act_ai_settings.setToolTip("Configurează cheia Gemini, modul de lucru și modelul (fără a atinge fișiere)")
        act_ai_settings.triggered.connect(self.open_ai_settings)
        toolbar.addAction(act_ai_settings)

        # căutare loc pe hartă
        from PySide6.QtWidgets import QLineEdit as _QLE
        self.search_box = _QLE()
        self.search_box.setPlaceholderText("🔎 caută loc pe hartă (cod)…")
        self.search_box.setMaximumWidth(220)
        self.search_box.setObjectName("map-search-box")
        self.search_box.returnPressed.connect(
            lambda: self.features.search_plot(self.search_box.text()))
        toolbar.addWidget(self.search_box)

        # spacer flexibil (împinge butonul de logout la dreapta)
        from PySide6.QtWidgets import QWidget as _QW, QSizePolicy as _QS
        spacer = _QW()
        spacer.setSizePolicy(_QS.Expanding, _QS.Preferred)
        toolbar.addWidget(spacer)

        # badge cu user + buton logout
        from PySide6.QtWidgets import QLabel as _QL
        badge = _QL(f"  👤  {self.current_user.get('username','?')}  ")
        badge.setProperty("class", "badge-primary")
        badge.setStyleSheet(
            "background-color: rgba(14,165,233,0.15); "
            "color: #0ea5e9; padding: 4px 12px; border-radius: 10px; font-weight: 600;"
        )
        toolbar.addWidget(badge)

        act_registry = QAction("🗂 Schimbă registrul", self)
        act_registry.triggered.connect(self._do_switch_registry)
        toolbar.addAction(act_registry)

        act_logout = QAction("🚪 Deconectare", self)
        act_logout.triggered.connect(self._do_logout)
        toolbar.addAction(act_logout)

    def closeEvent(self, event):
        auto = getattr(self, "_auto_sync", None)
        if auto is not None:
            try:
                auto.stop()
            except Exception:
                pass
        super().closeEvent(event)

    def _do_switch_registry(self):
        reply = QMessageBox.question(
            self, "Schimbă registrul",
            "Fereastra se închide și alegi alt cimitir. Datele cimitirului curent rămân neschimbate. Continui?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.registry_switch_requested.emit()
            self.close()

    def _do_logout(self):
        reply = QMessageBox.question(
            self, "Deconectare",
            "Ești sigur că vrei să te deconectezi?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.logout_requested.emit()
            self.close()

    def build_editor_toolbar(self):
        toolbar = QToolBar("Editor hartă")
        toolbar.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, toolbar)

        act_load_map = QAction("🗺 Încarcă hartă", self)
        act_load_map.triggered.connect(lambda: self._safe_action_call("load_map"))
        toolbar.addAction(act_load_map)

        toolbar.addSeparator()

        self.act_select = QAction("🖱 Selectează", self)
        self.act_select.setCheckable(True)
        self.act_select.triggered.connect(lambda: self.set_tool_mode("select"))
        toolbar.addAction(self.act_select)

        self.act_draw = QAction("✏ Desenează poligon", self)
        self.act_draw.setCheckable(True)
        self.act_draw.triggered.connect(lambda: self.set_tool_mode("draw"))
        toolbar.addAction(self.act_draw)

        self.act_delete = QAction("🗑 Șterge loc", self)
        self.act_delete.setCheckable(True)
        self.act_delete.triggered.connect(lambda: self.set_tool_mode("delete"))
        toolbar.addAction(self.act_delete)

        toolbar.addSeparator()

        self.act_draw_sector = QAction("🏷 Desenează sector", self)
        self.act_draw_sector.setCheckable(True)
        self.act_draw_sector.triggered.connect(lambda: self.set_tool_mode("draw_sector"))
        toolbar.addAction(self.act_draw_sector)

        self.act_delete_sector = QAction("🗑 Șterge sector", self)
        self.act_delete_sector.setCheckable(True)
        self.act_delete_sector.triggered.connect(lambda: self.set_tool_mode("delete_sector"))
        toolbar.addAction(self.act_delete_sector)

        toolbar.addSeparator()

        self.act_new_sector = QAction("📂 Sector nou", self)
        self.act_new_sector.triggered.connect(self.sector_mgr.create_sector)
        toolbar.addAction(self.act_new_sector)

        self.act_color_sector = QAction("🎨 Culoare sector", self)
        self.act_color_sector.triggered.connect(self.sector_mgr.change_sector_color)
        toolbar.addAction(self.act_color_sector)

        self.act_edit_sector = QAction("✏ Editează sector", self)
        # conectare defensivă: metoda există în clasă
        if hasattr(self, "start_sector_edit") and callable(getattr(self, "start_sector_edit")):
            self.act_edit_sector.triggered.connect(self.start_sector_edit)
        else:
            self.act_edit_sector.triggered.connect(lambda: show_message(self, "Info", "Editor sector indisponibil.", level="warning"))
        toolbar.addAction(self.act_edit_sector)

        toolbar.addSeparator()

        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(0, 255)
        self.opacity_slider.setValue(150)
        self.opacity_slider.setFixedWidth(120)
        self.opacity_slider.valueChanged.connect(self.update_sector_opacity)
        toolbar.addWidget(self.opacity_slider)

        toolbar.addSeparator()

        self.act_preview = QAction("👁 Preview", self)
        self.act_preview.setCheckable(True)
        self.act_preview.setChecked(True)
        self.act_preview.triggered.connect(self.toggle_preview)
        toolbar.addAction(self.act_preview)

        toolbar.addSeparator()

        act_save = QAction("💾 Salvează hartă", self)
        act_save.triggered.connect(lambda: self._safe_action_call("save_map"))
        toolbar.addAction(act_save)

    def build_advanced_toolbar(self):
        toolbar = QToolBar("Editor avansat")
        toolbar.setMovable(False)
        self.addToolBar(Qt.BottomToolBarArea, toolbar)

        act_calib = QAction("📐 Calibrare scară", self)
        act_calib.triggered.connect(lambda: self.adv.start_calibrate())
        toolbar.addAction(act_calib)

        toolbar.addSeparator()

        self.act_grid = QAction("▦ Grid", self)
        self.act_grid.setCheckable(True)
        self.act_grid.triggered.connect(lambda: self.adv.toggle_grid())
        toolbar.addAction(self.act_grid)

        act_grid_cfg = QAction("⚙ Config grid", self)
        act_grid_cfg.triggered.connect(lambda: self.adv.configure_grid())
        toolbar.addAction(act_grid_cfg)

        toolbar.addSeparator()

        act_gh = QAction("─ Ghidaj oriz.", self)
        act_gh.triggered.connect(lambda: self.set_tool_mode("draw_guide_h"))
        toolbar.addAction(act_gh)

        act_gv = QAction("│ Ghidaj vert.", self)
        act_gv.triggered.connect(lambda: self.set_tool_mode("draw_guide_v"))
        toolbar.addAction(act_gv)

        act_gl = QAction("╱ Ghidaj linie", self)
        act_gl.triggered.connect(lambda: self.set_tool_mode("draw_guide_line"))
        toolbar.addAction(act_gl)

        act_gclear = QAction("🗑 Șterge ghidaje", self)
        act_gclear.triggered.connect(lambda: self.adv.clear_all_guides())
        toolbar.addAction(act_gclear)

        toolbar.addSeparator()

        act_row = QAction("➖ Rând nou", self)
        act_row.triggered.connect(lambda: self.adv.start_draw_row())
        toolbar.addAction(act_row)

        act_rows = QAction("📋 Gestionează rânduri", self)
        act_rows.triggered.connect(lambda: self.adv.manage_rows())
        toolbar.addAction(act_rows)

        act_gen = QAction("▦ Generează locuri pe rând", self)
        act_gen.triggered.connect(lambda: self.adv.generate_plots_dialog())
        toolbar.addAction(act_gen)

        toolbar.addSeparator()

        act_style = QAction("🎨 Stil linii", self)
        act_style.triggered.connect(lambda: self.adv.configure_line_style())
        toolbar.addAction(act_style)

        act_ruler = QAction("📏 Riglă", self)
        act_ruler.triggered.connect(lambda: self.adv.start_ruler())
        toolbar.addAction(act_ruler)

        try:
            self.act_grid.setChecked(self.adv.grid_cfg()["visible"])
        except Exception:
            pass

    def build_map_toolbar(self):
        toolbar = QToolBar("Instrumente hartă")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(22, 22))
        self.addToolBar(Qt.BottomToolBarArea, toolbar)

        act_load = QAction("🗺 Încarcă hartă", self)
        act_load.triggered.connect(lambda: self._safe_action_call("load_map"))
        toolbar.addAction(act_load)

        toolbar.addSeparator()

        act_fit = QAction("🔍 Fit hartă", self)
        act_fit.setToolTip("Încadrează harta în fereastră (scurtătură: F sau Home)")
        act_fit.triggered.connect(lambda: self.map_view.fit_to_content())
        toolbar.addAction(act_fit)

        toolbar.addSeparator()

        self.act_pan = QAction("✋ Pan", self)
        self.act_pan.setCheckable(True)
        self.act_pan.setChecked(True)
        self.act_pan.setToolTip("Pan hartă. Poți naviga oricând și cu butonul din mijloc al mouse-ului.")
        self.act_pan.triggered.connect(lambda: self.exit_draw_mode())
        toolbar.addAction(self.act_pan)

        lbl = QLabel("  🖱 mijloc = navighezi oricând  ")
        lbl.setStyleSheet("color:#64748b;")
        toolbar.addWidget(lbl)

    # ---------------------------
    # ferestre mici / deschidere module (importuri lazy)
    # ---------------------------
    def open_locuri_window(self):
        try:
            from locuri_window import LocuriWindow
        except Exception as e:
            show_message(self, "Eroare", f"Nu am putut deschide Locuri: {e}", level="error")
            return

        dlg = LocuriWindow(self)
        try:
            if hasattr(dlg, "on_data_changed"):
                self.signals.data_changed.connect(dlg.on_data_changed)
        except Exception:
            pass
        dlg.exec()
        try:
            if hasattr(dlg, "on_data_changed"):
                self.signals.data_changed.disconnect(dlg.on_data_changed)
        except Exception:
            pass

    def open_inhumati_window(self):
        try:
            from inhumati_window import InhumatiWindow
        except Exception as e:
            show_message(self, "Eroare", f"Nu am putut deschide Înhumați: {e}", level="error")
            return

        dlg = InhumatiWindow(self)
        try:
            if hasattr(dlg, "on_data_changed"):
                self.signals.data_changed.connect(dlg.on_data_changed)
        except Exception:
            pass
        dlg.exec()
        try:
            if hasattr(dlg, "on_data_changed"):
                self.signals.data_changed.disconnect(dlg.on_data_changed)
        except Exception:
            pass

    def refresh_cereri_badge(self, announce: bool = False):
        """Actualizează badge-ul și anunță numai cererile locale nou sosite."""
        previous = self._cereri_last_count
        try:
            n = db.count_cereri_noi()
        except Exception:
            n = 0
        self._cereri_last_count = n
        act = getattr(self, "act_cereri", None)
        if act is not None:
            act.setText(f"📥 Cereri online ({n})" if n else "📥 Cereri online")
        if announce and previous is not None and n > previous:
            try:
                from cereri_notifier import notify_new_cereri
                self._cerere_toast = notify_new_cereri(
                    self, n - previous, self.open_cereri_online_window
                )
            except Exception:
                pass

    def open_cereri_online_window(self):
        try:
            from cereri_online_window import CereriOnlineWindow
        except Exception as e:
            show_message(self, "Eroare", f"Nu am putut deschide Cereri online: {e}", level="error")
            return
        try:
            dlg = CereriOnlineWindow(self, actions=self.actions)
            dlg.changed.connect(self.refresh_cereri_badge)
        except Exception as e:
            show_message(self, "Eroare", f"Eroare la inițializare Cereri online: {e}", level="error")
            return
        try:
            dlg.exec()
        finally:
            self.refresh_cereri_badge()

    def _build_sync_status_panel(self):
        """Panou permanent în bara de status: starea sincronizării + buton rapid."""
        from PySide6.QtWidgets import QLabel, QPushButton, QWidget, QHBoxLayout

        self._lbl_sync = QLabel("☁ —")
        self._lbl_sync.setStyleSheet("color:#94a3b8;")
        btn = QPushButton("Sincronizează acum")
        btn.setToolTip("Trimite harta în portal și adu cererile online")
        btn.clicked.connect(self._sync_now)

        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(8)
        lay.addWidget(self._lbl_sync)
        lay.addWidget(btn)
        self.statusBar().addPermanentWidget(wrap)
        self._refresh_sync_status()

        self._sync_status_timer = QTimer(self)
        self._sync_status_timer.setInterval(30000)
        self._sync_status_timer.timeout.connect(self._refresh_sync_status)
        self._sync_status_timer.start()

    def _refresh_sync_status(self):
        lbl = getattr(self, "_lbl_sync", None)
        if lbl is None:
            return
        try:
            import cloud_sync
            cfg = cloud_sync.get_config()
            if not (cfg["url"] and cfg["key"]):
                lbl.setText("☁ Sincronizare neconfigurată")
                return
            pending = cloud_sync.pending_count()
            last = cfg["last"] or "niciodată"
            import entity_sync
            summary = entity_sync.status_summary()
            text = ("☁ ONLINE" if summary["online"] else "☁ OFFLINE (lucrezi local)") + f" · ultima sincronizare: {last}"
            if pending:
                text += f" · {pending} în coadă"
            if summary["conflicts"]:
                text += f" · {summary['conflicts']} CONFLICT(E) de rezolvat"
            if cfg.get("last_error"):
                text += f" · eroare: {cfg['last_error'][:60]}"
            if cfg["auto"]:
                text += " · automat"
            lbl.setText(text)
        except Exception:
            lbl.setText("☁ Stare sincronizare indisponibilă")

    def _sync_now(self):
        auto = getattr(self, "_auto_sync", None)
        if auto is None:
            self.open_cloud_sync_window()
            return
        try:
            import cloud_sync
            if not cloud_sync.is_configured():
                self.open_cloud_sync_window()
                return
        except Exception:
            self.open_cloud_sync_window()
            return
        self.statusBar().showMessage("☁ Sincronizare în curs…", 10000)
        auto.run_once(force=True)

    def _on_auto_sync_done(self, report: dict):
        """După fiecare sincronizare automată: reîmprospătează badge-ul și anunță în bara de status."""
        self.refresh_cereri_badge()
        noi = int(report.get("cereri_noi") or 0)
        if noi:
            msg = f"☁ Sincronizare: {noi} cerere/cereri online noi — vezi „Cereri online”."
        elif report.get("ok"):
            msg = "☁ Sincronizare cu serverul reușită."
        else:
            msg = "☁ Sincronizare amânată (fără conexiune) — se reia automat."
        try:
            self.statusBar().showMessage(msg, 15000)
        except Exception:
            pass
        if noi:
            try:
                self.signals.data_changed.emit()
            except Exception:
                pass
            try:
                from cereri_notifier import notify_new_cereri
                self._cerere_toast = notify_new_cereri(self, noi, self.open_cereri_online_window)
            except Exception:
                pass
        self._refresh_sync_status()

    def open_backup_window(self):
        """Copii de rezervă: setări, copie imediată, restaurare (necesită dreptul de configurare)."""
        import access_session
        if not access_session.require("manage_config", self, "copiile de rezervă"):
            return
        from backup_window import BackupWindow
        BackupWindow(self).exec()

    def open_cloud_sync_window(self):
        """Deschide fereastra de sincronizare cu serverul cloud."""
        import access_session
        if not access_session.require("manage_config", self, "configurarea sincronizării"):
            return
        try:
            from cloud_sync_window import CloudSyncWindow
        except Exception as e:
            show_message(self, "Eroare", f"Nu am putut deschide Sincronizare cloud: {e}", level="error")
            return
        dlg = CloudSyncWindow(self)
        dlg.synced.connect(self.refresh_cereri_badge)
        try:
            dlg.exec()
        finally:
            self.refresh_cereri_badge()

    def open_concesionari_window(self):
        try:
            from concesionari_window import ConcesionariWindow
        except Exception as e:
            show_message(self, "Eroare", f"Nu am putut deschide Concesionari: {e}", level="error")
            return

        try:
            dlg = ConcesionariWindow(self, actions=self.actions)
            if hasattr(dlg, "on_data_changed"):
                self.signals.data_changed.connect(dlg.on_data_changed)
        except Exception as e:
            show_message(self, "Eroare", f"Eroare la inițializare Concesionari: {e}", level="error")
            return

        try:
            dlg.exec()
        finally:
            try:
                if hasattr(dlg, "on_data_changed"):
                    self.signals.data_changed.disconnect(dlg.on_data_changed)
            except Exception:
                pass

    def open_contracte_window(self):
        try:
            from contracts_window import ContractsWindow
        except Exception as e:
            show_message(self, "Eroare", f"Nu am putut deschide Contracte: {e}", level="error")
            return

        try:
            dlg = ContractsWindow(self, actions=self.actions, selected_cod_loc=self.selected_cod_loc)
            if hasattr(dlg, "on_data_changed"):
                self.signals.data_changed.connect(dlg.on_data_changed)
        except Exception as e:
            show_message(self, "Eroare", f"Eroare la inițializare Contracte: {e}", level="error")
            return

        try:
            dlg.exec()
        finally:
            try:
                if hasattr(dlg, "on_data_changed"):
                    self.signals.data_changed.disconnect(dlg.on_data_changed)
            except Exception:
                pass

    def open_plati_window(self):
        from plati_window import PlatiWindow
        dlg = PlatiWindow(self)
        dlg.exec()

    def open_alerts_window(self):
        from alerts_window import AlertsWindow
        dlg = AlertsWindow(self)
        dlg.exec()

    def open_export_dialog(self):
        from PySide6.QtWidgets import QFileDialog, QInputDialog
        import reports_export as rex
        options = ["Locuri de veci", "Înhumați", "Contracte"]
        choice, ok = QInputDialog.getItem(
            self, "Export", "Ce vrei să exporți?", options, 0, False)
        if not ok:
            return
        default = {"Locuri de veci": "locuri.xlsx", "Înhumați": "inhumati.xlsx",
                   "Contracte": "contracte.xlsx"}[choice]
        path, _ = QFileDialog.getSaveFileName(
            self, "Salvează exportul", default, "Excel (*.xlsx);;CSV (*.csv)")
        if not path:
            return
        try:
            if choice == "Locuri de veci":
                out = rex.export_locuri(path)
            elif choice == "Înhumați":
                out = rex.export_inhumati(path)
            else:
                out = rex.export_contracte(path)
            QMessageBox.information(self, "Export", f"Export salvat:\n{out}")
        except Exception as e:
            QMessageBox.warning(self, "Export", f"Eroare la export: {e}")

    def import_locuri_dialog(self):
        from PySide6.QtWidgets import QFileDialog
        import reports_export as rex
        path, _ = QFileDialog.getOpenFileName(
            self, "Alege fișierul de import", "", "Excel/CSV (*.xlsx *.csv)")
        if not path:
            return
        updated, created, errors = rex.import_locuri(path)
        msg = f"Actualizate: {updated}\nCreate: {created}"
        if errors:
            msg += f"\nErori: {len(errors)}\n" + "\n".join(errors[:5])
        QMessageBox.information(self, "Import locuri", msg)
        try:
            fn = getattr(self.actions, "load_all_plots", None)
            if callable(fn):
                fn()
        except Exception:
            pass

    def backup_now(self):
        import reports_export as rex
        path = rex.backup_db()
        if path:
            QMessageBox.information(self, "Backup", f"Backup creat:\n{path}")
        else:
            QMessageBox.warning(self, "Backup", "Backup-ul a eșuat.")

    def export_map_dialog(self):
        from PySide6.QtWidgets import QFileDialog
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtCore import QRectF
        path, _ = QFileDialog.getSaveFileName(
            self, "Export hartă", "harta_cimitir.png", "PNG (*.png);;PDF (*.pdf)")
        if not path:
            return
        scene = self.map_view._scene
        rect = scene.itemsBoundingRect()
        if rect.isEmpty():
            rect = scene.sceneRect()
        try:
            if path.lower().endswith(".pdf"):
                from PySide6.QtGui import QPdfWriter
                writer = QPdfWriter(path)
                writer.setResolution(150)
                painter = QPainter(writer)
                scene.render(painter, source=rect)
                painter.end()
            else:
                w = max(1, min(int(rect.width()), 8000))
                h = max(1, min(int(rect.height()), 8000))
                img = QImage(w, h, QImage.Format_ARGB32)
                img.fill(Qt.white)
                painter = QPainter(img)
                scene.render(painter, target=QRectF(0, 0, w, h), source=rect)
                painter.end()
                img.save(path)
            QMessageBox.information(self, "Export hartă", f"Hartă salvată:\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "Export hartă", f"Eroare: {e}")

    def open_amenajari_window(self):
        self._open_small_window("Amenajări")

    def open_rapoarte_dialog(self):
        try:
            from rapoarte_dialog import RapoarteDialog
            dlg = RapoarteDialog(self)
            dlg.exec()
        except Exception as e:
            logger.exception("Eroare la deschiderea dialogului de rapoarte")
            QMessageBox.warning(self, "Rapoarte", f"Eroare: {e}")

    def open_qr_labels_dialog(self):
        """Deschide fereastra completă de management QR (nu dialog modal)."""
        try:
            from qr_management_widget import QrManagementWidget
            # o singură fereastră reutilizabilă
            if hasattr(self, "_qr_window") and self._qr_window is not None:
                try:
                    self._qr_window.reload()
                    self._qr_window.show()
                    self._qr_window.raise_()
                    self._qr_window.activateWindow()
                    return
                except Exception:
                    self._qr_window = None
            from PySide6.QtWidgets import QMainWindow
            self._qr_window = QMainWindow(self)
            self._qr_window.setWindowTitle("Management QR — Locuri de veci")
            self._qr_window.resize(1200, 720)
            widget = QrManagementWidget(self._qr_window,
                                        current_user=getattr(self, "_current_user", "admin"))
            self._qr_window.setCentralWidget(widget)
            self._qr_window.show()
        except Exception as e:
            logger.exception("Eroare la deschiderea ferestrei QR")
            QMessageBox.warning(self, "QR / Etichete", f"Eroare: {e}")

    def open_qr_custom_dialog(self):
        """Deschide fereastra „Generator QR Personalizat" cu date embedate în cod."""
        try:
            from qr_custom_dialog import QrCustomDialog
            if hasattr(self, "_qr_custom_window") and self._qr_custom_window is not None:
                try:
                    self._qr_custom_window.show()
                    self._qr_custom_window.raise_()
                    self._qr_custom_window.activateWindow()
                    return
                except Exception:
                    self._qr_custom_window = None
            from PySide6.QtWidgets import QMainWindow
            self._qr_custom_window = QMainWindow(self)
            self._qr_custom_window.setWindowTitle("Generator QR Personalizat — Date embedate în cod")
            self._qr_custom_window.resize(1240, 780)
            widget = QrCustomDialog(
                self._qr_custom_window,
                current_user=self.current_user.get("username", "admin"),
            )
            self._qr_custom_window.setCentralWidget(widget)
            self._qr_custom_window.show()
        except Exception as e:
            logger.exception("Eroare la deschiderea ferestrei QR Personalizat")
            QMessageBox.warning(self, "QR Personalizat", f"Eroare: {e}")

    def open_ai_settings(self):
        """Deschide ecranul „⚙️ Setări AI" (cheie Gemini, mod, model)."""
        try:
            from ai_scanare.ai_settings_dialog import AISettingsDialog
        except Exception as e:
            show_message(self, "Eroare", f"Modulul Setări AI nu se poate încărca: {e}", level="error")
            return
        try:
            dlg = AISettingsDialog(self)
            dlg.exec()
        except Exception as e:
            logger.exception("Eroare la deschiderea Setări AI")
            show_message(self, "Eroare", f"Eroare Setări AI: {e}", level="error")

    def open_ai_scanare_window(self):
        """Deschide fereastra AI Scanare Documente (Gemini 3 Flash)."""
        try:
            from ai_scanare.scan_window import AIScanareWindow
        except Exception as e:
            show_message(self, "Eroare", f"Modulul AI Scanare nu se poate încărca: {e}", level="error")
            return
        try:
            dlg = AIScanareWindow(self, actions=self.actions)
            try:
                if hasattr(dlg, "reload_history"):
                    # refresh la orice schimbare externă
                    self.signals.data_changed.connect(lambda *_: dlg.reload_history())
            except Exception:
                pass
            dlg.exec()
        except Exception as e:
            logger.exception("Eroare la deschiderea ferestrei AI Scanare")
            show_message(self, "Eroare", f"Eroare AI Scanare: {e}", level="error")


    def _open_small_window(self, title):
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setMinimumSize(500, 400)
        layout = QVBoxLayout()
        layout.addWidget(QLabel(f"Aici va fi modulul: {title}"))
        dlg.setLayout(layout)
        dlg.exec()

    # ---------------------------
    # editor hooks
    # ---------------------------
    def set_tool_mode(self, mode: str):
        try:
            self.tools.set_tool_mode(mode)
        except Exception:
            pass

    def exit_draw_mode(self, message: str = ""):
        self.tools.exit_draw_mode(message)

    def eventFilter(self, source, event):
        try:
            return self.events.eventFilter(source, event)
        except Exception:
            return False

    # ---------------------------
    # ștergere loc (din meniul contextual al poligonului)
    # ---------------------------
    def delete_plot(self, plot_item):
        data = plot_item.data(0)
        cod_loc = data.get("cod_loc") if isinstance(data, dict) else None
        if not cod_loc:
            return

        reply = QMessageBox.question(
            self, "Confirmare ștergere",
            f"Ești sigur că vrei să ștergi locul <b>{cod_loc}</b>?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        scene = getattr(self.map_view, "_scene", None)
        if scene is not None:
            try:
                scene.removeItem(plot_item)
            except Exception:
                pass
            # ștergem și eticheta text asociată
            for it in list(scene.items()):
                d = it.data(0)
                if isinstance(d, dict) and d.get("type") == "cod_loc_text" and d.get("cod_loc") == cod_loc:
                    try:
                        scene.removeItem(it)
                    except Exception:
                        pass
                    break

        try:
            self.actions.delete_plot_from_db(cod_loc)
            self.adv.refresh_area_labels()
            self.statusBar().showMessage(f"Loc {cod_loc} șters")
        except Exception:
            pass

    # ---------------------------
    # finalize polygon (loc) - importuri lazy si verificari
    # ---------------------------
    def finalize_polygon(self, points):
        try:
            self._finalize_polygon_impl(points)
        finally:
            self.exit_draw_mode()

    def _finalize_polygon_impl(self, points):
        if not points or len(points) < 3:
            return

        # 1. Dialog atribuire cod + toate câmpurile
        try:
            from assign_plot_code_window import AssignPlotCodeWindow
        except Exception:
            AssignPlotCodeWindow = None

        try:
            from grave_plot import GravePlotItem
        except Exception:
            GravePlotItem = None

        cod_loc = None
        if AssignPlotCodeWindow:
            dlg = AssignPlotCodeWindow(points, parent=self)
            if dlg.exec() != QDialog.Accepted:
                # userul a anulat — curățăm preview-ul și ieșim
                self.current_points = []
                try:
                    self.map_view.clear_preview()
                except Exception:
                    pass
                try:
                    self.statusBar().showMessage("Creare loc anulată")
                except Exception:
                    pass
                return
            try:
                cod_loc = dlg.txt_cod.text().strip()
            except Exception:
                cod_loc = None
        else:
            # fallback: prompt simplu
            try:
                from PySide6.QtWidgets import QInputDialog
                cod_loc, ok = QInputDialog.getText(self, "Cod loc", "Introduceți cod loc:")
                if not ok or not cod_loc:
                    return
            except Exception:
                return

        if not cod_loc:
            return

        # 2. Adaugă poligon vizual pe scenă (dialogul deja a salvat DB)
        poly = QPolygonF([QPointF(x, y) for x, y in points])
        data = {
            "type": "loc_de_veci",
            "cod_loc": cod_loc,
            "points": points,
            "plot_color": QColor(0, 120, 255, 80),
        }
        plot_item = None
        if GravePlotItem:
            try:
                plot_item = GravePlotItem(poly, data)
                plot_item.setZValue(10)
                if hasattr(self.map_view, "_scene") and self.map_view._scene is not None:
                    self.map_view._scene.addItem(plot_item)
                if hasattr(plot_item, "clicked"):
                    plot_item.clicked.connect(self.on_plot_clicked)
            except Exception:
                plot_item = None

        # 3. Etichetă text cu codul locului
        try:
            cx = sum(x for x, _ in points) / len(points)
            cy = sum(y for _, y in points) / len(points)
            text_item = QGraphicsSimpleTextItem(cod_loc)
            text_item.setFont(QFont("Arial", 11))
            text_item.setBrush(QColor(0, 0, 0))
            text_item.setZValue(20)
            br = text_item.boundingRect()
            text_item.setPos(cx - br.width() / 2, cy - br.height() / 2)
            text_item.setData(0, {"type": "cod_loc_text", "cod_loc": cod_loc})
            if hasattr(self.map_view, "_scene") and self.map_view._scene is not None:
                self.map_view._scene.addItem(text_item)
        except Exception:
            pass

        # 4. Întreabă dacă vrea să adauge înhumați acum
        try:
            reply = QMessageBox.question(
                self,
                "Adaugă înhumat",
                f"Locul <b>{cod_loc}</b> a fost creat cu succes.<br><br>"
                "Doriți să adăugați acum un înhumat pentru acest loc?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                try:
                    from add_inhumat_dialog import AddInhumatDialog
                    inh_dlg = AddInhumatDialog(self, cod_loc)
                    if inh_dlg.exec() == QDialog.Accepted:
                        data_inh = inh_dlg.get_data()
                        try:
                            saved_inh = self.actions.save_inhumat(cod_loc, data_inh)
                            import database as db
                            failed_docs = []
                            for _doc in inh_dlg.get_documents():
                                did = db.add_document("inhumat", saved_inh, _doc["doc_type"],
                                                      _doc["path"], nr_evidenta=_doc.get("nr_evidenta", ""))
                                if not did:
                                    failed_docs.append(_doc["path"])
                            if failed_docs:
                                show_message(self, "Documente nesalvate",
                                             "Aceste fișiere NU au putut fi salvate:\n\n"
                                             + "\n".join(failed_docs),
                                             level="warning")
                            self.statusBar().showMessage(f"Înhumat salvat pentru locul {cod_loc}")
                        except Exception:
                            show_message(self, "Eroare",
                                         "Nu am putut salva datele înhumatului.", level="warning")
                except Exception:
                    pass
        except Exception:
            pass

        # 5. Selectăm locul nou creat
        if plot_item:
            try:
                self.on_plot_clicked(plot_item)
            except Exception:
                pass

        self.current_points = []
        try:
            self.map_view.clear_preview()
        except Exception:
            pass
        try:
            self.adv.refresh_area_labels()
        except Exception:
            pass
        try:
            self.features.refresh_if_active()
        except Exception:
            pass
        try:
            self.statusBar().showMessage(f"Loc {cod_loc} creat cu succes")
        except Exception:
            pass

    # ---------------------------
    # finalize sector
    # ---------------------------
    def finalize_sector(self, points):
        try:
            self._finalize_sector_impl(points)
        finally:
            self.exit_draw_mode()

    def _finalize_sector_impl(self, points):
        if not points or len(points) < 3:
            return

        from grave_plot import GravePlotItem

        # id unic (nu se ciocnește cu sectoarele deja încărcate/create)
        idx = len(self.sectors) + 1
        sector_id = f"sector_{idx}"
        while sector_id in self.sectors:
            idx += 1
            sector_id = f"sector_{idx}"

        color = QColor(255, 0, 0, 100)
        poly = QPolygonF([QPointF(x, y) for x, y in points])
        data = {
            "type": "sector_grafic",
            "sector_id": sector_id,
            "points": points,
            "sector_color": color,
        }

        # item interactiv (selectabil + meniu contextual), la fel ca sectoarele încărcate
        sector_item = GravePlotItem(poly, data)

        if hasattr(self.map_view, "_scene") and self.map_view._scene is not None:
            try:
                self.map_view._scene.addItem(sector_item)
            except Exception:
                pass

        # registru unificat de sectoare
        self.sectors[sector_id] = {
            "id": sector_id,
            "name": f"Sector {idx}",
            "sector_color": color,
            "points": points,
            "item": sector_item,
        }
        # sectorul nou devine cel activ (toolbar culoare / opacitate / editare)
        self.active_sector_id = sector_id

        try:
            fn = getattr(self.actions, "save_sector_to_db", None)
            if callable(fn):
                fn(sector_id, points, color.name(QColor.HexArgb))
        except Exception:
            pass

        self.current_points = []
        try:
            self.map_view.clear_preview()
        except Exception:
            pass
        try:
            self.statusBar().showMessage(
                f"Sector {sector_id} creat — click dreapta pe sector pentru culoare / opacitate / editare"
            )
        except Exception:
            pass

    # ---------------------------
    # finalize row (rând de ghidare)
    # ---------------------------
    def finalize_row(self, points):
        try:
            self.adv.finalize_row(points)
        except Exception as e:
            show_message(self, "Eroare", f"Eroare la crearea rândului: {e}", level="error")

    # ---------------------------
    # sector edit (import lazy)
    # ---------------------------
    def start_sector_edit(self):
        sid = self.active_sector_id
        if not sid:
            try:
                self.statusBar().showMessage("Niciun sector selectat")
            except Exception:
                pass
            return

        sector_item = None
        try:
            if hasattr(self.map_view, "_scene") and self.map_view._scene is not None:
                for item in self.map_view._scene.items():
                    data = item.data(0)
                    if isinstance(data, dict) and data.get("type") == "sector_grafic" and data.get("sector_id") == sid:
                        sector_item = item
                        break
        except Exception:
            sector_item = None

        if sector_item is None:
            try:
                self.statusBar().showMessage("Sectorul nu are poligon desenat")
            except Exception:
                pass
            return

        try:
            from edit_sector_window import EditSectorWindow
        except Exception as e:
            show_message(self, "Eroare", f"Editor sector indisponibil: {e}", level="error")
            return

        sector_data = self.sectors.get(sid, {})
        try:
            dlg = EditSectorWindow(self, sector_item, sector_data)
            # NON-MODAL: folosim show() ca să putem muta punctele pe hartă
            dlg.show()
            self._active_editor = dlg
        except Exception as e:
            show_message(self, "Eroare", f"Eroare la deschiderea editorului de sector: {e}", level="error")

    # ---------------------------
    # opacity / preview
    # ---------------------------
    def update_sector_opacity(self, value: int):
        try:
            self.sector_mgr.set_sector_opacity(value)
            try:
                self.statusBar().showMessage(f"Opacitate sector: {value}/255")
            except Exception:
                pass
        except Exception:
            pass

    def toggle_preview(self):
        try:
            self.map_view.preview_enabled = self.act_preview.isChecked()
            if not self.map_view.preview_enabled:
                self.map_view.clear_preview()
                try:
                    self.statusBar().showMessage("Preview dezactivat")
                except Exception:
                    pass
            else:
                try:
                    self.statusBar().showMessage("Preview activat")
                except Exception:
                    pass
        except Exception:
            pass

    # ---------------------------
    # utilitare interne
    # ---------------------------
    def _safe_action_call(self, name: str, *args, **kwargs):
        try:
            fn = getattr(self.actions, name, None)
            if callable(fn):
                return fn(*args, **kwargs)
        except Exception:
            pass
        return None
