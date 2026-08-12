"""
ui/tab_query_retrieve.py
==========================
Query/Retrieve tab: C-FIND against a PACS at Patient/Study/Series/Image
level, then either C-MOVE the selected result(s) to a third AE (e.g.
"send this study to my test viewer") or C-GET them straight into a
local folder over the same association.

The query identifier is built from two things merged together:
  1. a small set of default "please return this field" keys for the
     chosen query level (added with an empty value = universal match)
  2. whatever the user actually typed into the filter fields, which
     both filters AND requests that field back

Results are shown in a table whose columns are built dynamically from
whatever keys actually came back, since different PACS return slightly
different optional fields.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import core.net_ops as net_ops
from config import DEFAULT_LOCAL_AE_TITLE, DEFAULT_NETWORK_TIMEOUT
from ui.widgets.log_console import LogConsole
from ui.widgets.node_selector import NodeSelector
from ui.worker import run_in_background

_LEVELS = ["PATIENT", "STUDY", "SERIES", "IMAGE"]

_DEFAULT_RETURN_FIELDS = {
    "PATIENT": ["PatientName", "PatientID", "PatientBirthDate", "PatientSex"],
    "STUDY": [
        "PatientName", "PatientID", "StudyDate", "StudyTime", "StudyDescription",
        "StudyInstanceUID", "AccessionNumber", "ModalitiesInStudy",
        "NumberOfStudyRelatedInstances",
    ],
    "SERIES": [
        "SeriesInstanceUID", "SeriesDescription", "SeriesNumber", "Modality",
        "NumberOfSeriesRelatedInstances",
    ],
    "IMAGE": ["SOPInstanceUID", "InstanceNumber"],
}

# The unique key(s) that identify a result at each level - these are what
# get sent back as the C-MOVE/C-GET identifier for a selected row.
_IDENTIFYING_KEYS = {
    "PATIENT": ["PatientID"],
    "STUDY": ["StudyInstanceUID"],
    "SERIES": ["StudyInstanceUID", "SeriesInstanceUID"],
    "IMAGE": ["StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID"],
}

_FILTER_FIELDS = ["PatientID", "PatientName", "StudyDate", "AccessionNumber", "Modality",
                   "StudyInstanceUID", "SeriesInstanceUID"]


def _move_selected_rows(rows_fields, level, log=None, **kwargs):
    """Runs C-MOVE once per selected result row; returns the list of MoveProgress."""
    results = []
    for fields in rows_fields:
        criteria = {k: fields[k] for k in _IDENTIFYING_KEYS[level] if k in fields}
        results.append(net_ops.move(criteria=criteria, query_level=level, log=log, **kwargs))
    return results


def _get_selected_rows(rows_fields, level, log=None, **kwargs):
    """Runs C-GET once per selected result row; returns the list of MoveProgress."""
    results = []
    for fields in rows_fields:
        criteria = {k: fields[k] for k in _IDENTIFYING_KEYS[level] if k in fields}
        results.append(net_ops.get(criteria=criteria, query_level=level, log=log, **kwargs))
    return results


class QueryRetrieveTab(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self.node = NodeSelector("PACS / Q-R SCP")

        self.local_ae_edit = QLineEdit(DEFAULT_LOCAL_AE_TITLE)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 600)
        self.timeout_spin.setValue(DEFAULT_NETWORK_TIMEOUT)
        self.level_combo = QComboBox()
        self.level_combo.addItems(_LEVELS)
        self.level_combo.setCurrentText("STUDY")
        self.root_combo = QComboBox()
        self.root_combo.addItems(["STUDY", "PATIENT"])

        query_form = QFormLayout()
        query_form.addRow("My AE Title:", self.local_ae_edit)
        query_form.addRow("Timeout (s):", self.timeout_spin)
        query_form.addRow("Query Level:", self.level_combo)
        query_form.addRow("Root Model:", self.root_combo)

        self.filter_edits: dict[str, QLineEdit] = {}
        filter_form = QFormLayout()
        for key in _FILTER_FIELDS:
            edit = QLineEdit()
            edit.setPlaceholderText("(any)")
            self.filter_edits[key] = edit
            filter_form.addRow(f"{key}:", edit)
        filter_box = QGroupBox("Filter (blank = match anything)")
        filter_box.setLayout(filter_form)

        self.query_button = QPushButton("Query (C-FIND)")
        self.query_button.clicked.connect(self._on_query_clicked)

        self.results_table = QTableWidget(0, 0)
        self.results_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.results_table.setEditTriggers(QTableWidget.NoEditTriggers)

        self.move_dest_edit = QLineEdit()
        self.move_dest_edit.setPlaceholderText("Destination AE title")
        self.move_button = QPushButton("C-MOVE Selected")
        self.move_button.clicked.connect(self._on_move_clicked)

        self.get_folder_edit = QLineEdit()
        self.get_folder_button = QPushButton("Browse...")
        self.get_folder_button.clicked.connect(self._on_browse_get_folder)
        self.get_button = QPushButton("C-GET Selected")
        self.get_button.clicked.connect(self._on_get_clicked)

        move_row = QHBoxLayout()
        move_row.addWidget(self.move_dest_edit)
        move_row.addWidget(self.move_button)

        get_row = QHBoxLayout()
        get_row.addWidget(self.get_folder_edit)
        get_row.addWidget(self.get_folder_button)
        get_row.addWidget(self.get_button)

        self.log = LogConsole()

        top = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(self.node)
        left.addLayout(query_form)
        right = QVBoxLayout()
        right.addWidget(filter_box)
        top.addLayout(left)
        top.addLayout(right)

        layout = QVBoxLayout()
        layout.addLayout(top)
        layout.addWidget(self.query_button)
        layout.addWidget(self.results_table, stretch=1)
        layout.addLayout(move_row)
        layout.addLayout(get_row)
        layout.addWidget(self.log, stretch=1)
        self.setLayout(layout)

        self._thread = None
        self._results_fields: list[dict] = []

    # -- query ----------------------------------------------------------

    def _build_criteria(self, level: str) -> dict:
        criteria = {kw: "" for kw in _DEFAULT_RETURN_FIELDS[level]}
        for key, edit in self.filter_edits.items():
            text = edit.text().strip()
            if text:
                criteria[key] = text
        return criteria

    def _on_query_clicked(self) -> None:
        level = self.level_combo.currentText()
        self.query_button.setEnabled(False)
        self._thread = run_in_background(
            net_ops.find,
            host=self.node.host(),
            port=self.node.port(),
            called_ae_title=self.node.ae_title(),
            calling_ae_title=self.local_ae_edit.text().strip(),
            query_level=level,
            criteria=self._build_criteria(level),
            root_model=self.root_combo.currentText(),
            timeout=self.timeout_spin.value(),
            on_log=self.log.log,
            on_finished=self._on_query_finished,
            on_failed=self._on_failed,
        )

    def _on_query_finished(self, matches) -> None:
        self.query_button.setEnabled(True)
        self._results_fields = [m.fields for m in matches]
        self._populate_table(self._results_fields)

    def _populate_table(self, rows_fields: list[dict]) -> None:
        columns: list[str] = []
        for fields in rows_fields:
            for key in fields:
                if key not in columns:
                    columns.append(key)

        self.results_table.clear()
        self.results_table.setColumnCount(len(columns))
        self.results_table.setHorizontalHeaderLabels(columns)
        self.results_table.setRowCount(len(rows_fields))
        for row_idx, fields in enumerate(rows_fields):
            for col_idx, key in enumerate(columns):
                value = fields.get(key, "")
                self.results_table.setItem(row_idx, col_idx, QTableWidgetItem(str(value)))
        self.results_table.resizeColumnsToContents()

    def _selected_rows_fields(self) -> list[dict]:
        selected_rows = sorted({idx.row() for idx in self.results_table.selectedIndexes()})
        return [self._results_fields[r] for r in selected_rows if r < len(self._results_fields)]

    # -- move / get -------------------------------------------------------

    def _on_browse_get_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Folder to save retrieved images")
        if folder:
            self.get_folder_edit.setText(folder)

    def _on_move_clicked(self) -> None:
        rows = self._selected_rows_fields()
        if not rows:
            self.log.warning("Select one or more result rows first.")
            return
        dest = self.move_dest_edit.text().strip()
        if not dest:
            self.log.warning("Enter a destination AE title to move to.")
            return
        self.move_button.setEnabled(False)
        self._thread = run_in_background(
            _move_selected_rows,
            rows_fields=rows,
            level=self.level_combo.currentText(),
            host=self.node.host(),
            port=self.node.port(),
            called_ae_title=self.node.ae_title(),
            calling_ae_title=self.local_ae_edit.text().strip(),
            move_destination_ae_title=dest,
            root_model=self.root_combo.currentText(),
            timeout=self.timeout_spin.value(),
            on_log=self.log.log,
            on_finished=lambda _: self.move_button.setEnabled(True),
            on_failed=self._on_failed,
        )

    def _on_get_clicked(self) -> None:
        rows = self._selected_rows_fields()
        if not rows:
            self.log.warning("Select one or more result rows first.")
            return
        save_dir = self.get_folder_edit.text().strip()
        if not save_dir:
            self.log.warning("Choose a folder to save retrieved images into.")
            return
        self.get_button.setEnabled(False)
        self._thread = run_in_background(
            _get_selected_rows,
            rows_fields=rows,
            level=self.level_combo.currentText(),
            host=self.node.host(),
            port=self.node.port(),
            called_ae_title=self.node.ae_title(),
            calling_ae_title=self.local_ae_edit.text().strip(),
            save_dir=save_dir,
            root_model=self.root_combo.currentText(),
            timeout=self.timeout_spin.value(),
            on_log=self.log.log,
            on_finished=lambda _: self.get_button.setEnabled(True),
            on_failed=self._on_failed,
        )

    def _on_failed(self, message: str) -> None:
        self.query_button.setEnabled(True)
        self.move_button.setEnabled(True)
        self.get_button.setEnabled(True)
        self.log.error(f"Unexpected error: {message}")
