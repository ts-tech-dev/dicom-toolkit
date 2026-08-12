"""
ui/widgets/node_selector.py
=============================
Reusable "which DICOM node am I talking to" widget: a dropdown of saved
presets (core/presets.py) plus editable AE title / host / port fields.
Every network tab (Echo, Send, Q/R, Worklist, Move) embeds one of these
for the remote node instead of duplicating the same three text fields
and save/delete buttons everywhere.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QWidget,
)

from config import DEFAULT_PORT, DEFAULT_REMOTE_AE_TITLE
from core.presets import NodePreset, PresetManager


class NodeSelector(QGroupBox):
    def __init__(self, title: str = "Remote Node", parent: QWidget | None = None):
        super().__init__(title, parent)
        self.presets = PresetManager()

        self.preset_combo = QComboBox()
        self.ae_title_edit = QLineEdit(DEFAULT_REMOTE_AE_TITLE)
        self.host_edit = QLineEdit("127.0.0.1")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(DEFAULT_PORT)
        self.save_button = QPushButton("Save as preset...")
        self.delete_button = QPushButton("Delete preset")

        self._refresh_presets()
        self.preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        self.save_button.clicked.connect(self._on_save_clicked)
        self.delete_button.clicked.connect(self._on_delete_clicked)

        form = QFormLayout()
        form.addRow("Preset:", self.preset_combo)
        form.addRow("AE Title:", self.ae_title_edit)
        form.addRow("Host:", self.host_edit)
        form.addRow("Port:", self.port_spin)
        buttons = QHBoxLayout()
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.delete_button)
        form.addRow(buttons)
        self.setLayout(form)

    def _refresh_presets(self) -> None:
        selected_name = self.preset_combo.itemData(self.preset_combo.currentIndex())
        self.presets.load()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem("(custom)")
        for p in self.presets.all():
            self.preset_combo.addItem(str(p), userData=p.name)
        if selected_name is not None:
            idx = self.preset_combo.findData(selected_name)
            self.preset_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.preset_combo.blockSignals(False)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override naming convention
        # Presets are edited from other tabs (the Node Presets tab, or
        # another NodeSelector's Save/Delete), each with its own
        # PresetManager loaded once at construction. Reload from disk
        # whenever this selector becomes visible so it never shows a
        # stale list without requiring an app restart.
        self._refresh_presets()
        super().showEvent(event)

    def _on_preset_selected(self, index: int) -> None:
        if index <= 0:
            return
        name = self.preset_combo.itemData(index)
        preset = self.presets.get(name)
        if preset:
            self.ae_title_edit.setText(preset.ae_title)
            self.host_edit.setText(preset.host)
            self.port_spin.setValue(preset.port)

    def _on_save_clicked(self) -> None:
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:")
        if not ok or not name.strip():
            return
        preset = NodePreset(
            name=name.strip(),
            ae_title=self.ae_title_edit.text().strip(),
            host=self.host_edit.text().strip(),
            port=self.port_spin.value(),
        )
        self.presets.add_or_update(preset)
        self._refresh_presets()
        QMessageBox.information(self, "Saved", f"Preset '{preset.name}' saved.")

    def _on_delete_clicked(self) -> None:
        index = self.preset_combo.currentIndex()
        if index <= 0:
            QMessageBox.warning(self, "No preset selected", "Select a saved preset to delete first.")
            return
        name = self.preset_combo.itemData(index)
        self.presets.delete(name)
        self._refresh_presets()

    # -- accessors used by tabs -------------------------------------------

    def ae_title(self) -> str:
        return self.ae_title_edit.text().strip()

    def host(self) -> str:
        return self.host_edit.text().strip()

    def port(self) -> int:
        return self.port_spin.value()
