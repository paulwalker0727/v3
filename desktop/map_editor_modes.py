# map_editor_modes.py
"""
Manager pentru „modul curent al hărții" (unealta activă).

Modes recunoscute:
    - "select"          — pan/selectare
    - "draw"            — desenare poligon loc de veci
    - "delete"          — ștergere loc
    - "draw_sector"     — desenare sector
    - "delete_sector"   — ștergere sector
"""

VALID_MODES = {
    "select", "draw", "delete", "draw_sector", "delete_sector",
    "draw_row", "calibrate", "draw_guide_h", "draw_guide_v", "draw_guide_line", "ruler",
}


class EditorModes:
    def __init__(self, window):
        self.w = window
        self.current_mode: str = "select"

    def set_tool_mode(self, mode: str) -> None:
        """Setează modul curent. Ignoră silențios valorile necunoscute."""
        if mode in VALID_MODES:
            self.current_mode = mode

    def is_draw_mode(self) -> bool:
        return self.current_mode in ("draw", "draw_sector")

    def is_delete_mode(self) -> bool:
        return self.current_mode in ("delete", "delete_sector")
