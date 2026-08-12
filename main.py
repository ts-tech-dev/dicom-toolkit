#!/usr/bin/env python3
"""
main.py
========
Entry point. Run with `python main.py` during development, or via the
packaged .exe (see build/build.bat) on Windows.
"""

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from config import resource_path
from ui.main_window import MainWindow
from ui.theme import apply_theme, load_saved_theme


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("DICOM Toolkit")
    app.setWindowIcon(QIcon(str(resource_path("assets/icon.ico"))))
    apply_theme(app, load_saved_theme())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
