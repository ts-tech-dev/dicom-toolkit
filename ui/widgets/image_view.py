"""
ui/widgets/image_view.py
==========================
DICOM image display widget built on QGraphicsView. Renders a pydicom
Dataset's pixel data with window/level applied, and supports:

  - Mouse wheel:            zoom
  - Right-button drag:       pan
  - Left-button drag:         adjust window center/width (standard viewer
                                 convention: up/down = center, left/right = width)
  - Frame scrubbing for multi-frame objects (set_frame())
  - "Mask mode": when enabled, left-button drag instead draws a
     rectangle and emits region_drawn(Rect) on release - used by the
     Masking tab to let you pick redaction regions visually instead of
     typing pixel coordinates.
  - A persistent mask-region overlay (set_mask_preview_regions()): drawn
     as translucent red rectangles on top of the image so already-marked
     regions stay visible - across pan/zoom/frame changes - instead of
     only flashing during the drag that created them.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from pydicom.dataset import Dataset

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QWidget,
)

from core.mask import Rect


def _first_of(value):
    """DICOM multi-valued elements (e.g. WindowCenter with VM>1) come back as a
    pydicom MultiValue; plain single-valued ones come back as a scalar. Normalize
    to a single float either way."""
    if value is None:
        return None
    if hasattr(value, "__len__") and not isinstance(value, str):
        return float(value[0])
    return float(value)


def _apply_window(frame: np.ndarray, center: float, width: float) -> np.ndarray:
    """Apply DICOM windowing to a single frame, returning an 8-bit array ready for display."""
    width = max(width, 1.0)
    low = center - width / 2.0
    high = center + width / 2.0
    clipped = np.clip(frame, low, high)
    scaled = (clipped - low) / (high - low) * 255.0
    return scaled.astype(np.uint8)


class ImageView(QGraphicsView):
    window_level_changed = Signal(float, float)  # center, width
    region_drawn = Signal(object)  # emits a core.mask.Rect when a mask-mode drag completes

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.Antialiasing, False)
        self.setBackgroundBrush(QColor(20, 20, 20))
        self.setMouseTracking(True)

        self._pixmap_item: Optional[QGraphicsPixmapItem] = None
        self._raw_frames: Optional[np.ndarray] = None  # (frames, rows, cols[, 3]) or (rows, cols[, 3])
        self._is_multiframe = False
        self._is_color = False
        self._frame_index = 0
        self._window_center = 128.0
        self._window_width = 256.0

        self.mask_mode = False
        self._mask_drag_start: Optional[QPointF] = None
        self._rubber_band_item: Optional[QGraphicsRectItem] = None
        self._mask_preview_items: list[QGraphicsRectItem] = []

        self._wl_drag_start = None
        self._wl_drag_origin_cw = None

        self._panning = False
        self._pan_start = None

    # -- loading ------------------------------------------------------------

    def load_dataset(self, ds: Dataset) -> None:
        arr = ds.pixel_array.astype(np.float64)
        slope = float(ds.get("RescaleSlope", 1.0) or 1.0)
        intercept = float(ds.get("RescaleIntercept", 0.0) or 0.0)

        self._is_color = ds.get("SamplesPerPixel", 1) > 1
        if not self._is_color:
            arr = arr * slope + intercept

        n_frames = int(ds.get("NumberOfFrames", 1) or 1)
        self._is_multiframe = n_frames > 1
        self._raw_frames = arr
        self._frame_index = 0

        wc = _first_of(ds.get("WindowCenter", None))
        ww = _first_of(ds.get("WindowWidth", None))
        if wc is not None and ww is not None:
            self._window_center, self._window_width = wc, ww
        else:
            self._window_center = float((arr.max() + arr.min()) / 2.0)
            self._window_width = float(max(arr.max() - arr.min(), 1.0))

        self.resetTransform()
        self._render()

    def frame_count(self) -> int:
        if self._raw_frames is None:
            return 0
        return self._raw_frames.shape[0] if self._is_multiframe else 1

    def set_frame(self, index: int) -> None:
        if self._raw_frames is None:
            return
        self._frame_index = max(0, min(index, self.frame_count() - 1))
        self._render()

    def set_window(self, center: float, width: float) -> None:
        self._window_center = center
        self._window_width = max(width, 1.0)
        self._render()

    def window_values(self) -> tuple:
        return self._window_center, self._window_width

    def current_frame_array_raw(self) -> Optional[np.ndarray]:
        """Un-windowed pixel data for the currently displayed frame (used by the Masking tab)."""
        if self._raw_frames is None:
            return None
        return self._raw_frames[self._frame_index] if self._is_multiframe else self._raw_frames

    def current_frame_index(self) -> int:
        return self._frame_index

    # -- persistent mask-region overlay --------------------------------------

    def set_mask_preview_regions(self, regions) -> None:
        """
        Show `regions` (core.mask.Rect) as translucent red rectangles on top
        of the image, replacing whatever was shown before. These are plain
        scene items (not baked into the pixmap), so they survive pan/zoom
        and frame changes without needing to be reapplied - kept only so a
        drawn region stays visibly marked instead of disappearing the
        instant the drag that created it ends.
        """
        for item in self._mask_preview_items:
            self._scene.removeItem(item)
        self._mask_preview_items = []
        for r in regions:
            item = QGraphicsRectItem(QRectF(r.x, r.y, r.width, r.height))
            item.setPen(QPen(QColor(255, 40, 40), 2))
            item.setBrush(QBrush(QColor(255, 40, 40, 90)))
            item.setZValue(10)
            self._scene.addItem(item)
            self._mask_preview_items.append(item)

    # -- rendering ------------------------------------------------------------

    def _render(self) -> None:
        if self._raw_frames is None:
            return
        frame = self._raw_frames[self._frame_index] if self._is_multiframe else self._raw_frames

        if self._is_color:
            display = np.clip(frame, 0, 255).astype(np.uint8)
            display = np.ascontiguousarray(display)
            h, w, _ = display.shape
            qimg = QImage(display.tobytes(), w, h, w * 3, QImage.Format_RGB888)
        else:
            display = _apply_window(frame, self._window_center, self._window_width)
            display = np.ascontiguousarray(display)
            h, w = display.shape
            qimg = QImage(display.tobytes(), w, h, w, QImage.Format_Grayscale8)

        # QPixmap.fromImage() deep-copies the pixel data, so it's safe that
        # `display`/`qimg` go out of scope right after this.
        pixmap = QPixmap.fromImage(qimg)
        if self._pixmap_item is None:
            self._pixmap_item = self._scene.addPixmap(pixmap)
        else:
            self._pixmap_item.setPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))

    # -- interaction: zoom, pan, window/level drag, mask-mode rectangle ------

    def wheelEvent(self, event):
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        self.scale(factor, factor)

    def mousePressEvent(self, event):
        if self._pixmap_item is None:
            return super().mousePressEvent(event)

        if event.button() == Qt.RightButton:
            self._panning = True
            self._pan_start = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            return

        if event.button() == Qt.LeftButton and self.mask_mode:
            self._mask_drag_start = self.mapToScene(event.pos())
            rect_item = QGraphicsRectItem(QRectF(self._mask_drag_start, self._mask_drag_start))
            rect_item.setPen(QPen(QColor(255, 40, 40), 2))
            self._scene.addItem(rect_item)
            self._rubber_band_item = rect_item
            return

        if event.button() == Qt.LeftButton and not self.mask_mode:
            self._wl_drag_start = event.pos()
            self._wl_drag_origin_cw = (self._window_center, self._window_width)
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning and self._pan_start is not None:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            return

        if self.mask_mode and self._mask_drag_start is not None and self._rubber_band_item is not None:
            current = self.mapToScene(event.pos())
            self._rubber_band_item.setRect(QRectF(self._mask_drag_start, current).normalized())
            return

        if not self.mask_mode and self._wl_drag_start is not None:
            dx = event.pos().x() - self._wl_drag_start.x()
            dy = event.pos().y() - self._wl_drag_start.y()
            base_center, base_width = self._wl_drag_origin_cw
            new_width = max(1.0, base_width + dx * 2)
            new_center = base_center + dy * 2
            self.set_window(new_center, new_width)
            self.window_level_changed.emit(new_center, new_width)
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton and self._panning:
            self._panning = False
            self._pan_start = None
            self.setCursor(Qt.ArrowCursor)
            return

        if self.mask_mode and self._rubber_band_item is not None:
            rect = self._rubber_band_item.rect()
            self._scene.removeItem(self._rubber_band_item)
            self._rubber_band_item = None
            self._mask_drag_start = None
            if rect.width() >= 2 and rect.height() >= 2:
                self.region_drawn.emit(
                    Rect(x=int(rect.x()), y=int(rect.y()), width=int(rect.width()), height=int(rect.height()))
                )
            return

        if self._wl_drag_start is not None:
            self._wl_drag_start = None
            self._wl_drag_origin_cw = None
            return

        super().mouseReleaseEvent(event)
