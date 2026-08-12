"""
ui/tab_echo.py
================
"Verification" tab: sends a single C-ECHO to a node and reports whether
it answered. This is the DICOM equivalent of `ping` - the first thing
to try when a device/server "isn't working" as a PACS analyst, since it
proves basic network reachability + AE title matching before you waste
time debugging a more complex Q/R or storage problem.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
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


class EchoTab(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self.node = NodeSelector("Node to verify")

        self.local_ae_edit = QLineEdit(DEFAULT_LOCAL_AE_TITLE)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 300)
        self.timeout_spin.setValue(DEFAULT_NETWORK_TIMEOUT)

        local_form = QFormLayout()
        local_form.addRow("My AE Title:", self.local_ae_edit)
        local_form.addRow("Timeout (s):", self.timeout_spin)

        self.echo_button = QPushButton("Send C-ECHO")
        self.echo_button.clicked.connect(self._on_echo_clicked)

        self.log = LogConsole()

        layout = QVBoxLayout()
        layout.addWidget(self.node)
        layout.addLayout(local_form)
        top_buttons = QHBoxLayout()
        top_buttons.addWidget(self.echo_button)
        top_buttons.addStretch()
        layout.addLayout(top_buttons)
        layout.addWidget(self.log, stretch=1)
        self.setLayout(layout)

        self._thread = None

    def _on_echo_clicked(self) -> None:
        self.echo_button.setEnabled(False)
        self._thread = run_in_background(
            net_ops.echo,
            host=self.node.host(),
            port=self.node.port(),
            called_ae_title=self.node.ae_title(),
            calling_ae_title=self.local_ae_edit.text().strip(),
            timeout=self.timeout_spin.value(),
            on_log=self.log.log,
            on_finished=self._on_finished,
            on_failed=self._on_failed,
        )

    def _on_finished(self, result) -> None:
        self.echo_button.setEnabled(True)
        if result.success:
            self.log.success(f"Node is alive: {result.message}")
        else:
            self.log.error(f"Verification failed: {result.message}")

    def _on_failed(self, message: str) -> None:
        self.echo_button.setEnabled(True)
        self.log.error(f"Unexpected error: {message}")
