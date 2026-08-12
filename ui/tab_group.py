"""
ui/tab_group.py
==================
Reusable "grouped tab": a dropdown selector plus a stacked widget, used to
fold a set of related tools under a single top-level tab (e.g. everything
that talks to a PACS node, or everything that's a file-based tool) instead
of giving every one of them its own slot in the tab bar.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget


class GroupedTab(QWidget):
    def __init__(self, items: list[tuple[str, QWidget]], parent: QWidget | None = None):
        super().__init__(parent)

        self.selector = QComboBox()
        self.stack = QStackedWidget()
        for label, widget in items:
            self.selector.addItem(label)
            self.stack.addWidget(widget)
        self.selector.currentIndexChanged.connect(self.stack.setCurrentIndex)

        selector_row = QHBoxLayout()
        selector_row.addWidget(QLabel("Tool:"))
        selector_row.addWidget(self.selector, stretch=1)

        layout = QVBoxLayout()
        layout.addLayout(selector_row)
        layout.addWidget(self.stack, stretch=1)
        self.setLayout(layout)
