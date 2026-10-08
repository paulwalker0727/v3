"""Verificare update prin GitHub Releases — silențioasă, fără cheie API.

Repository-ul se poate schimba fără recompilare, prin variabila de mediu
`CIMITIR_UPDATE_REPO` (format `utilizator/repo`).

Folosire (un singur punct de integrare, în `main.py`):

    from updater import attach_update_banner
    attach_update_banner(window)

Dacă nu există internet, dacă GitHub limitează cererile sau dacă nu există
niciun release, funcția nu face absolut nimic — aplicația pornește normal.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

import requests
from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QToolBar, QWidget

GITHUB_REPO = os.environ.get("CIMITIR_UPDATE_REPO", "paulwalker0727/qrwork").strip()
RELEASE_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": f"Cimitir-Desktop-Updater/1.0 (+https://github.com/{GITHUB_REPO})",
}

TIMEOUT = (3.05, 5)  # (conectare, citire) — startup-ul nu are voie să atârne

_SEMVER_RE = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\.(0|[1-9]\d*))?"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?"
)


@dataclass(frozen=True)
class ReleaseInfo:
    tag_name: str
    version: tuple
    html_url: str
    assets: list = field(default_factory=list)


def current_version() -> str:
    try:
        from security.config import APP_VERSION
        return APP_VERSION
    except Exception:
        return "1.0.0"


def parse_semver(tag: str) -> tuple:
    """`v1.2.3` / `1.2` / `1.2.3-beta` → tuplu comparabil. Ridică ValueError."""
    value = (tag or "").strip().lstrip("vV")
    m = _SEMVER_RE.fullmatch(value)
    if not m:
        raise ValueError(f"Tag de versiune neacceptat: {tag!r}")
    major, minor = int(m.group(1)), int(m.group(2))
    patch = int(m.group(3) or 0)
    prerelease = m.group(4)
    # o versiune stabilă e mai nouă decât un prerelease cu același x.y.z
    return (major, minor, patch, 0 if prerelease else 1, prerelease or "")


def get_latest_release() -> Optional[ReleaseInfo]:
    """Ultimul release publicat sau None la ORICE problemă (verificarea e opțională)."""
    if "/" not in GITHUB_REPO:
        return None
    try:
        resp = requests.get(RELEASE_API_URL, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code != 200:
            # 404 = fără release-uri, 403/429 = limită de cereri → tăcere, fără reîncercări
            return None
        data = resp.json()
        assets = [
            {"name": a.get("name", ""), "download_url": a.get("browser_download_url", "")}
            for a in (data.get("assets") or [])
            if a.get("browser_download_url")
        ]
        return ReleaseInfo(
            tag_name=data["tag_name"],
            version=parse_semver(data["tag_name"]),
            html_url=data.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases",
            assets=assets,
        )
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        print(f"[updater] verificare indisponibilă: {exc}")
        return None


class _UpdateWorker(QObject):
    finished = Signal(object)

    @Slot()
    def run(self) -> None:
        self.finished.emit(get_latest_release())


class UpdateChecker(QObject):
    """Verifică pe un thread separat dacă există o versiune mai nouă."""

    update_available = Signal(str, str)  # tag, adresa paginii de release
    check_finished = Signal()

    def __init__(self, version: Optional[str] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        try:
            self.current = parse_semver(version or current_version())
        except ValueError:
            self.current = (0, 0, 0, 1, "")
        self._thread = QThread(self)
        self._worker = _UpdateWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_result)
        self._worker.finished.connect(self._thread.quit)

    def start(self) -> None:
        self._thread.start()

    @Slot(object)
    def _on_result(self, release) -> None:
        if release is not None and release.version > self.current:
            self.update_available.emit(release.tag_name, release.html_url)
        self.check_finished.emit()


class UpdateBanner(QToolBar):
    """Bară discretă sus: „Versiunea X e disponibilă — Descarcă"."""

    def __init__(self, tag: str, url: str, parent=None):
        super().__init__("Update", parent)
        self.setObjectName("update-banner")
        self.setMovable(False)
        self.setFloatable(False)
        self.url = url

        holder = QWidget()
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(12, 4, 12, 4)
        lay.setSpacing(10)

        label = QLabel(f"⬆  Versiunea {tag} este disponibilă.")
        label.setObjectName("update-banner-label")
        label.setStyleSheet("color:#0f172a; font-weight:700; background:transparent;")

        btn_download = QPushButton("Descarcă")
        btn_download.setObjectName("update-banner-download")
        btn_download.setCursor(Qt.PointingHandCursor)
        btn_download.setStyleSheet(
            "QPushButton { background:#0f172a; color:#f8fafc; border:none; "
            "border-radius:10px; padding:4px 14px; font-weight:700; }"
            "QPushButton:hover { background:#1e293b; }"
        )
        btn_download.clicked.connect(self._open)

        btn_close = QPushButton("✕")
        btn_close.setObjectName("update-banner-dismiss")
        btn_close.setToolTip("Ascunde până la următoarea pornire")
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setStyleSheet(
            "QPushButton { background:transparent; color:#0f172a; border:none; "
            "font-weight:700; padding:2px 6px; }"
            "QPushButton:hover { color:#7f1d1d; }"
        )
        btn_close.clicked.connect(self.hide)

        lay.addWidget(label)
        lay.addWidget(btn_download)
        lay.addStretch()
        lay.addWidget(btn_close)

        self.addWidget(holder)
        self.setStyleSheet("QToolBar#update-banner { background:#22c55e; border:none; }")

    def _open(self):
        QDesktopServices.openUrl(QUrl(self.url))


_checked_once = False


def attach_update_banner(window, version: Optional[str] = None) -> Optional[UpdateChecker]:
    """Pornește verificarea și afișează bara doar dacă apare o versiune nouă.

    O singură verificare per pornire a aplicației (limita GitHub e 60 cereri/oră).
    Întoarce checker-ul (util în teste) sau None dacă s-a verificat deja.
    """
    global _checked_once
    if _checked_once or not hasattr(window, "addToolBar"):
        return None
    _checked_once = True

    checker = UpdateChecker(version, parent=window)

    def _show(tag: str, url: str):
        banner = UpdateBanner(tag, url, window)
        window.addToolBarBreak(Qt.TopToolBarArea)
        window.addToolBar(Qt.TopToolBarArea, banner)
        banner.show()

    checker.update_available.connect(_show)
    checker.start()
    return checker


def reset_update_check() -> None:
    """Permite o nouă verificare (folosit în teste)."""
    global _checked_once
    _checked_once = False
