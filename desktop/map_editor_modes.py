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

from app_logging import get_logger

logger = get_logger(__name__)

VALID_MODES = {
    "select", "draw", "delete", "draw_sector", "delete_sector",
    "draw_row", "calibrate", "draw_guide_h", "draw_guide_v", "draw_guide_line", "ruler",
}


class EditorModes:
    def __init__(self, window):
        self.w = window
        self.current_mode: str = "select"

    def set_tool_mode(self, mode: str) -> bool:
        """Setează modul curent. Un mod necunoscut e refuzat și logat (modul rămâne neschimbat)."""
        if mode in VALID_MODES:
            self.current_mode = mode
            return True
        logger.error("Mod de hartă necunoscut refuzat: %r (modul rămâne %r)", mode, self.current_mode)
        return False

    def is_draw_mode(self) -> bool:
        return self.current_mode in ("draw", "draw_sector")

    def is_delete_mode(self) -> bool:
        return self.current_mode in ("delete", "delete_sector")
