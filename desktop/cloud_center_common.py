"""cloud_center_common.py — utilitare Qt comune pentru Centrul de configurare Cloud."""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

import cimitir_cloud_admin_client as adc
import cloud_center as cc
import field_inbox_client as fic
from cloud_supabase import CenterError
from field_worker import run_task


def run_bg(owner, fn: Callable[[], Any], ok: Callable[[Any], None], fail: Callable[[str], None]):
    """Rulează `fn` în fundal; erorile cunoscute ajung ca text fix în română, cele necunoscute ca mesaj generic (fără detalii/secrete)."""
    def wrapped():
        try:
            return fn()
        except (CenterError, adc.AdminApiError) as exc:
            raise fic.FieldCloudError(str(exc)) from None
    return run_task(owner, wrapped, ok, lambda msg, _nc: fail(msg))


def unavailable_box(key: str) -> QFrame:
    """Funcție neimplementată: marcată explicit ca INDISPONIBILĂ, cu motivul și etapa."""
    info = cc.UNAVAILABLE[key]
    box = QFrame()
    box.setObjectName(f"unavailable_{key}")
    box.setStyleSheet("QFrame{border:1px dashed #94a3b8;border-radius:6px;background:rgba(148,163,184,0.12);} QLabel{border:none;background:transparent;}")
    lay = QVBoxLayout(box)
    head = QLabel(f"<b>INDISPONIBIL — {info['stage']}:</b> {info['title']}")
    head.setWordWrap(True)
    why = QLabel(info["reason"])
    why.setWordWrap(True)
    why.setStyleSheet("color:#64748b;font-size:11px;")
    lay.addWidget(head)
    lay.addWidget(why)
    return box


def state_label(label: QLabel, state: str, text: str) -> None:
    label.setText(text)
    label.setStyleSheet(f"color:{cc.COLORS.get(state, '#64748b')};font-weight:600;")
