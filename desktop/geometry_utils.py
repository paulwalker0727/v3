"""
geometry_utils.py — funcții geometrice PURE (fără Qt) folosite de editorul avansat.

Toate coordonatele sunt tuple (x, y) în spațiul scenei (pixeli pe imaginea hărții).
Sunt pure ca să poată fi testate ușor și refolosite oriunde.
"""
from __future__ import annotations
import math
from typing import List, Tuple, Optional

Point = Tuple[float, float]


# ---------------------------------------------------------------------------
# BAZĂ
# ---------------------------------------------------------------------------
def dist(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def polyline_length(pts: List[Point]) -> float:
    return sum(dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def _cum_lengths(pts: List[Point]) -> List[float]:
    cum = [0.0]
    for i in range(len(pts) - 1):
        cum.append(cum[-1] + dist(pts[i], pts[i + 1]))
    return cum


def point_and_tangent_at(pts: List[Point], s: float) -> Tuple[Point, Point]:
    """Punctul și tangenta (unitară) la lungimea de arc `s` de-a lungul polyline-ului."""
    if len(pts) < 2:
        p = pts[0] if pts else (0.0, 0.0)
        return p, (1.0, 0.0)
    cum = _cum_lengths(pts)
    total = cum[-1]
    s = max(0.0, min(s, total))
    for i in range(len(pts) - 1):
        if s <= cum[i + 1] or i == len(pts) - 2:
            seg_len = cum[i + 1] - cum[i]
            t = 0.0 if seg_len == 0 else (s - cum[i]) / seg_len
            ax, ay = pts[i]
            bx, by = pts[i + 1]
            px = ax + (bx - ax) * t
            py = ay + (by - ay) * t
            dx, dy = bx - ax, by - ay
            n = math.hypot(dx, dy) or 1.0
            return (px, py), (dx / n, dy / n)
    return pts[-1], (1.0, 0.0)


# ---------------------------------------------------------------------------
# GENERARE LOCURI DE-A LUNGUL UNUI RÂND (linie reală, poate fi strâmbă)
# ---------------------------------------------------------------------------
def generate_plots_along_baseline(
    baseline: List[Point],
    count: int,
    width: float,
    depth: float,
    gap: float = 0.0,
    side: str = "center",
) -> List[List[Point]]:
    """
    Așează `count` dreptunghiuri de-a lungul liniei rândului, fiecare orientat
    pe direcția locală a liniei (deci urmează curbele/strâmbăturile).

    - `width`  = dimensiunea pe direcția rândului (de-a lungul liniei)
    - `depth`  = dimensiunea perpendiculară pe rând
    - `gap`    = spațiu între locuri
    - `side`   = 'center' | 'left' | 'right' (partea față de linie)

    Returnează o listă de poligoane (fiecare = listă de 4 puncte (x,y)).
    """
    if not baseline or len(baseline) < 2 or count <= 0 or width <= 0 or depth <= 0:
        return []

    if side == "center":
        n0, n1 = -depth / 2.0, depth / 2.0
    elif side == "left":
        n0, n1 = 0.0, depth
    else:  # right
        n0, n1 = 0.0, -depth

    step = width + gap
    plots: List[List[Point]] = []
    for i in range(count):
        s_center = width / 2.0 + i * step
        (px, py), (tx, ty) = point_and_tangent_at(baseline, s_center)
        # normala (perpendiculara pe tangenta)
        nx, ny = -ty, tx
        hw = width / 2.0
        corners = [
            (px - hw * tx + n0 * nx, py - hw * ty + n0 * ny),
            (px + hw * tx + n0 * nx, py + hw * ty + n0 * ny),
            (px + hw * tx + n1 * nx, py + hw * ty + n1 * ny),
            (px - hw * tx + n1 * nx, py - hw * ty + n1 * ny),
        ]
        plots.append(corners)
    return plots


# ---------------------------------------------------------------------------
# EDITARE POLIGON
# ---------------------------------------------------------------------------
def polygon_centroid(points: List[Point]) -> Point:
    n = len(points)
    if n == 0:
        return (0.0, 0.0)
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)


def point_to_segment(p: Point, a: Point, b: Point) -> Tuple[float, Point, float]:
    """Returnează (distanța, punct_proiectat, t) de la p la segmentul ab (t clamp 0..1)."""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom == 0:
        return dist(p, a), a, 0.0
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / denom
    t = max(0.0, min(1.0, t))
    proj = (ax + t * dx, ay + t * dy)
    return dist(p, proj), proj, t


def insert_vertex_on_polygon(points: List[Point], click: Point) -> Tuple[List[Point], int]:
    """Inserează un vârf nou pe muchia cea mai apropiată de `click`.
    Returnează (puncte_noi, index_inserat)."""
    n = len(points)
    if n < 2:
        return list(points), -1
    best_d, best_i, best_proj = float("inf"), 0, points[0]
    for i in range(n):
        a = points[i]
        b = points[(i + 1) % n]
        d, proj, _ = point_to_segment(click, a, b)
        if d < best_d:
            best_d, best_i, best_proj = d, i, proj
    new_pts = list(points)
    new_pts.insert(best_i + 1, best_proj)
    return new_pts, best_i + 1


def delete_vertex(points: List[Point], index: int) -> List[Point]:
    if len(points) <= 3 or not (0 <= index < len(points)):
        return list(points)
    new_pts = list(points)
    del new_pts[index]
    return new_pts


def transform_polygon(points: List[Point], angle_deg: float = 0.0, scale: float = 1.0,
                      dx: float = 0.0, dy: float = 0.0,
                      pivot: Optional[Point] = None) -> List[Point]:
    """Rotește (grade) + scalează în jurul pivotului, apoi translatează."""
    if pivot is None:
        pivot = polygon_centroid(points)
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    px, py = pivot
    out = []
    for x, y in points:
        rx, ry = x - px, y - py
        rx *= scale
        ry *= scale
        nx = rx * ca - ry * sa
        ny = rx * sa + ry * ca
        out.append((nx + px + dx, ny + py + dy))
    return out


# ---------------------------------------------------------------------------
# SNAP (activat cu Shift/Ctrl)
# ---------------------------------------------------------------------------
def snap_to_grid(pt: Point, origin: Point, spacing: float, angle_deg: float = 0.0) -> Point:
    """Rotunjește punctul la cea mai apropiată intersecție a grid-ului (cu unghi/origine)."""
    if spacing <= 0:
        return pt
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    ox, oy = origin
    # transform in coordonate grid
    vx, vy = pt[0] - ox, pt[1] - oy
    gx = vx * ca + vy * sa
    gy = -vx * sa + vy * ca
    gx = round(gx / spacing) * spacing
    gy = round(gy / spacing) * spacing
    # inapoi in coordonate scena
    nx = gx * ca - gy * sa + ox
    ny = gx * sa + gy * ca + oy
    return (nx, ny)


def snap_point(pt: Point, vertex_targets: List[Point], segments: List[Tuple[Point, Point]],
               threshold: float, grid: Optional[dict] = None) -> Tuple[Point, bool]:
    """
    Găsește cel mai apropiat punct de snap în raza `threshold`.
    Prioritate: vârfuri > segmente (ghidaje) > grid.
    Returnează (punct, s_a_facut_snap).
    """
    best_d, best = threshold, None
    for v in vertex_targets:
        d = dist(pt, v)
        if d < best_d:
            best_d, best = d, v
    if best is None:
        for a, b in segments:
            d, proj, _ = point_to_segment(pt, a, b)
            if d < best_d:
                best_d, best = d, proj
    if best is None and grid and grid.get("spacing", 0) > 0:
        gp = snap_to_grid(pt, grid.get("origin", (0.0, 0.0)),
                          grid["spacing"], grid.get("angle", 0.0))
        if dist(pt, gp) < threshold:
            best = gp
    if best is None:
        return pt, False
    return best, True


# ---------------------------------------------------------------------------
# CALIBRARE SCARĂ (pixeli <-> metri)
# ---------------------------------------------------------------------------
def px_per_meter(p1: Point, p2: Point, real_meters: float) -> Optional[float]:
    if real_meters <= 0:
        return None
    d = dist(p1, p2)
    if d <= 0:
        return None
    return d / real_meters


def polygon_area(points: List[Point]) -> float:
    n = len(points)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def area_m2(points: List[Point], ppm: Optional[float]) -> Optional[float]:
    if not ppm or ppm <= 0:
        return None
    return polygon_area(points) / (ppm * ppm)


# ---------------------------------------------------------------------------
# COD LOC (format configurabil, implicit A-01-01)
# ---------------------------------------------------------------------------
DEFAULT_CODE_TEMPLATE = "{S}-{R:02d}-{N:02d}"


def format_plot_code(template: str, sector_label: str, rand: int, loc: int) -> str:
    """
    Formatează codul locului.
    Placeholder-e: {S} = sector+parcela (ex. 'A'), {R} = rând, {N} = nr. loc.
    Implicit: 'A-01-01'.
    """
    tpl = template or DEFAULT_CODE_TEMPLATE
    try:
        return tpl.format(S=sector_label, R=int(rand), N=int(loc),
                          s=sector_label, r=int(rand), n=int(loc))
    except Exception:
        return f"{sector_label}-{int(rand):02d}-{int(loc):02d}"
