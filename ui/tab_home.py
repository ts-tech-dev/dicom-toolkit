"""
ui/tab_home.py
=================
Home tab: the app's main page. Upload/browse DICOM imaging, view it with
proper window/level, zoom, pan, and multi-frame scrubbing, optionally mask
(redact) regions of it, then export either the currently displayed frame
to PNG/JPG or an masked copy of the file to DICOM.

Viewing and masking share one ImageView (see ui/widgets/image_view.py):
"Mask Mode" toggles that widget between its two left-drag behaviors -
window/level adjustment (off) and rectangle drawing (on) - so the same
image never needs to be reopened in a separate tool to redact it.
"""

from __future__ import annotations

from pathlib import Path

import pydicom
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from core.mask import Rect, apply_masks
from ui.widgets.image_view import ImageView

# A quick-glance subset of header fields - the full tag tree is available
# in the Dataset Editor (Tools tab) for anything not shown here.
_QUICK_INFO_FIELDS = [
    "PatientName", "PatientID", "StudyDate", "Modality", "StudyDescription",
    "SeriesDescription", "Rows", "Columns", "BitsAllocated", "PhotometricInterpretation",
    "NumberOfFrames", "SOPClassUID", "TransferSyntaxUID",
]


class HomeTab(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        # -- left: file browser + header quick view --------------------------
        self.open_file_button = QPushButton("Open File...")
        self.open_folder_button = QPushButton("Open Folder...")
        self.open_file_button.clicked.connect(self._on_open_file)
        self.open_folder_button.clicked.connect(self._on_open_folder)

        self.file_list = QListWidget()
        self.file_list.currentRowChanged.connect(self._on_file_selected)

        self.info_text = QTextEdit()
        self.info_text.setReadOnly(True)

        left_panel = QVBoxLayout()
        open_buttons = QHBoxLayout()
        open_buttons.addWidget(self.open_file_button)
        open_buttons.addWidget(self.open_folder_button)
        left_panel.addLayout(open_buttons)
        left_panel.addWidget(self.file_list, stretch=1)
        left_panel.addWidget(QLabel("Header (quick view):"))
        left_panel.addWidget(self.info_text, stretch=1)
        left_widget = QWidget()
        left_widget.setLayout(left_panel)

        # -- center: image view + frame/window controls -----------------------
        self.image_view = ImageView()
        self.image_view.region_drawn.connect(self._on_region_drawn)
        self.image_view.window_level_changed.connect(self._on_window_level_changed)

        self.frame_slider = QSlider(Qt.Horizontal)
        self.frame_slider.setMinimum(0)
        self.frame_slider.setMaximum(0)
        self.frame_slider.valueChanged.connect(self._on_frame_slider_changed)
        self.frame_label = QLabel("Frame: 0 / 0")

        self.wl_label = QLabel("Window Center/Width: -")

        self.mask_mode_button = QPushButton("Mask Mode: OFF")
        self.mask_mode_button.setCheckable(True)
        self.mask_mode_button.toggled.connect(self._on_mask_mode_toggled)
        self.reset_zoom_button = QPushButton("Reset Zoom")
        self.reset_zoom_button.clicked.connect(lambda: self.image_view.resetTransform())
        self.reset_wl_button = QPushButton("Reset Window/Level")
        self.reset_wl_button.clicked.connect(self._on_reset_window_level)

        controls = QHBoxLayout()
        controls.addWidget(self.mask_mode_button)
        controls.addWidget(self.reset_zoom_button)
        controls.addWidget(self.reset_wl_button)
        controls.addStretch()
        controls.addWidget(self.wl_label)

        center_panel = QVBoxLayout()
        center_panel.addWidget(self.image_view, stretch=1)
        center_panel.addWidget(self.frame_label)
        center_panel.addWidget(self.frame_slider)
        center_panel.addLayout(controls)
        center_widget = QWidget()
        center_widget.setLayout(center_panel)

        # -- right: masking + export --------------------------------------------
        self.mask_hint_label = QLabel(
            "Turn on Mask Mode above, then left-drag on the image to draw a "
            "redaction rectangle."
        )
        self.mask_hint_label.setWordWrap(True)
        self.region_list = QListWidget()
        self.remove_region_button = QPushButton("Remove Selected Region")
        self.clear_regions_button = QPushButton("Clear All Regions")
        self.remove_region_button.clicked.connect(self._on_remove_region)
        self.clear_regions_button.clicked.connect(self._on_clear_regions)
        self.apply_scope_combo = QComboBox()
        self.apply_scope_combo.addItems(["Current frame only", "All frames"])
        self.save_masked_button = QPushButton("Apply Masks && Save As DICOM...")
        self.save_masked_button.clicked.connect(self._on_save_masked)

        mask_box_layout = QVBoxLayout()
        mask_box_layout.addWidget(self.mask_hint_label)
        mask_box_layout.addWidget(self.region_list, stretch=1)
        region_buttons = QHBoxLayout()
        region_buttons.addWidget(self.remove_region_button)
        region_buttons.addWidget(self.clear_regions_button)
        mask_box_layout.addLayout(region_buttons)
        mask_box_layout.addWidget(QLabel("Apply masks to:"))
        mask_box_layout.addWidget(self.apply_scope_combo)
        mask_box_layout.addWidget(self.save_masked_button)
        mask_box = QGroupBox("Masking")
        mask_box.setLayout(mask_box_layout)

        self.export_image_button = QPushButton("Export Frame to PNG/JPG...")
        self.export_image_button.clicked.connect(self._on_export_image)
        export_box_layout = QVBoxLayout()
        export_box_layout.addWidget(self.export_image_button)
        export_box = QGroupBox("Export")
        export_box.setLayout(export_box_layout)

        right_panel = QVBoxLayout()
        right_panel.addWidget(mask_box, stretch=1)
        right_panel.addWidget(export_box)
        right_widget = QWidget()
        right_widget.setLayout(right_panel)
        right_widget.setMaximumWidth(320)

        splitter = QSplitter()
        splitter.addWidget(left_widget)
        splitter.addWidget(center_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(1, 1)

        layout = QVBoxLayout()
        layout.addWidget(splitter)
        self.setLayout(layout)

        self._current_dir_files: list[str] = []
        self._current_ds = None
        self._regions: list[Rect] = []

    # -- file navigation --------------------------------------------------

    def _on_open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open DICOM file")
        if path:
            self._current_dir_files = [path]
            self.file_list.clear()
            self.file_list.addItem(path)
            self.file_list.setCurrentRow(0)

    def _on_open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open folder")
        if not folder:
            return
        files = sorted(str(f) for f in Path(folder).rglob("*") if f.is_file())
        self._current_dir_files = files
        self.file_list.clear()
        for f in files:
            self.file_list.addItem(f)
        if files:
            self.file_list.setCurrentRow(0)

    def _on_file_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._current_dir_files):
            return
        path = self._current_dir_files[row]
        try:
            ds = pydicom.dcmread(path)
        except Exception as exc:  # noqa: BLE001 - show the error, don't crash the viewer on a bad file
            QMessageBox.warning(self, "Could not open file", f"{path}\n\n{exc}")
            return

        if "PixelData" not in ds:
            self.info_text.setPlainText("This file has no PixelData (not an image).")
            self._current_ds = None
            return

        self._current_ds = ds
        self._regions = []
        self.region_list.clear()
        self.image_view.load_dataset(ds)

        n_frames = self.image_view.frame_count()
        self.frame_slider.setMaximum(max(0, n_frames - 1))
        self.frame_slider.setValue(0)
        self.frame_label.setText(f"Frame: 1 / {n_frames}")
        self._update_wl_label()
        self._update_info_text(ds)

    def _update_info_text(self, ds) -> None:
        lines = []
        for keyword in _QUICK_INFO_FIELDS:
            if keyword in ds or hasattr(ds, keyword):
                lines.append(f"{keyword}: {getattr(ds, keyword, '')}")
        self.info_text.setPlainText("\n".join(lines))

    # -- window/level, zoom, frame scrubbing -------------------------------------

    def _update_wl_label(self) -> None:
        c, w = self.image_view.window_values()
        self.wl_label.setText(f"Window Center/Width: {c:.1f} / {w:.1f}")

    def _on_window_level_changed(self, center: float, width: float) -> None:
        self._update_wl_label()

    def _on_reset_window_level(self) -> None:
        if self._current_ds is not None:
            self.image_view.load_dataset(self._current_ds)  # recomputes default W/L from the header
            self._update_wl_label()

    def _on_frame_slider_changed(self, value: int) -> None:
        self.image_view.set_frame(value)
        n = self.image_view.frame_count()
        self.frame_label.setText(f"Frame: {self.image_view.current_frame_index() + 1} / {n}")

    # -- mask mode + regions --------------------------------------------------

    def _on_mask_mode_toggled(self, checked: bool) -> None:
        self.image_view.mask_mode = checked
        self.mask_mode_button.setText(f"Mask Mode: {'ON' if checked else 'OFF'}")

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

    # -- export -------------------------------------------------------------

    def _on_save_masked(self) -> None:
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

    def _on_export_image(self) -> None:
        if self._current_ds is None:
            QMessageBox.warning(self, "No file open", "Open a file first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export frame", filter="PNG (*.png);;JPEG (*.jpg)")
        if not path:
            return
        from PIL import Image
        import numpy as np

        frame = self.image_view.current_frame_array_raw()
        if frame is None:
            return
        if frame.ndim == 3:  # already color RGB
            img = Image.fromarray(np.clip(frame, 0, 255).astype("uint8"))
        else:
            c, w = self.image_view.window_values()
            from ui.widgets.image_view import _apply_window

            img = Image.fromarray(_apply_window(frame, c, w))
        img.save(path)
        QMessageBox.information(self, "Exported", f"Saved to {path}")
