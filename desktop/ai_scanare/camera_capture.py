"""Captură webcam simplă cu PySide6 (QMediaCaptureSession)."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QAction, QImage, QPixmap
from PySide6.QtMultimedia import (
    QCamera,
    QImageCapture,
    QMediaCaptureSession,
    QMediaDevices,
)
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)


class CameraCaptureDialog(QDialog):
    """Dialog cu preview webcam + buton „Fotografiază". Salvează imaginea într-un fișier temp."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Fotografiere document")
        self.setMinimumSize(720, 560)

        self.captured_path: Optional[str] = None
        self._camera: Optional[QCamera] = None
        self._session = QMediaCaptureSession(self)
        self._capture = QImageCapture(self)
        self._session.setImageCapture(self._capture)

        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        # Selector cameră
        top = QHBoxLayout()
        top.addWidget(QLabel("Cameră:"))
        self.cmb_camera = QComboBox()
        self._populate_cameras()
        self.cmb_camera.currentIndexChanged.connect(self._on_camera_changed)
        top.addWidget(self.cmb_camera, 1)
        v.addLayout(top)

        # Preview
        self.video = QVideoWidget()
        self.video.setMinimumHeight(400)
        self.video.setStyleSheet("background:#000; border-radius:6px;")
        self._session.setVideoOutput(self.video)
        v.addWidget(self.video, 1)

        # Butoane
        btn_row = QHBoxLayout()
        self.btn_capture = QPushButton("📸  Fotografiază")
        self.btn_capture.setStyleSheet(
            "QPushButton { background:#0ea5e9; color:white; padding:10px 24px; "
            "border:none; border-radius:6px; font-weight:700; font-size:11pt; }"
            "QPushButton:hover { background:#0284c7; }"
        )
        self.btn_capture.clicked.connect(self._take_photo)

        self.btn_cancel = QPushButton("Anulează")
        self.btn_cancel.clicked.connect(self.reject)

        btn_row.addStretch()
        btn_row.addWidget(self.btn_cancel)
        btn_row.addWidget(self.btn_capture)
        v.addLayout(btn_row)

        # capture signal
        self._capture.imageCaptured.connect(self._on_image_captured)
        self._capture.errorOccurred.connect(self._on_capture_error)

        # pornim camera implicit
        if self.cmb_camera.count() > 0:
            self._on_camera_changed(0)

    def _populate_cameras(self):
        devices = QMediaDevices.videoInputs()
        if not devices:
            self.cmb_camera.addItem("(nicio cameră detectată)")
            self.btn_capture = None  # will be created later, so just note
            return
        for d in devices:
            self.cmb_camera.addItem(d.description(), d)

    def _on_camera_changed(self, idx: int):
        data = self.cmb_camera.itemData(idx)
        if data is None:
            return
        if self._camera:
            self._camera.stop()
        self._camera = QCamera(data, self)
        self._session.setCamera(self._camera)
        self._camera.start()

    def _take_photo(self):
        if not self._camera or not self._camera.isActive():
            QMessageBox.warning(self, "Cameră inactivă", "Camera nu este pornită.")
            return
        # QImageCapture salvează automat un fișier temporar
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=f"_cam_{ts}.jpg", prefix="scan_"
        )
        tmp.close()
        self._capture.captureToFile(tmp.name)

    def _on_image_captured(self, id_: int, image: QImage):
        # imaginea vine ca QImage; o salvăm noi ca JPG
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(tempfile.gettempdir(), f"scan_cam_{ts}.jpg")
        image.save(path, "JPG", 90)
        self.captured_path = path
        if self._camera:
            self._camera.stop()
        self.accept()

    def _on_capture_error(self, id_: int, error, msg: str):
        QMessageBox.warning(self, "Eroare captură", f"Nu am putut face poza: {msg}")

    def closeEvent(self, event):
        if self._camera:
            self._camera.stop()
        event.accept()
