"""
ui/tab_home.py
=================
Home tab: the app's main page. Browse imaging in a Patient -> Study ->
Series -> Image hierarchy (like a PACS browser, not a flat file list),
view it with proper window/level, zoom, pan, multi-frame (cine) scrubbing,
and image-to-image scrubbing within a series, optionally mask (redact)
regions of it - with the marked regions staying visible on the image
instead of only flashing during the drag that drew them - then export
either the currently displayed frame to PNG/JPG, or the drawn mask
regions applied across every image in the whole study to DICOM.

Viewing and masking share one ImageView (see ui/widgets/image_view.py):
"Mask Mode" toggles that widget between its two left-drag behaviors -
window/level adjustment (off) and rectangle drawing (on) - so the same
image never needs to be reopened in a separate tool to redact it.
"""

from __future__ import annotations

import os
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
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from core.mask import Rect, apply_masks
from ui.widgets.image_view import ImageView
from ui.widgets.log_console import LogConsole
from ui.worker import run_in_background

# A quick-glance subset of header fields - the full tag tree is available
# in the Dataset Editor (Tools tab) for anything not shown here.
_QUICK_INFO_FIELDS = [
    "PatientName", "PatientID", "StudyDate", "Modality", "StudyDescription",
    "SeriesDescription", "Rows", "Columns", "BitsAllocated", "PhotometricInterpretation",
    "NumberOfFrames", "SOPClassUID", "TransferSyntaxUID",
]

_UNSORTED = 10**9  # sort key for missing SeriesNumber/InstanceNumber - sorts last, not first


def _safe_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _scan_headers(files: list[str], log=None) -> list[dict]:
    """
    Read just the headers (stop_before_pixels=True - no pixel decode) of
    every file so the Patient/Study/Series/Image tree can be built without
    paying to decode imaging data for files the user may never even view.
    Runs on a background thread (see _on_open_folder) since folders can
    hold hundreds of files.
    """
    rows = []
    for f in files:
        try:
            ds = pydicom.dcmread(f, stop_before_pixels=True)
        except Exception as exc:  # noqa: BLE001 - folder scans often include non-DICOM files; skip and keep going
            if log:
                log(f"  SKIP {Path(f).name}: {exc}")
            continue
        if "SOPInstanceUID" not in ds:
            continue
        rows.append({
            "path": f,
            "patient_id": str(ds.get("PatientID", "")),
            "patient_name": str(ds.get("PatientName", "")) or "Unknown",
            "study_uid": str(ds.get("StudyInstanceUID", "")) or f,
            "study_date": str(ds.get("StudyDate", "")),
            "study_desc": str(ds.get("StudyDescription", "")) or "(no description)",
            "series_uid": str(ds.get("SeriesInstanceUID", "")) or f,
            "series_num": _safe_int(ds.get("SeriesNumber", None)),
            "series_desc": str(ds.get("SeriesDescription", "")) or "(no description)",
            "modality": str(ds.get("Modality", "")),
            "instance_num": _safe_int(ds.get("InstanceNumber", None)),
            "n_frames": int(ds.get("NumberOfFrames", 1) or 1),
        })
    return rows


