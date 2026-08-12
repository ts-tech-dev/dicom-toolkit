"""
ui/tab_convert_enhanced.py
============================
Enhanced -> Classic SOP conversion tab: pick Enhanced MR/CT/PET Image
files (or a folder of them), split each into classic single-frame
instances (core/enhanced_convert.py), and write them to an output
folder. Files that aren't Enhanced MR/CT/PET are skipped with a log
note rather than failing the whole batch.
"""

from __future__ import annotations

from pathlib import Path

import pydicom

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.enhanced_convert import convert_enhanced_to_classic
from ui.widgets.log_console import LogConsole
from ui.worker import run_in_background


def _convert_batch(paths, out_dir, log=None):
    files: list[str] = []
    for p in paths:
        pth = Path(p)
        if pth.is_dir():
            files.extend(str(f) for f in sorted(pth.rglob("*")) if f.is_file())
        elif pth.is_file():
            files.append(str(pth))

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    converted_files, skipped, failed = 0, 0, 0
    for f in files:
        try:
            ds = pydicom.dcmread(f)
        except Exception as exc:  # noqa: BLE001
            if log:
                log(f"  SKIP {Path(f).name}: not readable as DICOM ({exc})")
            skipped += 1
            continue
        try:
            outputs = convert_enhanced_to_classic(ds, log=log)
        except ValueError:
            if log:
                log(f"  SKIP {Path(f).name}: not an Enhanced MR/CT/PET Image")
            skipped += 1
            continue
        except Exception as exc:  # noqa: BLE001 - keep the batch going
            if log:
                log(f"  FAIL {Path(f).name}: {exc}")
            failed += 1
            continue

        for out_ds in outputs:
            out_path = Path(out_dir) / f"{out_ds.SOPInstanceUID}.dcm"
            out_ds.save_as(out_path, enforce_file_format=True)
        converted_files += len(outputs)

    return converted_files, skipped, failed


class ConvertEnhancedTab(QWidget):
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

        self.output_dir_edit = QLineEdit()
        self.output_browse_button = QPushButton("Browse...")
        self.output_browse_button.clicked.connect(self._on_browse_output)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_dir_edit)
        output_row.addWidget(self.output_browse_button)

        self.run_button = QPushButton("Convert Enhanced -> Classic")
        self.run_button.clicked.connect(self._on_run_clicked)

        self.log = LogConsole()

        layout = QVBoxLayout()
        layout.addLayout(file_buttons)
        layout.addWidget(self.file_list, stretch=1)
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

        self.run_button.setEnabled(False)
        self._thread = run_in_background(
            _convert_batch,
            paths=paths,
            out_dir=out_dir,
            on_log=self.log.log,
            on_finished=self._on_finished,
            on_failed=self._on_failed,
        )

    def _on_finished(self, result) -> None:
        self.run_button.setEnabled(True)
        converted, skipped, failed = result
        self.log.success(f"Done: {converted} classic instance(s) written, {skipped} file(s) skipped, {failed} failed.")

    def _on_failed(self, message: str) -> None:
        self.run_button.setEnabled(True)
        self.log.error(f"Unexpected error: {message}")
