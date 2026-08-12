"""
ui/tab_storescp.py
====================
Storage SCP tab: run a local DICOM receiver so you can test that a
modality, PACS, or workstation sends images correctly *to* you - point
the device at this tool's AE title/host/port and watch what arrives.
Also useful as the destination for C-MOVE tests in the Query/Retrieve
tab.

core.net_ops.StorageSCP runs its own accept loop on a pynetdicom-managed
background thread once started() (it's non-blocking), so start/stop can
be called directly from the GUI thread. Its callbacks (log lines,
on_receive) fire from THAT background thread though, so they're
marshalled back to the GUI thread via a small QObject/Signal bridge
(_ReceiverBridge) - the same rule as everywhere else in this app: only
touch widgets from the GUI thread.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal
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

from config import DEFAULT_LOCAL_STORESCP_PORT, RECEIVED_FILES_DIR
from core.net_ops import StorageSCP
from ui.widgets.log_console import LogConsole


class _ReceiverBridge(QObject):
    log_line = Signal(str)
    file_received = Signal(str)


class StorageScpTab(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self.ae_title_edit = QLineEdit("DICOMTOOLKIT")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(DEFAULT_LOCAL_STORESCP_PORT)
        self.save_dir_edit = QLineEdit(str(RECEIVED_FILES_DIR))
        self.browse_button = QPushButton("Browse...")
        self.browse_button.clicked.connect(self._on_browse)

        form = QFormLayout()
        form.addRow("AE Title:", self.ae_title_edit)
        form.addRow("Port:", self.port_spin)
        save_row = QHBoxLayout()
        save_row.addWidget(self.save_dir_edit)
        save_row.addWidget(self.browse_button)
        form.addRow("Save to:", save_row)

        self.start_button = QPushButton("Start Listening")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(self._on_start)
        self.stop_button.clicked.connect(self._on_stop)
        buttons = QHBoxLayout()
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)

        self.received_list = QListWidget()
        self.log = LogConsole()

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.received_list, stretch=1)
        layout.addWidget(self.log, stretch=1)
        self.setLayout(layout)

        self._scp: StorageSCP | None = None
        self._bridge = _ReceiverBridge()
        self._bridge.log_line.connect(self.log.log)
        self._bridge.file_received.connect(self.received_list.addItem)

    def _on_browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Folder to save received files")
        if folder:
            self.save_dir_edit.setText(folder)

    def _on_start(self) -> None:
        if self._scp is not None and self._scp.is_running:
            return
        save_dir = self.save_dir_edit.text().strip() or str(RECEIVED_FILES_DIR)
        Path(save_dir).mkdir(parents=True, exist_ok=True)

        self._scp = StorageSCP(
            ae_title=self.ae_title_edit.text().strip() or "DICOMTOOLKIT",
            port=self.port_spin.value(),
            save_dir=save_dir,
            log=self._bridge.log_line.emit,
            on_receive=lambda path, ds: self._bridge.file_received.emit(Path(path).name),
        )
        try:
            self._scp.start()
        except OSError as exc:
            self.log.error(f"Could not start listener: {exc}")
            self._scp = None
            return

        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.ae_title_edit.setEnabled(False)
        self.port_spin.setEnabled(False)

    def _on_stop(self) -> None:
        self.shutdown()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.ae_title_edit.setEnabled(True)
        self.port_spin.setEnabled(True)

    def shutdown(self) -> None:
        """Stop the listener if running. Safe to call even if it was never started."""
        if self._scp is not None:
            self._scp.stop()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override naming convention
        self.shutdown()
        super().closeEvent(event)
