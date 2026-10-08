from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QColorDialog, QSlider, QMessageBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QPen, QColor


class EditPlotWindow(QDialog):
    """
    Editor DOAR pentru culoare și opacitate.
    NU editează poligonul.
    NU creează handle-uri.
    NU permite mutarea punctelor.
    După salvare, dacă locul are cod_loc, se salvează geometria și
    se întreabă utilizatorul dacă vrea să pornească adăugarea înhumaților.
    """

    def __init__(self, parent, plot_item):
        super().__init__(parent)
        self.setWindowTitle("Schimbă culoarea locului")
        self.resize(320, 180)
        self.setModal(False)

        self.plot_item = plot_item
        self.view = getattr(parent, "map_view", None)
        self.scene = getattr(self.view, "_scene", None) if self.view is not None else None

        # salvăm starea inițială
        self.original_color = self.plot_item.brush().color()
        self.original_pen = self.plot_item.pen().color()

        layout = QVBoxLayout()
        layout.addWidget(
            QLabel(
                "Schimbă doar culoarea și opacitatea locului.\n"
                "Forma poligonului rămâne neschimbată."
            )
        )

        # Buton schimbare culoare
        btn_color = QPushButton("Schimbă culoarea")
        btn_color.clicked.connect(self.change_color)
        layout.addWidget(btn_color)

        # Slider opacitate
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(0, 255)
        self.opacity_slider.setValue(self.original_color.alpha())
        self.opacity_slider.valueChanged.connect(self.change_opacity)
        layout.addWidget(self.opacity_slider)

        # Butoane salvare / anulare
        btn_row = QHBoxLayout()
        btn_save = QPushButton("Salvează")
        btn_cancel = QPushButton("Anulează")
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        self.setLayout(layout)

        btn_save.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)

    # SCHIMBARE CULOARE
    def change_color(self):
        new_color = QColorDialog.getColor(self.plot_item.brush().color(), self, "Culoare loc")
        if not new_color.isValid():
            return

        alpha = self.plot_item.brush().color().alpha()
        new_color.setAlpha(alpha)

        self.plot_item.setBrush(QBrush(new_color))
        self.plot_item.setPen(QPen(new_color.darker(), 2))

    # SCHIMBARE OPACITATE
    def change_opacity(self, value: int):
        c = QColor(self.plot_item.brush().color())
        c.setAlpha(value)
        self.plot_item.setBrush(QBrush(c))
        self.plot_item.setPen(QPen(c.darker(), 2))

    # SALVARE
    def accept(self):
        data0 = self.plot_item.data(0)

        # extragem cod_loc în mod sigur
        cod_loc = None
        if isinstance(data0, dict):
            cod_loc = data0.get("cod_loc")
        elif hasattr(data0, "__getitem__") and "cod_loc" in data0:
            try:
                cod_loc = data0["cod_loc"]
            except Exception:
                cod_loc = None

        if not cod_loc:
            print("[EditPlotWindow] EROARE: cod_loc lipsă în data(0)")
            super().accept()
            return

        poly = self.plot_item.polygon()
        points = [(poly.at(i).x(), poly.at(i).y()) for i in range(poly.count())]

        color = self.plot_item.brush().color().name(QColor.HexArgb)

        # salvăm în DB și, dacă s-a salvat corect, întrebăm utilizatorul dacă vrea să adauge înhumați
        try:
            actions = getattr(self.parent(), "actions", None)
            if not actions or not callable(getattr(actions, "save_plot_to_db", None)):
                QMessageBox.warning(self, "Eroare", "Funcția de salvare nu este disponibilă.")
                super().accept()
                return

            id_loc = actions.save_plot_to_db(cod_loc, points, color)
            if not id_loc:
                QMessageBox.warning(self, "Eroare", "Nu am putut salva locul de veci.")
                super().accept()
                return

            # întrebăm utilizatorul dacă vrea să pornească adăugarea înhumaților
            reply = QMessageBox.question(
                self,
                "Adaugă înhumați",
                "Doriți să adăugați acum înhumați pentru acest loc?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply == QMessageBox.Yes:
                if callable(getattr(actions, "prompt_add_inhumati", None)):
                    try:
                        actions.prompt_add_inhumati(cod_loc)
                    except Exception:
                        # nu blocăm UI-ul dacă ceva nu merge cu dialogul de înhumați
                        pass

        except Exception as e:
            print("[EditPlotWindow] EROARE la salvare:", e)
            QMessageBox.warning(self, "Eroare", "A apărut o eroare la salvare.")
        finally:
            super().accept()

    # ANULARE
    def reject(self):
        try:
            self.plot_item.setBrush(QBrush(self.original_color))
            self.plot_item.setPen(QPen(self.original_pen.darker(), 2))
        except Exception:
            pass
        super().reject()
