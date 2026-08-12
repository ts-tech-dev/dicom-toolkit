"""
ui/worker.py
=============
Generic "run this function on a background thread, stream its log lines
to the console, report success/failure" wrapper. Every tab that kicks
off a network operation or a batch file job uses run_in_background()
instead of calling the (potentially slow - network timeouts, hundreds
of files) work directly on the GUI thread, which would freeze the whole
app until it finished.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal


class _Worker(QObject):
    finished = Signal(object)  # emits the function's return value
    failed = Signal(str)  # emits str(exception) if the function raised
    log = Signal(str)  # forwarded to whatever LogConsole the caller wired up

    def __init__(self, fn: Callable[..., Any], kwargs: dict):
        super().__init__()
        self._fn = fn
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            result = self._fn(log=self.log.emit, **self._kwargs)
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001 - surface *any* failure to the GUI instead of losing it on a background thread
            self.failed.emit(str(exc))


def run_in_background(
    fn: Callable[..., Any],
    on_log: Callable[[str], None] | None = None,
    on_finished: Callable[[Any], None] | None = None,
    on_failed: Callable[[str], None] | None = None,
    **kwargs: Any,
) -> QThread:
    """
    Starts `fn(log=<callback>, **kwargs)` on a new QThread.

    Every core/*.py operation (echo, send, find, move, get, validate,
    de-identify, ...) accepts a `log` keyword callback - that's what lets
    this one generic wrapper drive all of them.

    IMPORTANT: the caller must keep a reference to the returned QThread
    (e.g. `self._thread = run_in_background(...)`) until it finishes, or
    Python/Qt may garbage-collect it mid-run and silently kill the job.
    """
    thread = QThread()
    worker = _Worker(fn, kwargs)
    worker.moveToThread(thread)

    thread.started.connect(worker.run)
    if on_log:
        worker.log.connect(on_log)
    if on_finished:
        worker.finished.connect(on_finished)
    if on_failed:
        worker.failed.connect(on_failed)

    # Tear-down: whichever of finished/failed fires, stop the thread and
    # schedule both Qt objects for deletion once Qt is done with them.
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    worker.failed.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)

    thread._worker_ref = worker  # keep the worker alive as long as the thread is referenced
    thread.start()
    return thread
