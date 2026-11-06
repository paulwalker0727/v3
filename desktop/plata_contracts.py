"""Legătura explicită Plată ↔ Contract (P3-B). Fără alegere automată: dacă locul are contracte, utilizatorul alege contractul
sau „plată fără contract” în mod explicit. Plățile istorice fără contract_id nu se modifică."""
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtWidgets import QComboBox, QLabel, QVBoxLayout, QWidget

import database as db

NO_CONTRACT = "__fara_contract__"
UNSET = None


def contracts_for_loc(cod_loc: str) -> List[Dict[str, Any]]:
    cod = (cod_loc or "").strip()
    if not cod:
        return []
    try:
        rows = [c for c in db.load_contracte() if (c.get("cod_loc") or "") == cod]
    except Exception:
        return []
    return sorted(rows, key=lambda c: (c.get("data_contract") or "", c.get("id") or 0))


def titular_of(contract: Optional[Dict[str, Any]]) -> str:
    """Titularul contractului: instantaneul istoric owner_*; fallback pe concesionarul legat doar dacă instantaneul lipsește."""
    if not contract:
        return ""
    name = (contract.get("owner_fullname") or "").strip()
    if name:
        return name
    cid = contract.get("concesionar_id")
    if cid:
        try:
            c = db.get_concesionar(int(cid)) or {}
            return f"{c.get('nume', '')} {c.get('prenume', '')}".strip()
        except Exception:
            return ""
    return ""


def contract_label(c: Dict[str, Any]) -> str:
    nr = c.get("numar_contract") or f"#{c.get('id')}"
    parts = [f"Contract {nr}"]
    if c.get("data_contract"):
        parts.append(f"din {c['data_contract']}")
    t = titular_of(c)
    parts.append(f"— {t}" if t else "— fără titular")
    return " ".join(parts)


class ContractPicker(QWidget):
    """Combo cu contractele locului + etichetă cu titularul. `choice()` întoarce UNSET | NO_CONTRACT | id contract."""

    def __init__(self, cod_loc: str = "", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        self.combo.setObjectName("plata-contract-combo")
        self.lbl_titular = QLabel("")
        self.lbl_titular.setObjectName("plata-titular-label")
        lay.addWidget(self.combo)
        lay.addWidget(self.lbl_titular)
        self.combo.currentIndexChanged.connect(self._on_changed)
        self.set_loc(cod_loc)

    def set_loc(self, cod_loc: str):
        self._contracts = {c["id"]: c for c in contracts_for_loc(cod_loc)}
        self.combo.blockSignals(True)
        self.combo.clear()
        if self._contracts:
            self.combo.addItem("— alege contractul —", UNSET)
            for cid, c in self._contracts.items():
                self.combo.addItem(contract_label(c), cid)
            self.combo.addItem("Plată fără contract (alegere explicită)", NO_CONTRACT)
        else:
            self.combo.addItem("Fără contract (locul nu are contracte)", NO_CONTRACT)
        self.combo.blockSignals(False)
        self._on_changed()

    def _on_changed(self, *_):
        ch = self.choice()
        if ch in (UNSET, NO_CONTRACT):
            self.lbl_titular.setText("" if ch is UNSET else "Concesionar: — (plată fără contract)")
        else:
            t = titular_of(self._contracts.get(ch))
            self.lbl_titular.setText(f"Concesionar: {t or '—'} (datele titularului din contract)")

    def choice(self):
        return self.combo.currentData()

    def contract_id(self) -> Tuple[bool, Optional[int]]:
        """(valid, contract_id). valid=False dacă locul are contracte și nu s-a ales nimic."""
        ch = self.choice()
        if ch is UNSET:
            return False, None
        return True, (None if ch == NO_CONTRACT else int(ch))

    def select_contract(self, contract_id: int):
        i = self.combo.findData(contract_id)
        if i >= 0:
            self.combo.setCurrentIndex(i)


def contract_columns(plata: Dict[str, Any], cache: Dict[int, Dict[str, Any]]) -> Tuple[str, str]:
    """(etichetă contract, titular) pentru o plată; plățile fără contract_id rămân marcate, nu sunt deduse."""
    cid = plata.get("contract_id")
    if not cid:
        return "— fără contract", ""
    if cid not in cache:
        cache[cid] = db.get_contract(int(cid)) or {}
    c = cache[cid]
    if not c:
        return f"#{cid} (inexistent)", ""
    return str(c.get("numar_contract") or f"#{cid}"), titular_of(c)
