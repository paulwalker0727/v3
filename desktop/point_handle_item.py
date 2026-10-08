from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsItem
from PySide6.QtCore import Qt, QPointF, Signal, QObject
from PySide6.QtGui import QBrush, QPen, QColor


class PointHandleItem(QObject, QGraphicsEllipseItem):
    moved = Signal(int, QPointF)     # index, new_scene_pos
    pressed = Signal(int)            # index (la începutul unui drag)
    released = Signal(int)           # index (la finalul unui drag)
    deleted = Signal(int)            # index (click dreapta → șterge punct)

    def __init__(self, index: int, scene_pos: QPointF, parent_item: QGraphicsItem):
        QObject.__init__(self)

        size = 10.0
        half = size / 2.0

        # IMPORTANT: handle-ul este copil al poligonului
        QGraphicsEllipseItem.__init__(self, -half, -half, size, size, parent_item)

        self.index = index
        self.setPos(scene_pos)

        # stil vizual
        self.normal_color = QColor(0, 120, 255)
        self.hover_color = QColor(255, 200, 0)

        self.setBrush(QBrush(self.normal_color))
        self.setPen(QPen(QColor(255, 255, 255), 1))
        self.setZValue(parent_item.zValue() + 5)

        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsScenePositionChanges, True)
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)

        self.setAcceptedMouseButtons(Qt.LeftButton | Qt.RightButton)
        self.setCursor(Qt.OpenHandCursor)

    def hoverEnterEvent(self, event):
        self.setBrush(QBrush(self.hover_color))
        self.setCursor(Qt.SizeAllCursor)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.setBrush(QBrush(self.normal_color))
        self.setCursor(Qt.OpenHandCursor)
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            event.accept()
            self.deleted.emit(self.index)
            return
        self.pressed.emit(self.index)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.released.emit(self.index)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.moved.emit(self.index, self.scenePos())
        return super().itemChange(change, value)
