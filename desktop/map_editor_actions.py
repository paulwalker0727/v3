# map_editor_actions.py
"""
EditorActions — clasă „façade" folosită de UI (map_view, panels, etc.).
Delegă TOATE operațiile DB către modulul `database.py` (single source of truth),
și emite semnalul global `data_changed` după fiecare mutare, ca UI-urile
deschise să se poată actualiza automat.
"""

import json
import os
from typing import List, Dict, Any, Optional

from PySide6.QtWidgets import QFileDialog, QGraphicsSimpleTextItem
from PySide6.QtGui import QPixmap, QColor, QFont, QPolygonF
from PySide6.QtCore import QPointF

import database as db


class EditorActions:
    def __init__(self, window):
        self.w = window

    # -------------------------------------------------------------------------
    # Semnal global „data_changed"
    # -------------------------------------------------------------------------
    def _emit_update(self, entity: str, key: str):
        try:
            if hasattr(self.w, "signals") and hasattr(self.w.signals, "data_changed"):
                self.w.signals.data_changed.emit(entity, str(key))
        except Exception:
            pass

    # =========================================================================
    # HARTĂ
    # =========================================================================
    def load_map(self):
        path, _ = QFileDialog.getOpenFileName(
            self.w, "Alege hartă", "", "Imagini (*.png *.jpg *.jpeg *.svg)"
        )
        if not path:
            return
        try:
            if hasattr(self.w, "map_view") and callable(getattr(self.w.map_view, "load_map_file", None)):
                self.w.map_view.load_map_file(path)
        except Exception:
            pass
        try:
            db.set_setting("map_path", path)
        except Exception:
            pass

    def load_saved_map(self):
        try:
            path = db.get_setting("map_path")
        except Exception:
            path = None
        if path and hasattr(self.w, "map_view") and callable(getattr(self.w.map_view, "load_map_file", None)):
            try:
                self.w.map_view.load_map_file(path)
            except Exception:
                pass

    def save_map(self):
        path, _ = QFileDialog.getSaveFileName(self.w, "Salvează hartă", "", "PNG (*.png)")
        if not path:
            return
        try:
            pix: QPixmap = self.w.map_view.grab()
            pix.save(path)
        except Exception:
            pass

    # =========================================================================
    # LOCURI - GEOMETRIE
    # =========================================================================
    def save_plot_to_db(self, cod_loc: str, points: List[tuple], color: str) -> Optional[int]:
        result = db.save_plot_to_db(cod_loc, points, color)
        # calcul AUTOMAT al suprafeței (în m² dacă scara e calibrată, altfel px²)
        try:
            import geometry_utils as geo
            ppm = None
            v = db.get_setting("px_per_meter")
            if v:
                ppm = float(v)
            area = geo.area_m2(points, ppm) if ppm else geo.polygon_area(points)
            if area is not None:
                db.save_loc_metadata(cod_loc, {"suprafata": round(area, 2)})
        except Exception:
            pass
        self._emit_update("loc", cod_loc)
        return result

    def load_all_plots(self):
        """Încarcă poligoanele locurilor pe scenă (map_view)."""
        try:
            from grave_plot import GravePlotItem
        except Exception:
            return

        conn = db.get_connection()
        try:
            rows = conn.execute("SELECT cod_loc, points, color FROM locuri_de_veci").fetchall()
        finally:
            conn.close()

        for row in rows:
            cod_loc = row["cod_loc"]
            try:
                points = json.loads(row["points"]) if row["points"] else []
            except Exception:
                points = []
            if not points:
                continue
            color = row["color"] or "#0078FF"
            try:
                poly = QPolygonF([QPointF(x, y) for x, y in points])
            except Exception:
                continue
            data = {"type": "loc_de_veci", "cod_loc": cod_loc, "points": points,
                    "plot_color": QColor(color)}
            item = GravePlotItem(poly, data)
            item.setZValue(10)
            try:
                if hasattr(self.w, "map_view") and hasattr(self.w.map_view, "_scene"):
                    self.w.map_view._scene.addItem(item)
                if hasattr(item, "clicked") and hasattr(self.w, "on_plot_clicked"):
                    item.clicked.connect(self.w.on_plot_clicked)
            except Exception:
                pass
            # etichetă text cu codul locului
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
                if hasattr(self.w, "map_view") and hasattr(self.w.map_view, "_scene"):
                    self.w.map_view._scene.addItem(text_item)
            except Exception:
                pass

    def delete_plot_from_db(self, cod_loc: str) -> bool:
        ok = db.delete_plot(cod_loc)
        self._emit_update("loc", cod_loc)
        return ok

    # =========================================================================
    # METADATA LOCURI
    # =========================================================================
    def save_loc_metadata(self, cod_loc: str, metadata: Dict[str, Any]) -> bool:
        ok = db.save_loc_metadata(cod_loc, metadata)
        self._emit_update("loc", cod_loc)
        return ok

    def load_loc_by_cod(self, cod_loc: str) -> Optional[Dict[str, Any]]:
        return db.load_loc_by_cod(cod_loc)

    def list_locuri(self) -> List[Dict[str, Any]]:
        return db.list_locuri()

    def search_locuri(self, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        return db.search_locuri(filters)

    # =========================================================================
    # EXTRA DATA (JSON per loc)
    # =========================================================================
    def load_extra_data(self, cod_loc: str) -> Dict[str, Any]:
        # merge extra din locuri_de_veci.extra_data + loc_extra.extra_json
        merged: Dict[str, Any] = {}
        loc = db.load_loc_by_cod(cod_loc)
        if loc and isinstance(loc.get("extra_data"), dict):
            merged.update(loc["extra_data"])
        try:
            fallback = db.load_extra_data(cod_loc)
            if isinstance(fallback, dict):
                merged.update(fallback)
        except Exception:
            pass
        return merged

    def save_extra_data(self, cod_loc: str, data_dict: Dict[str, Any]) -> bool:
        # persistăm ambele surse pentru compat
        existing = self.load_extra_data(cod_loc)
        existing.update(data_dict or {})
        ok1 = db.save_loc_metadata(cod_loc, {"extra_data": existing})
        ok2 = db.save_extra_data(cod_loc, existing)
        self._emit_update("loc_extra", cod_loc)
        return bool(ok1 or ok2)

    # =========================================================================
    # INHUMAȚI  (funcțiile lipsă înainte!)
    # =========================================================================
    def save_inhumat(self, cod_loc: str, data: Dict[str, Any],
                     inhumat_id: Optional[int] = None) -> Optional[int]:
        saved_id = db.save_inhumat(cod_loc, data, inhumat_id)
        if saved_id is not None:
            self._emit_update("inhumat", cod_loc)
        return saved_id

    def load_inhumati_for_loc(self, cod_loc: str) -> List[Dict[str, Any]]:
        return db.load_inhumati_for_loc(cod_loc)

    def load_all_inhumati(self, filters: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
        return db.load_all_inhumati(filters)

    def get_inhumat(self, inhumat_id: int) -> Optional[Dict[str, Any]]:
        return db.get_inhumat(inhumat_id)

    def delete_inhumat(self, inhumat_id: int) -> bool:
        # aflăm cod_loc înainte de ștergere pentru semnalul de refresh
        inh = db.get_inhumat(inhumat_id)
        cod_loc = inh.get("cod_loc", "") if inh else ""
        ok = db.delete_inhumat(inhumat_id)
        if ok:
            self._emit_update("inhumat", cod_loc or str(inhumat_id))
        return ok

    # =========================================================================
    # CONCESIONARI
    # =========================================================================
    def save_concesionar(self, data: Dict[str, Any],
                         concesionar_id: Optional[int] = None) -> Optional[int]:
        saved_id = db.save_concesionar(data, concesionar_id)
        if saved_id is not None:
            self._emit_update("concesionar", str(saved_id))
        return saved_id

    def load_concesionari(self, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        return db.load_concesionari(filters)

    def get_concesionar(self, concesionar_id: int) -> Optional[Dict[str, Any]]:
        return db.get_concesionar(concesionar_id)

    def delete_concesionar(self, concesionar_id: int) -> bool:
        ok = db.delete_concesionar(concesionar_id)
        if ok:
            self._emit_update("concesionar", str(concesionar_id))
        return ok

    # =========================================================================
    # SECTOARE
    # =========================================================================
    def save_sector_to_db(self, sector_id: str, points: List[tuple], color: str) -> bool:
        ok = db.save_sector_to_db(sector_id, points, color)
        self._emit_update("sector", sector_id)
        return ok

    def load_all_sectors(self):
        """Adaugă sectoarele pe scenă."""
        try:
            from grave_plot import GravePlotItem
        except Exception:
            return
        try:
            sectors = db.load_all_sectors()
        except Exception:
            return
        for s in sectors:
            points = s.get("points") or []
            if not points:
                continue
            try:
                poly = QPolygonF([QPointF(x, y) for x, y in points])
            except Exception:
                continue
            data = {"type": "sector_grafic", "sector_id": s["id"],
                    "points": points, "sector_color": QColor(s.get("color") or "#FF0000")}
            item = GravePlotItem(poly, data)
            try:
                if hasattr(self.w, "map_view") and hasattr(self.w.map_view, "_scene"):
                    self.w.map_view._scene.addItem(item)
                # înregistrăm în registrul de sectoare al ferestrei (pentru editare)
                if hasattr(self.w, "sectors"):
                    self.w.sectors[s["id"]] = {
                        "id": s["id"],
                        "name": s["id"],
                        "sector_color": QColor(s.get("color") or "#FF0000"),
                        "points": points,
                        "item": item,
                    }
            except Exception:
                pass

    def delete_sector_from_db(self, sector_id: str) -> bool:
        ok = db.delete_sector_from_db(sector_id)
        self._emit_update("sector", sector_id)
        return ok

    # =========================================================================
    # CONTRACTE
    # =========================================================================
    def load_contracte(self, extra_where: str = "") -> List[Dict[str, Any]]:
        return db.load_contracte(extra_where)

    def get_contract(self, contract_id: int) -> Optional[Dict[str, Any]]:
        return db.get_contract(contract_id)

    def save_contract(self, data: Dict[str, Any],
                      contract_id: Optional[int] = None) -> Optional[int]:
        saved_id = db.save_contract(data, contract_id)
        if saved_id is not None:
            self._emit_update("contract", str(saved_id))
        return saved_id

    def delete_contract(self, contract_id: int) -> bool:
        ok = db.delete_contract(contract_id)
        if ok:
            self._emit_update("contract", str(contract_id))
        return ok

    def generate_contract_document(self, contract_id: int,
                                   template_path: str = "templates/contract_template.docx",
                                   out_dir: str = "contracts",
                                   out_path: Optional[str] = None) -> Optional[str]:
        """
        Generează documentul .docx pentru contractul cu id-ul dat.
        - `out_path` — dacă e furnizat, salvează exact acolo.
        - `out_dir` — altfel, salvează în directorul dat cu nume auto-generat.
        Returnează calea absolută a fișierului sau None la eroare.
        """
        from contract_generator import generate_contract, build_default_filename

        contract = db.get_contract(contract_id)
        if not contract:
            return None

        if out_path is None:
            filename = build_default_filename(contract)
            out_path = os.path.join(out_dir, filename)

        result = generate_contract(contract_id, template_path, out_path)
        if result is None:
            return None
        return result.get("path")

    # =========================================================================
    # PHOTOS
    # =========================================================================
    def list_photos_for_loc(self, cod_loc: str) -> List[Dict[str, Any]]:
        return db.list_photos_for_loc(cod_loc)

    def save_photo(self, cod_loc: str, file_path: str,
                   metadata: Optional[Dict[str, Any]] = None) -> Optional[int]:
        pid = db.save_photo(cod_loc, file_path, metadata)
        if pid is not None:
            self._emit_update("photo", cod_loc)
        return pid

    def delete_photo(self, photo_id: int, cod_loc: str = "") -> bool:
        ok = db.delete_photo(photo_id)
        if ok:
            self._emit_update("photo", cod_loc or str(photo_id))
        return ok

    def set_cover_photo(self, cod_loc: str, photo_id: int) -> bool:
        ok = db.set_cover_photo(cod_loc, photo_id)
        if ok:
            self._emit_update("photo", cod_loc)
        return ok

    # =========================================================================
    # AVIZE / AMENAJARI
    # =========================================================================
    def list_avize_for_loc(self, cod_loc: str) -> List[Dict[str, Any]]:
        return db.list_avize_for_loc(cod_loc)

    def add_aviz(self, cod_loc: str, nr: str, data_aviz: Optional[str]) -> Optional[int]:
        aid = db.add_aviz(cod_loc, nr, data_aviz)
        if aid is not None:
            self._emit_update("aviz", cod_loc)
        return aid

    def delete_aviz(self, aviz_id: int, cod_loc: str = "") -> bool:
        ok = db.delete_aviz(aviz_id)
        if ok:
            self._emit_update("aviz", cod_loc or str(aviz_id))
        return ok
