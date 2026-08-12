"""
ui/tab_viewer.py
==================
Image viewer tab: open a DICOM file (or step through every file in its
folder) and view it with proper window/level, zoom, pan, and multi-frame
scrubbing (see ui/widgets/image_view.py for the actual rendering/
interaction code). Also shows the key header fields at a glance and can
export the currently-displayed frame to PNG/JPG.
"""

from __future__ import annotations

from pathlib import Path

import pydicom
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSlider,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from ui.widgets.image_view import ImageView

# A quick-glance subset of header fields - the full tag tree is available
# in the Dataset Editor tab for anything not shown here.
_QUICK_INFO_FIELDS = [
    "PatientName", "PatientID", "StudyDate", "Modality", "StudyDescription",
    "SeriesDescription", "Rows", "Columns", "BitsAllocated", "PhotometricInterpretation",
    "NumberOfFrames", "SOPClassUID", "TransferSyntaxUID",
]


class ViewerTab(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

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

        self.image_view = ImageView()

        self.frame_slider = QSlider(Qt.Horizontal)
        self.frame_slider.setMinimum(0)
        self.frame_slider.setMaximum(0)
        self.frame_slider.valueChanged.connect(self._on_frame_slider_changed)
        self.frame_label = QLabel("Frame: 0 / 0")

        self.wl_label = QLabel("Window Center/Width: -")
        self.image_view.window_level_changed.connect(self._on_window_level_changed)

        self.reset_zoom_button = QPushButton("Reset Zoom")
        self.reset_zoom_button.clicked.connect(lambda: self.image_view.resetTransform())
        self.reset_wl_button = QPushButton("Reset Window/Level")
        self.reset_wl_button.clicked.connect(self._on_reset_window_level)
        self.export_button = QPushButton("Export Frame to PNG/JPG...")
        self.export_button.clicked.connect(self._on_export)

        controls = QHBoxLayout()
        controls.addWidget(self.reset_zoom_button)
        controls.addWidget(self.reset_wl_button)
        controls.addWidget(self.export_button)
        controls.addStretch()
        controls.addWidget(self.wl_label)

        right_panel = QVBoxLayout()
        right_panel.addWidget(self.image_view, stretch=1)
        right_panel.addWidget(self.frame_label)
        right_panel.addWidget(self.frame_slider)
        right_panel.addLayout(controls)
        right_widget = QWidget()
        right_widget.setLayout(right_panel)

        splitter = QSplitter()
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(1, 1)

        layout = QVBoxLayout()
        layout.addWidget(splitter)
        self.setLayout(layout)

        self._current_dir_files: list[str] = []
        self._current_ds = None

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
            return

        self._current_ds = ds
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

    def _update_wl_label(self) -> None:
        c, w = self.image_view.window_values()
        self.wl_label.setText(f"Window Center/Width: {c:.1f} / {w:.1f}")

    def _on_window_level_changed(self, center: float, width: float) -> None:
        self._update_wl_label()

    def _on_reset_window_level(self) -> None:
        if self._current_ds is not None:
            self.image_view.load_dataset(self._current_ds)  # recomputes default W/L from the header
            self._update_wl_label()

    # -- frame slider ---------------------------------------------------------

    def _on_frame_slider_changed(self, value: int) -> None:
        self.image_view.set_frame(value)
        n = self.image_view.frame_count()
        self.frame_label.setText(f"Frame: {self.image_view.current_frame_index() + 1} / {n}")

    def _on_export(self) -> None:
        if self._current_ds is None:
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
