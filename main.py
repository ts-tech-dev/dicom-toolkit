#!/usr/bin/env python3
"""
main.py
========
Entry point. Run with `python main.py` during development, or via the
packaged .exe (see build/build.bat) on Windows.
"""

import sys

from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("DICOM Toolkit")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
