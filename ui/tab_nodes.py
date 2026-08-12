"""
ui/tab_nodes.py
=================
Node/AE title preset manager tab: a central place to see, add, edit,
and delete every saved connection profile (core/presets.py). Each
network tab's NodeSelector widget can also save a preset on the fly,
but this tab is the full CRUD view over the same underlying JSON file,
so you can clean up / review your whole list in one place.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.presets import NodePreset, PresetManager

_COLUMNS = ["Name", "AE Title", "Host", "Port", "Description"]


class NodesTab(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.presets = PresetManager()

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        self.name_edit = QLineEdit()
        self.ae_title_edit = QLineEdit()
        self.host_edit = QLineEdit()
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(104)
        self.description_edit = QLineEdit()

        form = QFormLayout()
        form.addRow("Name:", self.name_edit)
        form.addRow("AE Title:", self.ae_title_edit)
        form.addRow("Host:", self.host_edit)
        form.addRow("Port:", self.port_spin)
        form.addRow("Description:", self.description_edit)

        self.save_button = QPushButton("Save / Update")
        self.new_button = QPushButton("Clear (New)")
        self.delete_button = QPushButton("Delete Selected")
        self.save_button.clicked.connect(self._on_save)
        self.new_button.clicked.connect(self._on_new)
        self.delete_button.clicked.connect(self._on_delete)

        buttons = QHBoxLayout()
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.new_button)
        buttons.addWidget(self.delete_button)

        layout = QVBoxLayout()
        layout.addWidget(self.table, stretch=1)
        layout.addLayout(form)
        layout.addLayout(buttons)
        self.setLayout(layout)

        self._refresh_table()

    def _refresh_table(self) -> None:
        presets = self.presets.all()
        self.table.setRowCount(len(presets))
        for row, p in enumerate(presets):
            for col, value in enumerate([p.name, p.ae_title, p.host, str(p.port), p.description]):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()

    def _on_selection_changed(self) -> None:
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        if not rows:
            return
        presets = self.presets.all()
        row = rows[0]
        if row >= len(presets):
            return
        p = presets[row]
        self.name_edit.setText(p.name)
        self.ae_title_edit.setText(p.ae_title)
        self.host_edit.setText(p.host)
        self.port_spin.setValue(p.port)
        self.description_edit.setText(p.description)

    def _on_save(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Name required", "Enter a name for this preset.")
            return
        preset = NodePreset(
            name=name,
            ae_title=self.ae_title_edit.text().strip(),
            host=self.host_edit.text().strip(),
            port=self.port_spin.value(),
            description=self.description_edit.text().strip(),
        )
        self.presets.add_or_update(preset)
        self._refresh_table()

    def _on_new(self) -> None:
        self.name_edit.clear()
        self.ae_title_edit.clear()
        self.host_edit.clear()
        self.port_spin.setValue(104)
        self.description_edit.clear()
        self.table.clearSelection()

    def _on_delete(self) -> None:
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        presets = self.presets.all()
        for row in rows:
            if row < len(presets):
                self.presets.delete(presets[row].name)
        self._refresh_table()
        self._on_new()