def _export_masked_study(paths, regions, frame_indices, common_root, out_dir, log=None):
    """
    Apply `regions` to every file in `paths` (the whole study, gathered by
    the caller from the browser tree) and save masked copies under
    `out_dir`, mirroring each file's path relative to `common_root` so
    files from different series don't collide/overwrite each other.

    Regions are pixel coordinates from whichever image they were drawn on;
    other images/series in the study may be a different size, so
    core.mask.apply_masks's own clipping is what keeps this from writing
    out-of-bounds - it does not rescale regions for images of a different
    size than the one they were drawn on (see README "Scope and
    limitations").
    """
    ok, failed = 0, 0
    for f in paths:
        try:
            ds = pydicom.dcmread(f)
            if "PixelData" not in ds:
                if log:
                    log(f"  SKIP {Path(f).name}: no pixel data")
                continue

            targets = frame_indices
            if targets is not None:
                n_frames = int(ds.get("NumberOfFrames", 1) or 1)
                in_range = [i for i in targets if i < n_frames]
                # The requested frame index came from a different file (the
                # one open when Export was clicked); if this file doesn't
                # have that many frames, mask all of its frames instead of
                # silently masking nothing (or crashing on an out-of-range
                # index).
                targets = in_range if in_range else (list(range(n_frames)) if n_frames > 1 else [0])

            apply_masks(ds, regions, frame_indices=targets)
            rel = Path(f).relative_to(common_root)
            out_path = Path(out_dir) / rel
            out_path.parent.mkdir(parents=True, exist_ok=True)
            ds.save_as(out_path, enforce_file_format=True)
            if log:
                log(f"  OK   {rel}")
            ok += 1
        except Exception as exc:  # noqa: BLE001 - keep the study export going past one bad file
            if log:
                log(f"  FAIL {Path(f).name}: {exc}")
            failed += 1
    return ok, failed


