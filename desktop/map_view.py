# map_view.py
"""
CemeteryMapView — QGraphicsView pentru harta cimitirului.

Îmbunătățiri profesionale la desenare:
    - Vertecși vizibili (cercuri) pentru fiecare click
    - Rubber-band (linie punctată) de la ultimul vertex la cursor
    - Highlight „snap-to-close" când cursorul e aproape de primul punct
    - Semnal cu info live: număr puncte, arie, perimetru
    - Zoom la cursor (pivot pe poziția mouse-ului)
    - Fitness auto la încărcare hartă
"""

from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, QPointF, QRectF, Signal, QTimer
from PySide6.QtGui import (
    QPolygonF, QBrush, QPen, QColor, QPixmap, QPainter,
)
from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsPolygonItem, QGraphicsPixmapItem,
    QGraphicsEllipseItem, QGraphicsLineItem,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _polygon_area_perimeter(points: List[Tuple[float, float]]) -> Tuple[float, float]:
    """Returnează (area, perimeter) pentru un poligon. area>=0."""
    n = len(points)
    if n < 2:
        return 0.0, 0.0
    perim = 0.0
    for i in range(n - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        perim += ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    if n < 3:
        return 0.0, perim
    # aria (shoelace)
    s = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += (x1 * y2) - (x2 * y1)
    return abs(s) / 2.0, perim


class CemeteryMapView(QGraphicsView):
    # emis la fiecare update_preview cu (n_points, area, perimeter, snap_close, last_seg)
    drawing_info = Signal(int, float, float, bool, float)

    # constante vizuale
    VERTEX_RADIUS = 5           # rază vertex (unități scenă)
    SNAP_THRESHOLD = 12         # distanță maximă la primul punct pentru snap-to-close
    PREVIEW_FILL = QColor(0, 120, 255, 60)
    PREVIEW_BORDER = QColor(0, 120, 255)
    PREVIEW_RUBBER = QColor(0, 120, 255, 180)
    VERTEX_COLOR = QColor(0, 120, 255)
    VERTEX_FIRST_HIGHLIGHT = QColor(255, 140, 0)  # portocaliu pentru snap-close
    SECTOR_PREVIEW_FILL = QColor(255, 0, 0, 60)
    SECTOR_PREVIEW_BORDER = QColor(200, 0, 0)
    ROW_PREVIEW_FILL = QColor(34, 197, 94, 25)
    ROW_PREVIEW_BORDER = QColor(21, 128, 61)

    def __init__(self):
        super().__init__()

        # scenă
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        # aspecte
        self.setDragMode(QGraphicsView.NoDrag)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setBackgroundBrush(QBrush(QColor(245, 247, 250)))

        # zoom
        self._zoom_factor = 1.15
        self._min_scale = 0.01
        self._max_scale = 30.0

        # imaginea hărții
        self._map_item: Optional[QGraphicsPixmapItem] = None

        # fit programat: True cat timp asteptam ca widget-ul sa aiba dimensiune valida
        self._pending_fit = False
        self._did_initial_fit = False
        # dimensiune minima acceptabila a viewport-ului pt un fit corect;
        # sub aceasta, amanam fit-ul (evitam scale ultra-mic care blocheaza zoom).
        self._min_viewport_for_fit = 200

        # preview state
        self.preview_enabled = True
        self.preview_item: Optional[QGraphicsPolygonItem] = None
        self._rubber_line: Optional[QGraphicsLineItem] = None
        self._vertex_items: List[QGraphicsEllipseItem] = []
        self._mode: str = "loc"  # "loc" sau "sector" (culoare diferită)

    # =========================================================================
    # ÎNCĂRCARE HARTĂ
    # =========================================================================
    def load_map_file(self, path: str):
        # Ștergem harta veche
        if self._map_item is not None:
            self._scene.removeItem(self._map_item)
            self._map_item = None

        pix = QPixmap(path)
        if pix.isNull():
            print(f"[map_view] Nu am putut încărca harta: {path}")
            return

        self._map_item = QGraphicsPixmapItem(pix)
        self._map_item.setZValue(-2000)
        self._map_item.setData(0, {"type": "map_image"})
        self._map_item.setTransformationMode(Qt.SmoothTransformation)
        self._scene.addItem(self._map_item)

        self._scene.setSceneRect(QRectF(pix.rect()))
        # Ne asiguram ca fit-ul se aplica ABIA dupa ce widget-ul are dimensiune reala.
        # La startup, widget-ul poate avea inca 0x0 -> fitInView produce zoom neverosimil (patratel mic).
        self._pending_fit = True
        self._did_initial_fit = False
        # incercam sincron (daca dimensiunea e deja valida)
        self._try_fit_content()
        # ...si asincron (dupa ce event loop-ul a livrat resize-ul).
        # Facem multiple retry-uri; ultimul e „forteaza fit indiferent de marime"
        # ca sa acoperim si scenariile in care QSplitter intarzie foarte mult layout-ul.
        QTimer.singleShot(0, self._try_fit_content)
        QTimer.singleShot(50, self._try_fit_content)
        QTimer.singleShot(200, self._try_fit_content)
        QTimer.singleShot(500, self._try_fit_content)
        QTimer.singleShot(1000, lambda: self._try_fit_content(force=True))

    def _target_rect(self) -> QRectF:
        """Zona de fit: harta daca exista, altfel bounding rect-ul tuturor itemelor."""
        if self._map_item is not None:
            return self._map_item.boundingRect()
        r = self._scene.itemsBoundingRect()
        if r.isEmpty() or r.width() < 1 or r.height() < 1:
            return self._scene.sceneRect()
        return r

    def _try_fit_content(self, force: bool = False):
        """Aplica fitInView doar daca widget-ul are dimensiune valida.

        `force=True` incearca fit-ul chiar si daca viewport-ul e sub pragul minim
        (ultimul retry, ca sa nu ramanem niciodata cu harta „patratel mic").
        """
        if not self._pending_fit:
            return
        vp = self.viewport()
        if vp is None:
            return
        # sub pragul minim, refuzam fit-ul (ar produce un scale ultra-mic
        # care blocheaza wheelEvent-ul). Ultimul retry are force=True.
        min_side = self._min_viewport_for_fit
        if not force and (vp.width() < min_side or vp.height() < min_side):
            return
        # protectie totala impotriva viewport-ului degenerat (0x0)
        if vp.width() < 20 or vp.height() < 20:
            return
        target = self._target_rect()
        if target.width() < 1 or target.height() < 1:
            return
        self.fitInView(target, Qt.KeepAspectRatio)
        # daca in urma fit-ului am ajuns sub min_scale (mapa foarte mare / viewport mic),
        # ridicam scale-ul la min_scale ca sa NU blocam zoom-ul ulterior.
        s = self.transform().m11()
        if s > 0 and s < self._min_scale:
            factor = self._min_scale / s
            self.scale(factor, factor)
        self._pending_fit = False
        self._did_initial_fit = True

    def fit_to_content(self):
        """Fit manual (pentru buton 'Fit to view')."""
        self._pending_fit = True
        # buton manual = utilizatorul vede fereastra, deci forteaza fit-ul.
        self._try_fit_content(force=True)

    def showEvent(self, event):
        """La prima afisare, aplicam fit-ul daca e pending (widget devine vizibil)."""
        super().showEvent(event)
        if self._pending_fit or not self._did_initial_fit:
            # dam o mica intarziere ca Qt sa aloce dimensiunea finala.
            # Facem mai multe retry-uri; ultimul e „force" ca sa acoperim si cazuri
            # in care QSplitter intarzie foarte mult layout-ul (harta „patratel mic").
            QTimer.singleShot(0, self._try_fit_content)
            QTimer.singleShot(150, self._try_fit_content)
            QTimer.singleShot(400, self._try_fit_content)
            QTimer.singleShot(800, lambda: self._try_fit_content(force=True))

    def resizeEvent(self, event):
        """La primul resize semnificativ, aplicam fit-ul daca e inca in asteptare."""
        super().resizeEvent(event)
        if self._pending_fit:
            QTimer.singleShot(0, self._try_fit_content)

    # =========================================================================
    # ADAUGĂ POLIGON (loc)
    # =========================================================================
    def add_plot(self, points, data):
        from grave_plot import GravePlotItem
        polygon = QPolygonF([QPointF(x, y) for x, y in points])
        item = GravePlotItem(polygon, data)
        item.setZValue(10)
        self._scene.addItem(item)
        return item

    # =========================================================================
    # PREVIEW POLIGON
    # =========================================================================
    def set_preview_mode(self, mode: str):
        """mode: 'loc' / 'sector' / 'row' — schimbă culoarea preview-ului."""
        if mode in ("draw_sector", "sector"):
            self._mode = "sector"
        elif mode in ("draw_row", "row"):
            self._mode = "row"
        else:
            self._mode = "loc"

    def _colors(self):
        if self._mode == "sector":
            return self.SECTOR_PREVIEW_FILL, self.SECTOR_PREVIEW_BORDER
        if self._mode == "row":
            return self.ROW_PREVIEW_FILL, self.ROW_PREVIEW_BORDER
        return self.PREVIEW_FILL, self.PREVIEW_BORDER

    def _preview_pen_width(self):
        return 4 if self._mode == "row" else 2

    def clear_preview(self):
        """Șterge tot preview-ul: poligon, linia rubber, vertecși."""
        if self.preview_item is not None:
            self._scene.removeItem(self.preview_item)
            self.preview_item = None
        if self._rubber_line is not None:
            self._scene.removeItem(self._rubber_line)
            self._rubber_line = None
        for v in self._vertex_items:
            self._scene.removeItem(v)
        self._vertex_items = []
        # notificăm consumatorii
        try:
            self.drawing_info.emit(0, 0.0, 0.0, False, 0.0)
        except Exception:
            pass

    def update_preview(self, points: List[Tuple[float, float]],
                       cursor_pos: Optional[QPointF] = None):
        """
        Actualizează preview-ul.
        - `points`: vertecși deja fixați (click stânga).
        - `cursor_pos`: poziția curentă a mouse-ului (pentru rubber-band).
        Ultimul element din `points` poate fi „candidatul curent" dacă cursor_pos e None.
        """
        if not self.preview_enabled:
            self.clear_preview()
            return

        if not points:
            self.clear_preview()
            return

        fill, border = self._colors()
        pw = self._preview_pen_width()

        # --- POLIGON PREVIEW (dacă avem ≥ 2 puncte) ---
        if len(points) >= 2:
            poly = QPolygonF([QPointF(x, y) for x, y in points])
            if self.preview_item is None:
                self.preview_item = QGraphicsPolygonItem(poly)
                self.preview_item.setBrush(QBrush(fill))
                pen = QPen(border, pw)
                pen.setCosmetic(True)  # linia rămâne constantă indiferent de zoom
                self.preview_item.setPen(pen)
                self.preview_item.setZValue(9)
                self._scene.addItem(self.preview_item)
            else:
                self.preview_item.setPolygon(poly)
                self.preview_item.setBrush(QBrush(fill))
                pen = QPen(border, pw)
                pen.setCosmetic(True)
                self.preview_item.setPen(pen)
        else:
            # ≤1 punct — nu desenăm poligon
            if self.preview_item is not None:
                self._scene.removeItem(self.preview_item)
                self.preview_item = None

        # --- VERTEX MARKERS ---
        # eliminăm cei vechi
        for v in self._vertex_items:
            self._scene.removeItem(v)
        self._vertex_items = []

        snap_close = False
        first_x, first_y = points[0]

        if cursor_pos is not None and len(points) >= 3:
            # detectăm dacă suntem în raza de snap-close
            dx = cursor_pos.x() - first_x
            dy = cursor_pos.y() - first_y
            # threshold în coordonate scenă — dar noi vrem în pixeli;
            # convertim prin scale factor curent
            scale = self.transform().m11() or 1.0
            snap_pixels = self.SNAP_THRESHOLD / max(abs(scale), 0.001)
            snap_close = (dx * dx + dy * dy) ** 0.5 < snap_pixels

        for i, (x, y) in enumerate(points):
            is_first = (i == 0)
            r = self.VERTEX_RADIUS + (2 if self._mode == "row" else 0)
            color = self.VERTEX_FIRST_HIGHLIGHT if (is_first and snap_close) else border
            ellipse = QGraphicsEllipseItem(x - r, y - r, 2 * r, 2 * r)
            ellipse.setBrush(QBrush(Qt.white))
            pen = QPen(color, pw)
            pen.setCosmetic(True)
            ellipse.setPen(pen)
            ellipse.setZValue(11)
            # primul punct un pic mai mare când snap-close
            if is_first and snap_close:
                bigger = r + 3
                ellipse.setRect(x - bigger, y - bigger, 2 * bigger, 2 * bigger)
            self._scene.addItem(ellipse)
            self._vertex_items.append(ellipse)

        # --- RUBBER-BAND (linie de la ultimul vertex la cursor) ---
        if self._rubber_line is not None:
            self._scene.removeItem(self._rubber_line)
            self._rubber_line = None

        if cursor_pos is not None:
            last_x, last_y = points[-1]
            line = QGraphicsLineItem(last_x, last_y, cursor_pos.x(), cursor_pos.y())
            rubber = border if self._mode == "row" else self.PREVIEW_RUBBER
            pen = QPen(rubber, pw, Qt.DashLine)
            pen.setCosmetic(True)
            line.setPen(pen)
            line.setZValue(9)
            self._scene.addItem(line)
            self._rubber_line = line

        # --- calculăm info live pentru status bar ---
        live_points = list(points)
        last_seg = 0.0
        if cursor_pos is not None:
            live_points.append((cursor_pos.x(), cursor_pos.y()))
            lx, ly = points[-1]
            last_seg = ((cursor_pos.x() - lx) ** 2 + (cursor_pos.y() - ly) ** 2) ** 0.5
        area, perim = _polygon_area_perimeter(live_points)
        try:
            self.drawing_info.emit(len(points), area, perim, snap_close, last_seg)
        except Exception:
            pass

    # =========================================================================
    # ZOOM (pivot pe cursor)
    # =========================================================================
    def wheelEvent(self, event):
        if event.angleDelta().y() > 0:
            factor = self._zoom_factor
        else:
            factor = 1.0 / self._zoom_factor

        cur_scale = self.transform().m11()
        new_scale = cur_scale * factor
        # Politica robusta: permitem tot timpul directia care aduce scale-ul
        # INAPOI in intervalul [min_scale, max_scale]. Blocam DOAR miscarile
        # care ies si mai mult in afara intervalului.
        if factor > 1.0:  # zoom-in
            if cur_scale >= self._max_scale and new_scale > self._max_scale:
                return
        else:  # zoom-out
            if cur_scale <= self._min_scale and new_scale < self._min_scale:
                return

        # zoom EXACT pe poziția cursorului (metoda manuală, robustă)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.NoAnchor)
        try:
            cursor_point = event.position().toPoint()
        except Exception:
            cursor_point = event.pos()
        old_scene = self.mapToScene(cursor_point)
        self.scale(factor, factor)
        new_scene = self.mapToScene(cursor_point)
        delta = new_scene - old_scene
        self.translate(delta.x(), delta.y())
        event.accept()

    # =========================================================================
    # PAN MODE
    # =========================================================================
    def set_pan_mode(self, enabled: bool):
        if enabled:
            self.setDragMode(QGraphicsView.ScrollHandDrag)
        else:
            self.setDragMode(QGraphicsView.NoDrag)

    # =========================================================================
    # PAN CU BUTONUL DIN MIJLOC (funcționează în orice mod)
    # =========================================================================
    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._middle_panning = True
            self._pan_last = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if getattr(self, "_middle_panning", False):
            delta = event.position() - self._pan_last
            self._pan_last = event.position()
            h = self.horizontalScrollBar()
            v = self.verticalScrollBar()
            h.setValue(h.value() - int(delta.x()))
            v.setValue(v.value() - int(delta.y()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton and getattr(self, "_middle_panning", False):
            self._middle_panning = False
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # =========================================================================
    # TASTATURA — F sau Home = fit harta la fereastra
    # =========================================================================
    def keyPressEvent(self, event):
        try:
            key = event.key()
            if key in (Qt.Key_F, Qt.Key_Home):
                self.fit_to_content()
                event.accept()
                return
        except Exception:
            pass
        super().keyPressEvent(event)
