"""
ui/tab_send.py
================
C-STORE tab: send one or more DICOM files (or whole folders, scanned
recursively) to a remote AE. Used to test how a PACS/workstation
handles inbound images - malformed files, unusual transfer syntaxes,
large studies, etc.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import core.net_ops as net_ops
from config import DEFAULT_LOCAL_AE_TITLE, DEFAULT_NETWORK_TIMEOUT
from ui.widgets.log_console import LogConsole
from ui.widgets.node_selector import NodeSelector
from ui.worker import run_in_background


class SendTab(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self.node = NodeSelector("Destination Node")

        self.local_ae_edit = QLineEdit(DEFAULT_LOCAL_AE_TITLE)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 600)
        self.timeout_spin.setValue(DEFAULT_NETWORK_TIMEOUT)
        local_form = QFormLayout()
        local_form.addRow("My AE Title:", self.local_ae_edit)
        local_form.addRow("Timeout (s):", self.timeout_spin)

        self.file_list = QListWidget()
        self.add_files_button = QPushButton("Add Files...")
        self.add_folder_button = QPushButton("Add Folder...")
        self.remove_button = QPushButton("Remove Selected")
        self.clear_button = QPushButton("Clear All")
        self.add_files_button.clicked.connect(self._on_add_files)
        self.add_folder_button.clicked.connect(self._on_add_folder)
        self.remove_button.clicked.connect(self._on_remove_selected)
        self.clear_button.clicked.connect(self.file_list.clear)

        file_buttons = QHBoxLayout()
        for b in (self.add_files_button, self.add_folder_button, self.remove_button, self.clear_button):
            file_buttons.addWidget(b)

        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self._on_send_clicked)

        self.log = LogConsole()

        layout = QVBoxLayout()
        layout.addWidget(self.node)
        layout.addLayout(local_form)
        layout.addLayout(file_buttons)
        layout.addWidget(self.file_list, stretch=1)
        layout.addWidget(self.send_button)
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

    def _on_remove_selected(self) -> None:
        for item in self.file_list.selectedItems():
            self.file_list.takeItem(self.file_list.row(item))

    def _paths(self) -> list[str]:
        return [self.file_list.item(i).text() for i in range(self.file_list.count())]

    def _on_send_clicked(self) -> None:
        paths = self._paths()
        if not paths:
            self.log.warning("No files or folders added to send.")
            return
        self.send_button.setEnabled(False)
        self._thread = run_in_background(
            net_ops.send,
            paths=paths,
            host=self.node.host(),
            port=self.node.port(),
            called_ae_title=self.node.ae_title(),
            calling_ae_title=self.local_ae_edit.text().strip(),
            timeout=self.timeout_spin.value(),
            on_log=self.log.log,
            on_finished=self._on_finished,
            on_failed=self._on_failed,
        )

    def _on_finished(self, results) -> None:
        self.send_button.setEnabled(True)
        ok = sum(1 for r in results if r.success)
        fail = len(results) - ok
        if fail:
            self.log.warning(f"Send complete: {ok} succeeded, {fail} failed.")
        else:
            self.log.success(f"Send complete: {ok} succeeded, 0 failed.")

    def _on_failed(self, message: str) -> None:
        self.send_button.setEnabled(True)
        self.log.error(f"Unexpected error: {message}")