class HomeTab(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        # -- left: Patient/Study/Series/Image browser + header quick view ----
        self.open_file_button = QPushButton("Open File...")
        self.open_folder_button = QPushButton("Open Folder...")
        self.open_file_button.clicked.connect(self._on_open_file)
        self.open_folder_button.clicked.connect(self._on_open_folder)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(self._on_tree_selection_changed)

        self.info_text = QTextEdit()
        self.info_text.setReadOnly(True)

        left_panel = QVBoxLayout()
        open_buttons = QHBoxLayout()
        open_buttons.addWidget(self.open_file_button)
        open_buttons.addWidget(self.open_folder_button)
        left_panel.addLayout(open_buttons)
        left_panel.addWidget(self.tree, stretch=2)
        left_panel.addWidget(QLabel("Header (quick view):"))
        left_panel.addWidget(self.info_text, stretch=1)
        left_widget = QWidget()
        left_widget.setLayout(left_panel)

        # -- center: image view + series/frame navigation ----------------------
        self.image_view = ImageView()
        self.image_view.region_drawn.connect(self._on_region_drawn)
        self.image_view.window_level_changed.connect(self._on_window_level_changed)

        self.prev_image_button = QPushButton("◀ Prev Image")
        self.next_image_button = QPushButton("Next Image ▶")
        self.prev_image_button.setShortcut("PgUp")
        self.next_image_button.setShortcut("PgDown")
        self.prev_image_button.clicked.connect(self._on_prev_image)
        self.next_image_button.clicked.connect(self._on_next_image)
        self.series_position_label = QLabel("")

        series_nav = QHBoxLayout()
        series_nav.addWidget(self.prev_image_button)
        series_nav.addWidget(self.next_image_button)
        series_nav.addStretch()
        series_nav.addWidget(self.series_position_label)

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
        center_panel.addLayout(series_nav)
        center_panel.addWidget(self.frame_label)
        center_panel.addWidget(self.frame_slider)
        center_panel.addLayout(controls)
        center_widget = QWidget()
        center_widget.setLayout(center_panel)

        # -- right: masking + export + activity log ------------------------------
        self.mask_hint_label = QLabel(
            "Turn on Mask Mode above, then left-drag on the image to draw a "
            "redaction rectangle. Marked regions stay visible on the image."
        )
        self.mask_hint_label.setWordWrap(True)
        self.region_list = QListWidget()
        self.remove_region_button = QPushButton("Remove Selected Region")
        self.clear_regions_button = QPushButton("Clear All Regions")
        self.remove_region_button.clicked.connect(self._on_remove_region)
        self.clear_regions_button.clicked.connect(self._on_clear_regions)
        self.apply_scope_combo = QComboBox()
        self.apply_scope_combo.addItems(["Current frame only", "All frames"])
        self.export_study_button = QPushButton("Apply Masks && Export Study...")
        self.export_study_button.clicked.connect(self._on_export_masked_study)

        mask_box_layout = QVBoxLayout()
        mask_box_layout.addWidget(self.mask_hint_label)
        mask_box_layout.addWidget(self.region_list, stretch=1)
        region_buttons = QHBoxLayout()
        region_buttons.addWidget(self.remove_region_button)
        region_buttons.addWidget(self.clear_regions_button)
        mask_box_layout.addLayout(region_buttons)
        mask_box_layout.addWidget(QLabel("Apply masks to:"))
        mask_box_layout.addWidget(self.apply_scope_combo)
        export_study_hint = QLabel(
            "Export applies these regions to every image in the whole "
            "study (all series), not just the one currently open."
        )
        export_study_hint.setWordWrap(True)
        mask_box_layout.addWidget(export_study_hint)
        mask_box_layout.addWidget(self.export_study_button)
        mask_box = QGroupBox("Masking")
        mask_box.setLayout(mask_box_layout)

        self.export_image_button = QPushButton("Export Frame to PNG/JPG...")
        self.export_image_button.clicked.connect(self._on_export_image)
        export_box_layout = QVBoxLayout()
        export_box_layout.addWidget(self.export_image_button)
        export_box = QGroupBox("Export Current Frame")
        export_box.setLayout(export_box_layout)

        self.log = LogConsole()

        right_panel = QVBoxLayout()
        right_panel.addWidget(mask_box, stretch=2)
        right_panel.addWidget(export_box)
        right_panel.addWidget(QLabel("Activity Log:"))
        right_panel.addWidget(self.log, stretch=1)
        right_widget = QWidget()
        right_widget.setLayout(right_panel)
        right_widget.setMaximumWidth(360)

        splitter = QSplitter()
        splitter.addWidget(left_widget)
        splitter.addWidget(center_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(1, 1)

        layout = QVBoxLayout()
        layout.addWidget(splitter)
        self.setLayout(layout)

        self._current_path: str | None = None
        self._current_ds = None
        self._regions: list[Rect] = []
        self._scan_thread = None
        self._export_thread = None
        self._last_export_out_dir: str | None = None

    # -- opening files / building the browser tree --------------------------

    def _on_open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open DICOM file")
        if not path:
            return
        rows = _scan_headers([path])
        if not rows:
            QMessageBox.warning(self, "Could not open file", f"{path}\n\nNot a readable DICOM file.")
            return
        self._populate_tree(rows)

    def _on_open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open folder")
        if not folder:
            return
        files = sorted(str(f) for f in Path(folder).rglob("*") if f.is_file())
        if not files:
            self.log.warning("That folder has no files.")
            return
        self.log.info(f"Scanning {len(files)} file(s)...")
        self._scan_thread = run_in_background(
            _scan_headers,
            files=files,
            on_log=self.log.log,
            on_finished=self._on_scan_finished,
            on_failed=self._on_scan_failed,
        )

    def _on_scan_finished(self, rows: list[dict]) -> None:
        if not rows:
            self.log.warning("No readable DICOM files found in that folder.")
            return
        self._populate_tree(rows)
        self.log.success(f"Loaded {len(rows)} image(s).")

    def _on_scan_failed(self, message: str) -> None:
        self.log.error(f"Unexpected error scanning folder: {message}")

    def _populate_tree(self, rows: list[dict]) -> None:
        self.tree.clear()
        patients: dict[str, dict] = {}
        for r in rows:
            patient = patients.setdefault(r["patient_id"], {
                "label": f"{r['patient_name']} ({r['patient_id'] or 'no ID'})",
                "studies": {},
            })
            study = patient["studies"].setdefault(r["study_uid"], {
                "label": f"{r['study_date'] + '  ' if r['study_date'] else ''}{r['study_desc']}",
                "series": {},
            })
            series = study["series"].setdefault(r["series_uid"], {
                "sort_key": r["series_num"] if r["series_num"] is not None else _UNSORTED,
                "desc": f"Series {r['series_num'] if r['series_num'] is not None else '?'}: "
                        f"{r['series_desc']} [{r['modality']}]",
                "images": [],
            })
            label = f"Image {r['instance_num'] if r['instance_num'] is not None else '?'}"
            if r["n_frames"] > 1:
                label += f" - {r['n_frames']} frames (cine)"
            sort_key = r["instance_num"] if r["instance_num"] is not None else _UNSORTED
            series["images"].append((sort_key, label, r["path"]))

        first_image_item = None
        for patient_id in sorted(patients):
            patient = patients[patient_id]
            p_item = QTreeWidgetItem([patient["label"]])
            self.tree.addTopLevelItem(p_item)
            for study_uid in sorted(patient["studies"], key=lambda k: patient["studies"][k]["label"]):
                study = patient["studies"][study_uid]
                s_item = QTreeWidgetItem([study["label"]])
                p_item.addChild(s_item)
                for series_uid in sorted(study["series"], key=lambda k: study["series"][k]["sort_key"]):
                    series = study["series"][series_uid]
                    se_item = QTreeWidgetItem([f"{series['desc']} - {len(series['images'])} image(s)"])
                    s_item.addChild(se_item)
                    for _, label, path in sorted(series["images"], key=lambda t: t[0]):
                        img_item = QTreeWidgetItem([label])
                        img_item.setData(0, Qt.UserRole, path)
                        se_item.addChild(img_item)
                        if first_image_item is None:
                            first_image_item = img_item

        self.tree.expandAll()
        if first_image_item is not None:
            self.tree.setCurrentItem(first_image_item)

    # -- tree selection / navigation ---------------------------------------------

    def _first_image_descendant(self, item: QTreeWidgetItem) -> QTreeWidgetItem | None:
        if item.data(0, Qt.UserRole) is not None:
            return item
        for i in range(item.childCount()):
            found = self._first_image_descendant(item.child(i))
            if found is not None:
                return found
        return None

    def _on_tree_selection_changed(self, current: QTreeWidgetItem | None, previous) -> None:
        if current is None:
            return
        path = current.data(0, Qt.UserRole)
        if path is None:
            # A Patient/Study/Series node was selected directly - drill down
            # to its first image, same as clicking a series in a PACS browser.
            first_img = self._first_image_descendant(current)
            if first_img is not None and first_img is not current:
                self.tree.setCurrentItem(first_img)
            return
        self._load_image(path)
        self._update_series_position_label(current)

    def _current_series_siblings(self) -> list[QTreeWidgetItem]:
        item = self.tree.currentItem()
        if item is None or item.data(0, Qt.UserRole) is None:
            return []
        series_item = item.parent()
        if series_item is None:
            return [item]
        return [series_item.child(i) for i in range(series_item.childCount())]

    def _update_series_position_label(self, item: QTreeWidgetItem) -> None:
        siblings = self._current_series_siblings()
        if len(siblings) <= 1:
            self.series_position_label.setText("")
            return
        idx = siblings.index(item)
        self.series_position_label.setText(f"Image {idx + 1} / {len(siblings)} in series")

    def _on_prev_image(self) -> None:
        siblings = self._current_series_siblings()
        item = self.tree.currentItem()
        if item not in siblings:
            return
        idx = siblings.index(item)
        if idx > 0:
            self.tree.setCurrentItem(siblings[idx - 1])

    def _on_next_image(self) -> None:
        siblings = self._current_series_siblings()
        item = self.tree.currentItem()
        if item not in siblings:
            return
        idx = siblings.index(item)
        if idx < len(siblings) - 1:
            self.tree.setCurrentItem(siblings[idx + 1])

    # -- loading an image ----------------------------------------------------

    def _load_image(self, path: str) -> None:
        try:
            ds = pydicom.dcmread(path)
        except Exception as exc:  # noqa: BLE001 - show the error, don't crash the viewer on a bad file
            QMessageBox.warning(self, "Could not open file", f"{path}\n\n{exc}")
            return

        if "PixelData" not in ds:
            self.info_text.setPlainText("This file has no PixelData (not an image).")
            self._current_ds = None
            self._current_path = None
            return

        self._current_path = path
        self._current_ds = ds
        self._regions = []
        self.region_list.clear()
        self.image_view.load_dataset(ds)
        self.image_view.set_mask_preview_regions([])

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

    # -- window/level, zoom, frame (cine) scrubbing -------------------------------

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
        self.image_view.set_mask_preview_regions(self._regions)

    def _on_remove_region(self) -> None:
        row = self.region_list.currentRow()
        if row < 0:
            return
        self.region_list.takeItem(row)
        del self._regions[row]
        self.image_view.set_mask_preview_regions(self._regions)

    def _on_clear_regions(self) -> None:
        self.region_list.clear()
        self._regions = []
        self.image_view.set_mask_preview_regions(self._regions)

    # -- export -------------------------------------------------------------

    def _current_study_item(self) -> QTreeWidgetItem | None:
        item = self.tree.currentItem()
        if item is None or item.data(0, Qt.UserRole) is None:
            return None
        series_item = item.parent()
        return series_item.parent() if series_item is not None else None

    def _collect_image_paths(self, item: QTreeWidgetItem) -> list[str]:
        path = item.data(0, Qt.UserRole)
        if path is not None:
            return [path]
        paths = []
        for i in range(item.childCount()):
            paths.extend(self._collect_image_paths(item.child(i)))
        return paths

    def _on_export_masked_study(self) -> None:
        if self._current_ds is None:
            QMessageBox.warning(self, "No file open", "Open a file first.")
            return
        if not self._regions:
            QMessageBox.warning(self, "No regions", "Draw at least one redaction rectangle first.")
            return
        study_item = self._current_study_item()
        if study_item is None:
            QMessageBox.warning(self, "No study", "Open a file from the browser first.")
            return
        paths = self._collect_image_paths(study_item)

        out_dir = QFileDialog.getExistingDirectory(self, "Folder to save the masked study into")
        if not out_dir:
            return

        frame_indices = None
        if self.apply_scope_combo.currentText() == "Current frame only":
            frame_indices = [self.image_view.current_frame_index()]

        common_root = os.path.commonpath(paths) if len(paths) > 1 else str(Path(paths[0]).parent)

        # _on_export_study_finished needs out_dir, but run_in_background's
        # on_finished callback only ever receives the worker's return value.
        # It's stored on self rather than captured in a lambda deliberately:
        # a lambda isn't a QObject-bound slot, so Qt can't tell it belongs
        # on the GUI thread and would invoke it on the *worker* thread
        # instead - fatal here since the callback shows a QMessageBox.
        self._last_export_out_dir = out_dir
        self.export_study_button.setEnabled(False)
        self._export_thread = run_in_background(
            _export_masked_study,
            paths=paths,
            regions=list(self._regions),
            frame_indices=frame_indices,
            common_root=common_root,
            out_dir=out_dir,
            on_log=self.log.log,
            on_finished=self._on_export_study_finished,
            on_failed=self._on_export_study_failed,
        )

    def _on_export_study_finished(self, result) -> None:
        self.export_study_button.setEnabled(True)
        out_dir = self._last_export_out_dir
        ok, failed = result
        self.log.success(f"Study export complete: {ok} succeeded, {failed} failed. Saved to {out_dir}")
        QMessageBox.information(self, "Study exported", f"{ok} image(s) saved to {out_dir}\n({failed} failed)")

    def _on_export_study_failed(self, message: str) -> None:
        self.export_study_button.setEnabled(True)
        self.log.error(f"Unexpected error exporting study: {message}")

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
