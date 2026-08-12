"""
ui/tab_dataset_editor.py
==========================
Dataset Editor tab: browse every tag in a DICOM file (including nested
sequences) in a tree view, edit values in place, add new elements by
tag/VR/value, or delete elements - then save. Useful for hand-crafting
edge-case test files (a missing required tag, a malformed VR, an
oddball value) that the other tools would never intentionally produce.

The actual tree model lives in core/dataset_utils.py (DicomTreeModel) -
this file is just the tab's toolbar/dialogs wired up to it.
"""

from __future__ import annotations

import pydicom
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from core.dataset_utils import DicomTreeModel


class _AddElementDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add DICOM Element")

        self.tag_edit = QLineEdit()
        self.tag_edit.setPlaceholderText("GGGG,EEEE  e.g. 0010,0010")
        self.vr_edit = QLineEdit()
        self.vr_edit.setPlaceholderText("e.g. LO, DA, US, SQ...")
        self.value_edit = QLineEdit()
        self.value_edit.setPlaceholderText("value (use \\ to separate multiple values)")

        form = QFormLayout()
        form.addRow("Tag:", self.tag_edit)
        form.addRow("VR:", self.vr_edit)
        form.addRow("Value:", self.value_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def values(self) -> tuple[str, str, str]:
        return self.tag_edit.text().strip(), self.vr_edit.text().strip().upper(), self.value_edit.text()


class DatasetEditorTab(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self.open_button = QPushButton("Open File...")
        self.add_button = QPushButton("Add Element...")
        self.delete_button = QPushButton("Delete Selected")
        self.save_button = QPushButton("Save")
        self.save_as_button = QPushButton("Save As...")
        for b in (self.add_button, self.delete_button, self.save_button, self.save_as_button):
            b.setEnabled(False)

        self.open_button.clicked.connect(self._on_open)
        self.add_button.clicked.connect(self._on_add)
        self.delete_button.clicked.connect(self._on_delete)
        self.save_button.clicked.connect(self._on_save)
        self.save_as_button.clicked.connect(self._on_save_as)

        toolbar = QHBoxLayout()
        for b in (self.open_button, self.add_button, self.delete_button, self.save_button, self.save_as_button):
            toolbar.addWidget(b)
        toolbar.addStretch()

        self.tree_view = QTreeView()
        self.tree_view.setAlternatingRowColors(True)

        layout = QVBoxLayout()
        layout.addLayout(toolbar)
        layout.addWidget(self.tree_view, stretch=1)
        self.setLayout(layout)

        self._path: str | None = None
        self._ds = None
        self._model: DicomTreeModel | None = None

    def _on_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open DICOM file")
        if not path:
            return
        try:
            ds = pydicom.dcmread(path, force=True)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Could not open file", f"{path}\n\n{exc}")
            return

        self._path = path
        self._ds = ds
        self._model = DicomTreeModel(ds)
        self.tree_view.setModel(self._model)
        self.tree_view.expandToDepth(0)
        for col in range(self._model.columnCount()):
            self.tree_view.resizeColumnToContents(col)

        for b in (self.add_button, self.delete_button, self.save_button, self.save_as_button):
            b.setEnabled(True)

    def _on_add(self) -> None:
        if self._model is None:
            return
        dialog = _AddElementDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        tag_str, vr, value = dialog.values()
        if not tag_str or not vr:
            QMessageBox.warning(self, "Missing input", "Tag and VR are required.")
            return
        current = self.tree_view.currentIndex()
        if not self._model.add_element(current, tag_str, vr, value):
            QMessageBox.warning(self, "Could not add element", "Check the tag format (GGGG,EEEE) and VR.")

    def _on_delete(self) -> None:
        if self._model is None:
            return
        current = self.tree_view.currentIndex()
        if not current.isValid():
            QMessageBox.information(self, "Nothing selected", "Select an element to delete.")
            return
        if not self._model.delete_element(current):
            QMessageBox.warning(self, "Could not delete", "Select a single data element (not a sequence item row).")

    def _on_save(self) -> None:
        if self._ds is None or self._path is None:
            return
        try:
            self._ds.save_as(self._path, enforce_file_format=True)
            QMessageBox.information(self, "Saved", f"Saved to {self._path}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Save failed", str(exc))

    def _on_save_as(self) -> None:
        if self._ds is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save As", filter="DICOM (*.dcm)")
        if not path:
            return
        try:
            self._ds.save_as(path, enforce_file_format=True)
            self._path = path
            QMessageBox.information(self, "Saved", f"Saved to {path}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Save failed", str(exc))
