"""
ui/tab_validate.py
====================
Validator tab: pick files/folders and check every DICOM file for
structural problems (see core/validate.py for exactly what's checked).
Results are shown as a tree - one top-level row per file, color-coded
by the worst severity found, expandable to see every individual finding.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.validate import validate_files
from ui.worker import run_in_background

_SEVERITY_COLOR = {
    "ERROR": QColor(230, 76, 76),
    "WARNING": QColor(230, 184, 0),
    "INFO": QColor(140, 140, 140),
}


def _validate_batch(paths, log=None):
    from pathlib import Path

    files: list[str] = []
    for p in paths:
        pth = Path(p)
        if pth.is_dir():
            files.extend(str(f) for f in sorted(pth.rglob("*")) if f.is_file())
        elif pth.is_file():
            files.append(str(pth))

    reports = []
    for f in files:
        if log:
            log(f"Validating {f} ...")
        reports.append(validate_files([f])[0])
    return reports


class ValidateTab(QWidget):
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

        self.validate_button = QPushButton("Validate")
        self.validate_button.clicked.connect(self._on_validate_clicked)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File / Finding", "Severity", "Tag"])

        self.summary_label = QPushButton("")  # used as a plain read-only status line
        self.summary_label.setEnabled(False)
        self.summary_label.setFlat(True)

        layout = QVBoxLayout()
        layout.addLayout(file_buttons)
        layout.addWidget(self.file_list, stretch=1)
        layout.addWidget(self.validate_button)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.tree, stretch=2)
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

    def _on_validate_clicked(self) -> None:
        paths = [self.file_list.item(i).text() for i in range(self.file_list.count())]
        if not paths:
            self.summary_label.setText("Add files or a folder first.")
            return
        self.validate_button.setEnabled(False)
        self.tree.clear()
        self.summary_label.setText("Validating...")
        self._thread = run_in_background(
            _validate_batch,
            paths=paths,
            on_log=lambda m: None,  # per-file progress isn't shown; the tree fills in when done
            on_finished=self._on_finished,
            on_failed=self._on_failed,
        )

    def _on_finished(self, reports) -> None:
        self.validate_button.setEnabled(True)
        clean = errors = warnings_only = 0

        for report in reports:
            if report.error_count:
                worst = "ERROR"
                errors += 1
            elif report.warning_count:
                worst = "WARNING"
                warnings_only += 1
            else:
                worst = "INFO"
                clean += 1

            top = QTreeWidgetItem([report.path, worst, ""])
            top.setForeground(1, _SEVERITY_COLOR.get(worst, QColor(200, 200, 200)))
            self.tree.addTopLevelItem(top)
            for finding in report.findings:
                child = QTreeWidgetItem([finding.message, finding.severity, finding.tag or ""])
                child.setForeground(1, _SEVERITY_COLOR.get(finding.severity, QColor(200, 200, 200)))
                top.addChild(child)
            if not report.findings:
                top.addChild(QTreeWidgetItem(["No issues found", "", ""]))

        self.tree.expandAll()
        self.tree.resizeColumnToContents(0)
        self.summary_label.setText(
            f"{len(reports)} file(s): {clean} clean, {warnings_only} with warnings only, {errors} with errors"
        )

    def _on_failed(self, message: str) -> None:
        self.validate_button.setEnabled(True)
        self.summary_label.setText(f"Error: {message}")
