"""
ui/widgets/log_console.py
==========================
Read-only, auto-scrolling log panel used by every tab that runs a
background operation (network calls, batch file processing, etc).

Qt widgets may only be touched from the GUI thread, but our core/*.py
network and batch functions run on background QThreads and just want to
call a plain `log(str)` callback. The fix is the usual Qt pattern: the
worker thread emits a Signal, and Qt automatically delivers signal
deliveries to slots on the *receiving* object's thread (a queued
connection), so `_append_line` always actually runs on the GUI thread
even though `log(...)` was called from a worker.
"""

from __future__ import annotations

import datetime
import html

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QPlainTextEdit, QWidget


class LogConsole(QPlainTextEdit):
    log_signal = Signal(str, str)  # (level, message)

    _COLORS = {
        "INFO": "#d0d0d0",
        "WARNING": "#e6b800",
        "ERROR": "#e64c4c",
        "SUCCESS": "#4caf50",
    }

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(5000)  # cap memory use on long-running sessions
        self.setStyleSheet(
            "QPlainTextEdit { background-color: #1e1e1e; color: #d0d0d0; "
            "font-family: Consolas, 'DejaVu Sans Mono', monospace; }"
        )
        self.log_signal.connect(self._append_line)

    # -- convenience level-tagged loggers -------------------------------

    def info(self, message: str) -> None:
        self.log_signal.emit("INFO", message)

    def warning(self, message: str) -> None:
        self.log_signal.emit("WARNING", message)

    def error(self, message: str) -> None:
        self.log_signal.emit("ERROR", message)

    def success(self, message: str) -> None:
        self.log_signal.emit("SUCCESS", message)

    def log(self, message: str) -> None:
        """Bound method usable directly as the `log: Callable[[str], None]` callback core/*.py expects."""
        self.log_signal.emit("INFO", message)

    def _append_line(self, level: str, message: str) -> None:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        color = self._COLORS.get(level, "#d0d0d0")
        safe_message = html.escape(message)
        self.appendHtml(
            f'<span style="color:#808080">[{ts}]</span> '
            f'<span style="color:{color}">{safe_message}</span>'
        )
