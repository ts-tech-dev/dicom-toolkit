"""
ui/tab_batch.py
=================
Batch folder tools: operations that make more sense run over an entire
folder at once and produce file output rather than an interactive view -
currently PNG/JPG image export and a written validation report. (The
Validate and De-identify tabs are themselves already folder-aware for
their own interactive views; this tab is for the "just give me files on
disk" versions of batch work.)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pydicom
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.validate import validate_files
from ui.widgets.log_console import LogConsole
from ui.worker import run_in_background


def _iter_files(paths):
    files: list[str] = []
    for p in paths:
        pth = Path(p)
        if pth.is_dir():
            files.extend(str(f) for f in sorted(pth.rglob("*")) if f.is_file())
        elif pth.is_file():
            files.append(str(pth))
    return files


def _apply_default_window(frame: np.ndarray, ds) -> np.ndarray:
    wc = ds.get("WindowCenter", None)
    ww = ds.get("WindowWidth", None)
    if wc is not None and ww is not None:
        c = float(wc[0] if hasattr(wc, "__len__") and not isinstance(wc, str) else wc)
        w = float(ww[0] if hasattr(ww, "__len__") and not isinstance(ww, str) else ww)
    else:
        c = float((frame.max() + frame.min()) / 2.0)
        w = float(max(frame.max() - frame.min(), 1.0))
    low, high = c - w / 2.0, c + w / 2.0
    return (np.clip(frame, low, high) - low) / max(high - low, 1.0) * 255.0


def _export_to_images(paths, out_dir, fmt, log=None):
    from PIL import Image

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    ok, failed = 0, 0
    for f in _iter_files(paths):
        try:
            ds = pydicom.dcmread(f)
            if "PixelData" not in ds:
                if log:
                    log(f"  SKIP {Path(f).name}: no pixel data")
                continue
            arr = ds.pixel_array
            is_color = ds.get("SamplesPerPixel", 1) > 1
            n_frames = int(ds.get("NumberOfFrames", 1) or 1)
            stem = Path(f).stem

            for i in range(n_frames):
                frame = arr[i] if n_frames > 1 else arr
                if is_color:
                    img = Image.fromarray(np.clip(frame, 0, 255).astype("uint8"))
                else:
                    slope = float(ds.get("RescaleSlope", 1.0) or 1.0)
                    intercept = float(ds.get("RescaleIntercept", 0.0) or 0.0)
                    windowed = _apply_default_window(frame.astype("float64") * slope + intercept, ds)
                    img = Image.fromarray(windowed.astype("uint8"))
                suffix = f"_frame{i}" if n_frames > 1 else ""
                out_path = Path(out_dir) / f"{stem}{suffix}.{fmt}"
                img.save(out_path)
            if log:
                log(f"  OK   {Path(f).name} ({n_frames} frame(s))")
            ok += 1
        except Exception as exc:  # noqa: BLE001 - keep the batch going
            if log:
                log(f"  FAIL {Path(f).name}: {exc}")
            failed += 1
    return ok, failed


def _write_validation_report(paths, out_dir, log=None):
    files = _iter_files(paths)
    reports = validate_files(files)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    report_path = Path(out_dir) / "validation_report.txt"

    lines = []
    clean = errors = warnings_only = 0
    for report in reports:
        if log:
            log(f"  validated {Path(report.path).name}")
        if report.error_count:
            errors += 1
        elif report.warning_count:
            warnings_only += 1
        else:
            clean += 1
        lines.append(f"=== {report.path} ===")
        if not report.findings:
            lines.append("  No issues found.")
        for finding in report.findings:
            tag = f" [{finding.tag}]" if finding.tag else ""
            lines.append(f"  {finding.severity}: {finding.message}{tag}")
        lines.append("")

    header = f"Validated {len(reports)} file(s): {clean} clean, {warnings_only} warnings only, {errors} with errors\n\n"
    report_path.write_text(header + "\n".join(lines), encoding="utf-8")
    return str(report_path), clean, warnings_only, errors


class BatchTab(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self.file_list = QListWidget()
        self.add_files_button = QPushButton("Add Files...")
        self.add_folder_button = QPushButton("Add Folder...")
        self.clear_button = QPushButton("Clear")
        self.add_files_button.clicked.connect(self._on_add_files)
        self.add_folder_button.clicked.connect(self._on_add_folder)
        self.clear_button.clicked.connect(self.file_list.clear)
        file_buttons = QHBoxLayout()
        for b in (self.add_files_button, self.add_folder_button, self.clear_button):
            file_buttons.addWidget(b)

        self.operation_combo = QComboBox()
        self.operation_combo.addItems(["Export to PNG", "Export to JPG", "Write validation report"])

        self.output_dir_edit = QLineEdit()
        self.output_browse_button = QPushButton("Browse...")
        self.output_browse_button.clicked.connect(self._on_browse_output)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_dir_edit)
        output_row.addWidget(self.output_browse_button)

        self.run_button = QPushButton("Run")
        self.run_button.clicked.connect(self._on_run_clicked)

        self.log = LogConsole()

        layout = QVBoxLayout()
        layout.addLayout(file_buttons)
        layout.addWidget(self.file_list, stretch=1)
        layout.addWidget(self.operation_combo)
        layout.addLayout(output_row)
        layout.addWidget(self.run_button)
        layout.addWidget(self.log, stretch=1)
        self.setLayout(layout)

        self._thread = None

    def _on_add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Select DICOM files")
        for f in files:
            self.file_list.addItem(f)

    def _on_add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select folder (scanned recursively)")
        if folder:
            self.file_list.addItem(folder)

    def _on_browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Output folder")
        if folder:
            self.output_dir_edit.setText(folder)

    def _on_run_clicked(self) -> None:
        paths = [self.file_list.item(i).text() for i in range(self.file_list.count())]
        out_dir = self.output_dir_edit.text().strip()
        if not paths:
            self.log.warning("Add files or a folder first.")
            return
        if not out_dir:
            self.log.warning("Choose an output folder first.")
            return

        operation = self.operation_combo.currentText()
        self.run_button.setEnabled(False)

        if operation == "Write validation report":
            self._thread = run_in_background(
                _write_validation_report,
                paths=paths,
                out_dir=out_dir,
                on_log=self.log.log,
                on_finished=self._on_report_finished,
                on_failed=self._on_failed,
            )
        else:
            fmt = "png" if operation == "Export to PNG" else "jpg"
            self._thread = run_in_background(
                _export_to_images,
                paths=paths,
                out_dir=out_dir,
                fmt=fmt,
                on_log=self.log.log,
                on_finished=self._on_export_finished,
                on_failed=self._on_failed,
            )

    def _on_export_finished(self, result) -> None:
        self.run_button.setEnabled(True)
        ok, failed = result
        self.log.success(f"Export complete: {ok} succeeded, {failed} failed.")

    def _on_report_finished(self, result) -> None:
        self.run_button.setEnabled(True)
        report_path, clean, warnings_only, errors = result
        self.log.success(
            f"Report written to {report_path} ({clean} clean, {warnings_only} warnings only, {errors} with errors)"
        )

    def _on_failed(self, message: str) -> None:
        self.run_button.setEnabled(True)
        self.log.error(f"Unexpected error: {message}")
