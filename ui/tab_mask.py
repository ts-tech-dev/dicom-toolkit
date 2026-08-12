"""
ui/tab_mask.py
================
Masking tab: open an image, drag rectangles directly on it to mark
burned-in PHI (or anything else) for redaction, then save a copy with
those regions blacked out. Pixel-level redaction that de-identify.py
can't do, since that tab only edits DICOM tags, not pixels.

Drawing is handled by ImageView itself (mask_mode=True makes left-drag
draw a rectangle instead of adjusting window/level); this tab just
listens for the region_drawn signal, keeps a running list, and applies
them via core/mask.py when you click Save.
"""

from __future__ import annotations

import pydicom
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from core.mask import Rect, apply_masks
from ui.widgets.image_view import ImageView


class MaskTab(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self.open_button = QPushButton("Open File...")
        self.open_button.clicked.connect(self._on_open)

        self.image_view = ImageView()
        self.image_view.mask_mode = True
        self.image_view.region_drawn.connect(self._on_region_drawn)

        self.frame_slider = QSlider(Qt.Horizontal)
        self.frame_slider.setMinimum(0)
        self.frame_slider.setMaximum(0)
        self.frame_slider.valueChanged.connect(self.image_view.set_frame)
        self.frame_label = QLabel("Frame: 0 / 0")
        self.frame_slider.valueChanged.connect(
            lambda v: self.frame_label.setText(f"Frame: {v + 1} / {self.image_view.frame_count()}")
        )

        self.region_list = QListWidget()
        self.remove_region_button = QPushButton("Remove Selected Region")
        self.clear_regions_button = QPushButton("Clear All Regions")
        self.remove_region_button.clicked.connect(self._on_remove_region)
        self.clear_regions_button.clicked.connect(self._on_clear_regions)

        self.apply_scope_combo = QComboBox()
        self.apply_scope_combo.addItems(["Current frame only", "All frames"])

        self.save_button = QPushButton("Apply & Save As...")
        self.save_button.clicked.connect(self._on_save)

        left_panel = QVBoxLayout()
        left_panel.addWidget(self.open_button)
        left_panel.addWidget(QLabel("Drag on the image (left-click) to draw a redaction rectangle."))
        left_panel.addWidget(QLabel("Regions:"))
        left_panel.addWidget(self.region_list, stretch=1)
        region_buttons = QHBoxLayout()
        region_buttons.addWidget(self.remove_region_button)
        region_buttons.addWidget(self.clear_regions_button)
        left_panel.addLayout(region_buttons)
        left_panel.addWidget(QLabel("Apply masks to:"))
        left_panel.addWidget(self.apply_scope_combo)
        left_panel.addWidget(self.save_button)
        left_widget = QWidget()
        left_widget.setLayout(left_panel)
        left_widget.setMaximumWidth(320)

        right_panel = QVBoxLayout()
        right_panel.addWidget(self.image_view, stretch=1)
        right_panel.addWidget(self.frame_label)
        right_panel.addWidget(self.frame_slider)

        layout = QHBoxLayout()
        layout.addWidget(left_widget)
        layout.addLayout(right_panel, stretch=1)
        self.setLayout(layout)

        self._current_path: str | None = None
        self._current_ds = None
        self._regions: list[Rect] = []

    def _on_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open DICOM file")
        if not path:
            return
        try:
            ds = pydicom.dcmread(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Could not open file", f"{path}\n\n{exc}")
            return
        if "PixelData" not in ds:
            QMessageBox.warning(self, "No image", "This file has no PixelData.")
            return

        self._current_path = path
        self._current_ds = ds
        self._regions = []
        self.region_list.clear()
        self.image_view.load_dataset(ds)

        n_frames = self.image_view.frame_count()
        self.frame_slider.setMaximum(max(0, n_frames - 1))
        self.frame_slider.setValue(0)
        self.frame_label.setText(f"Frame: 1 / {n_frames}")

    def _on_region_drawn(self, rect: Rect) -> None:
        self._regions.append(rect)
        self.region_list.addItem(QListWidgetItem(f"x={rect.x}, y={rect.y}, w={rect.width}, h={rect.height}"))

    def _on_remove_region(self) -> None:
        row = self.region_list.currentRow()
        if row < 0:
            return
        self.region_list.takeItem(row)
        del self._regions[row]

    def _on_clear_regions(self) -> None:
        self.region_list.clear()
        self._regions = []

    def _on_save(self) -> None:
        if self._current_ds is None:
            QMessageBox.warning(self, "No file open", "Open a file first.")
            return
        if not self._regions:
            QMessageBox.warning(self, "No regions", "Draw at least one redaction rectangle first.")
            return

        frame_indices = None
        if self.apply_scope_combo.currentText() == "Current frame only":
            frame_indices = [self.image_view.current_frame_index()]

        out_path, _ = QFileDialog.getSaveFileName(self, "Save masked copy as", filter="DICOM (*.dcm)")
        if not out_path:
            return

        apply_masks(self._current_ds, self._regions, frame_indices=frame_indices)
        self._current_ds.save_as(out_path, enforce_file_format=True)
        QMessageBox.information(self, "Saved", f"Masked copy saved to {out_path}")
