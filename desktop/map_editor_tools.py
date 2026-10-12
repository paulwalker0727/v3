# map_editor_tools.py
"""
EditorTools — gestionează interacțiunea cu harta în funcție de modul activ.

Interacțiuni suportate în modurile de desenare:
    - Click stânga        → adaugă punct
    - Mouse move          → preview + rubber-band + snap-to-close highlight
    - Click pe primul punct (dacă e snap-close) → finalizează poligonul
    - Dublu click        → finalizează poligonul (dacă ≥ 3 puncte)
    - Click dreapta       → finalizează poligonul (alternativă)
    - ESC (via keyboard)  → gestionat de EditorEvents/keyPressEvent
    - Backspace (via keyboard) → șterge ultimul punct

Info live (număr puncte, arie, perimetru) e emisă de map_view.drawing_info
și afișată în status bar de UI.
"""

from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QMessageBox


class EditorTools:
    # distanța (în pixeli, aprox) sub care un click pe primul punct
    # e considerat „snap to close"
    SNAP_PIXELS = 12

    def __init__(self, window):
        self.w = window

        # conectăm semnalul drawing_info la status bar (dacă există)
        try:
            self.w.map_view.drawing_info.connect(self._on_drawing_info)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # SETARE MOD UNEALTĂ
    # ------------------------------------------------------------------
    def set_tool_mode(self, mode: str):
        self.w.modes.set_tool_mode(mode)

        # resetare puncte + preview
        self.w.current_points = []
        self.w.map_view.clear_preview()
        self.w.map_view.set_preview_mode(mode)

        # butoane checkable
        try:
            self.w.act_select.setChecked(mode == "select")
            self.w.act_draw.setChecked(mode == "draw")
            self.w.act_delete.setChecked(mode == "delete")
            self.w.act_draw_sector.setChecked(mode == "draw_sector")
            self.w.act_delete_sector.setChecked(mode == "delete_sector")
        except Exception:
            pass
        try:
            self.w.act_pan.setChecked(mode == "select")
        except Exception:
            pass

        # cursor
        if mode in ("draw", "draw_sector"):
            cursor = Qt.CrossCursor
        elif mode in ("delete", "delete_sector"):
            cursor = Qt.PointingHandCursor
        else:
            cursor = Qt.ArrowCursor
        self.w.map_view.setCursor(QCursor(cursor))

        # pan doar în select
        self.w.map_view.set_pan_mode(mode == "select")

        # mesaj în status bar
        messages = {
            "select": "🖱  Selectare / Pan hartă",
            "draw": "✏  Desenează LOC — click pentru puncte, dublu-click sau click pe primul punct pentru finalizare. Backspace = șterge ultimul punct, ESC = anulare.",
            "delete": "🗑  Șterge LOC — click pe un loc pentru a-l șterge",
            "draw_sector": "🏷  Desenează SECTOR — click pentru puncte, dublu-click pentru finalizare",
            "delete_sector": "🗑  Șterge SECTOR — click pe un sector pentru a-l șterge",
        }
        try:
            self.w.statusBar().showMessage(messages.get(mode, ""))
        except Exception:
            pass

    def exit_draw_mode(self, message: str = ""):
        """Ieșire completă din desenare → unealta activă = select (mână)."""
        self.w.current_points = []
        try:
            self.w.map_view.clear_preview()
        except Exception:
            pass
        adv = getattr(self.w, "adv", None)
        if adv is not None:
            try:
                adv._clear_temp_line()
            except Exception:
                pass
            if hasattr(adv, "_pending_row_order"):
                adv._pending_row_order = None
        self.w.map_view._middle_panning = False
        self.set_tool_mode("select")
        if message:
            try:
                self.w.statusBar().showMessage(message)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # LIVE INFO IN STATUS BAR
    # ------------------------------------------------------------------
    def _on_drawing_info(self, n_points: int, area: float, perim: float,
                         snap_close: bool, last_seg: float = 0.0):
        """Slot conectat la map_view.drawing_info — afișează info live în status bar.
        Convertește în metri/m² dacă scara e calibrată (sincron cu unealta de scală)."""
        if not n_points:
            return
        mode = self.w.modes.current_mode
        if mode not in ("draw", "draw_sector", "draw_row"):
            return
        prefix = {"draw_sector": "SECTOR", "draw_row": "RÂND"}.get(mode, "LOC")

        ppm = None
        adv = getattr(self.w, "adv", None)
        if adv is not None:
            try:
                ppm = adv.get_ppm()
            except Exception:
                ppm = None

        if ppm:
            seg = last_seg / ppm
            per = perim / ppm
            ar = area / (ppm * ppm)
            ul, ua = "m", "m²"
        else:
            seg, per, ar = last_seg, perim, area
            ul, ua = "px", "px²"

        hint = "  ✅ CLICK pentru a închide poligonul" if snap_close else ""
        seg_txt = f"latura curentă ≈ {seg:.2f} {ul}  |  " if last_seg else ""
        try:
            self.w.statusBar().showMessage(
                f"✏  Desen {prefix}: {n_points} puncte  |  {seg_txt}"
                f"perimetru ≈ {per:.2f} {ul}  |  arie ≈ {ar:.2f} {ua}{hint}"
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # PROCESARE EVENIMENTE (apelat din EditorEvents)
    # ------------------------------------------------------------------
    def handle_event(self, event, pos):
        mode = self.w.modes.current_mode

        # desenare loc / sector / rând
        if mode in ("draw", "draw_sector", "draw_row"):
            return self._handle_draw(event, pos, mode)

        # moduri avansate (calibrare / ghidaje / riglă) → AdvancedEditor
        if mode in ("calibrate", "draw_guide_h", "draw_guide_v", "draw_guide_line", "ruler"):
            adv = getattr(self.w, "adv", None)
            if adv is not None:
                return adv.handle_event(event, pos)
            return False

        # ștergere loc
        if mode == "delete" and event.type() == QEvent.MouseButtonPress:
            return self._handle_delete(pos)

        # ștergere sector
        if mode == "delete_sector" and event.type() == QEvent.MouseButtonPress:
            return self._handle_delete_sector(pos)

        return False

    # ------------------------------------------------------------------
    # DESENARE POLIGON / SECTOR
    # ------------------------------------------------------------------
    def _clicked_first_point(self, pos) -> bool:
        """True dacă `pos` e în raza de snap față de primul vertex."""
        if not self.w.current_points:
            return False
        x0, y0 = self.w.current_points[0]
        # convertim SNAP_PIXELS din pixeli-ecran în unități scenă via scale factor
        scale = self.w.map_view.transform().m11() or 1.0
        snap_units = self.SNAP_PIXELS / max(abs(scale), 0.001)
        return ((pos.x() - x0) ** 2 + (pos.y() - y0) ** 2) ** 0.5 < snap_units

    def _finalize(self, mode: str):
        min_pts = 2 if mode == "draw_row" else 3
        if len(self.w.current_points) < min_pts:
            return
        pts = list(self.w.current_points)
        self.w.current_points = []
        self.w.map_view.clear_preview()
        if mode == "draw":
            self.w.finalize_polygon(pts)
        elif mode == "draw_sector":
            self.w.finalize_sector(pts)
        elif mode == "draw_row":
            self.w.finalize_row(pts)

    def _snap(self, pos):
        """Aplică snap (Shift/Ctrl) la poziția click-ului, dacă e disponibil."""
        adv = getattr(self.w, "adv", None)
        if adv is not None:
            try:
                return adv.snap_qpoint(pos)
            except Exception:
                return pos
        return pos

    def _handle_draw(self, event, pos, mode: str):
        # ---- click stânga → adaugă punct sau finalizează dacă e pe primul ----
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            # click pe primul vertex (snap-close) → finalizează
            if len(self.w.current_points) >= 3 and self._clicked_first_point(pos):
                self._finalize(mode)
                return True
            pos = self._snap(pos)
            self.w.current_points.append((pos.x(), pos.y()))
            self.w.map_view.update_preview(self.w.current_points, pos)
            return True

        # ---- click dreapta → finalizează (alternativă la dublu-click) ----
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.RightButton:
            min_pts = 2 if mode == "draw_row" else 3
            if len(self.w.current_points) >= min_pts:
                self._finalize(mode)
                return True
            # dacă are prea puține puncte, cancel
            self.w.current_points = []
            self.w.map_view.clear_preview()
            try:
                self.w.statusBar().showMessage("Desen anulat (prea puține puncte)")
            except Exception:
                pass
            return True

        # ---- mișcare mouse → update preview cu rubber-band ----
        if event.type() == QEvent.MouseMove:
            if self.w.current_points:
                self.w.map_view.update_preview(self.w.current_points, pos)
            return True

        # ---- dublu click → finalizează ----
        if event.type() == QEvent.MouseButtonDblClick:
            self._finalize(mode)
            return True

        return False

    # ------------------------------------------------------------------
    # KEYBOARD (apelat din EditorEvents)
    # ------------------------------------------------------------------
    def handle_key(self, event) -> bool:
        """Returnează True dacă tasta a fost consumată."""
        mode = self.w.modes.current_mode
        if mode not in ("draw", "draw_sector", "draw_row"):
            return False

        key = event.key()

        # ESC → anulează desenul curent
        if key == Qt.Key_Escape:
            self.exit_draw_mode("Desen anulat (ESC)")
            return True

        # BACKSPACE → șterge ultimul punct
        if key == Qt.Key_Backspace:
            if self.w.current_points:
                self.w.current_points.pop()
                if self.w.current_points:
                    self.w.map_view.update_preview(self.w.current_points)
                else:
                    self.w.map_view.clear_preview()
                try:
                    n = len(self.w.current_points)
                    self.w.statusBar().showMessage(f"Ultimul punct șters. Rămân {n} puncte.")
                except Exception:
                    pass
            return True

        # ENTER → finalizează
        if key in (Qt.Key_Return, Qt.Key_Enter):
            min_pts = 2 if mode == "draw_row" else 3
            if len(self.w.current_points) >= min_pts:
                self._finalize(mode)
                return True
            try:
                self.w.statusBar().showMessage(f"Ai nevoie de cel puțin {min_pts} puncte pentru a finaliza.")
            except Exception:
                pass
            return True

        return False

    # ------------------------------------------------------------------
    # ȘTERGERE LOC (poligon + text + DB)
    # ------------------------------------------------------------------
    def _handle_delete(self, pos):
        items = self.w.map_view._scene.items(pos)

        target_cod = None
        target_polygon = None
        target_text = None

        for it in items:
            data = it.data(0)
            if not isinstance(data, dict):
                continue
            if data.get("type") == "loc_de_veci":
                target_cod = data.get("cod_loc")
                target_polygon = it
            if data.get("type") == "cod_loc_text":
                target_cod = data.get("cod_loc")
                target_text = it

        if not target_cod:
            return False

        msg = QMessageBox(self.w)
        msg.setWindowTitle("Confirmare ștergere loc")
        msg.setText(f"Ești sigur că vrei să ștergi locul <b>{target_cod}</b>?")
        msg.setIcon(QMessageBox.Warning)
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.No)
        if msg.exec() != QMessageBox.Yes:
            return True

        if target_polygon:
            self.w.map_view._scene.removeItem(target_polygon)
        if target_text:
            self.w.map_view._scene.removeItem(target_text)
        else:
            for item in self.w.map_view._scene.items():
                data = item.data(0)
                if isinstance(data, dict) and data.get("type") == "cod_loc_text" and data.get("cod_loc") == target_cod:
                    self.w.map_view._scene.removeItem(item)
                    break

        self.w.actions.delete_plot_from_db(target_cod)
        try:
            self.w.statusBar().showMessage(f"Loc {target_cod} șters complet")
        except Exception:
            pass
        return True

    # ------------------------------------------------------------------
    # ȘTERGERE SECTOR
    # ------------------------------------------------------------------
    def _handle_delete_sector(self, pos):
        items = self.w.map_view._scene.items(pos)
        for it in items:
            data = it.data(0)
            if isinstance(data, dict) and data.get("type") == "sector_grafic":
                sector_id = data.get("sector_id")
                msg = QMessageBox(self.w)
                msg.setWindowTitle("Confirmare ștergere sector")
                msg.setText(f"Ești sigur că vrei să ștergi sectorul <b>{sector_id}</b>?")
                msg.setIcon(QMessageBox.Warning)
                msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
                msg.setDefaultButton(QMessageBox.No)
                if msg.exec() != QMessageBox.Yes:
                    return True

                self.w.map_view._scene.removeItem(it)
                self.w.actions.delete_sector_from_db(sector_id)
                try:
                    self.w.statusBar().showMessage(f"Sector {sector_id} șters")
                except Exception:
                    pass
                return True
        return False

    # ------------------------------------------------------------------
    # ȘTERGERE DIRECTĂ DUPĂ COD (folosită de EditorEvents)
    # ------------------------------------------------------------------
    def delete_plot_by_cod(self, cod_loc):
        scene = self.w.map_view._scene
        for item in scene.items():
            data = item.data(0)
            if isinstance(data, dict) and data.get("type") == "loc_de_veci" and data.get("cod_loc") == cod_loc:
                scene.removeItem(item)
                break
        for item in scene.items():
            data = item.data(0)
            if isinstance(data, dict) and data.get("type") == "cod_loc_text" and data.get("cod_loc") == cod_loc:
                scene.removeItem(item)
                break
        self.w.actions.delete_plot_from_db(cod_loc)
