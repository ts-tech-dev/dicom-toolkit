"""
ui/tab_worklist.py
====================
Modality Worklist tab: query an MWL SCP the way a modality would when a
technologist pulls up scheduled patients. Useful for testing that your
RIS/broker is publishing worklist entries correctly, or that a modality
is querying with the right filters.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFormLayout,
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

# Identifier keys requested by default. ScheduledProcedureStepSequence is
# nested (each item holds Modality/AE Title/Date/Time/Description) so we
# flatten its first item's fields into the top level after the query for
# a readable table - see _flatten_worklist_item().
_TOP_LEVEL_FIELDS = ["PatientName", "PatientID", "PatientBirthDate", "PatientSex", "AccessionNumber"]
_STEP_FIELDS = [
    "Modality", "ScheduledStationAETitle", "ScheduledProcedureStepStartDate",
    "ScheduledProcedureStepStartTime", "ScheduledPerformingPhysicianName",
    "ScheduledProcedureStepDescription",
]

_FILTER_FIELDS = ["PatientID", "PatientName", "Modality", "ScheduledStationAETitle",
                   "ScheduledProcedureStepStartDate", "AccessionNumber"]


def _flatten_worklist_item(fields: dict) -> dict:
    flat = {k: v for k, v in fields.items() if k != "ScheduledProcedureStepSequence"}
    steps = fields.get("ScheduledProcedureStepSequence")
    if steps:
        first_step = steps[0]
        for elem in first_step:
            if elem.keyword:
                flat[elem.keyword] = elem.value
    return flat


class WorklistTab(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self.node = NodeSelector("Worklist SCP")

        self.local_ae_edit = QLineEdit(DEFAULT_LOCAL_AE_TITLE)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 600)
        self.timeout_spin.setValue(DEFAULT_NETWORK_TIMEOUT)
        local_form = QFormLayout()
        local_form.addRow("My AE Title:", self.local_ae_edit)
        local_form.addRow("Timeout (s):", self.timeout_spin)

        self.filter_edits: dict[str, QLineEdit] = {}
        filter_form = QFormLayout()
        for key in _FILTER_FIELDS:
            edit = QLineEdit()
            edit.setPlaceholderText("(any)")
            self.filter_edits[key] = edit
            filter_form.addRow(f"{key}:", edit)

        self.query_button = QPushButton("Query Worklist (C-FIND)")
        self.query_button.clicked.connect(self._on_query_clicked)

        self.results_table = QTableWidget(0, 0)
        self.results_table.setEditTriggers(QTableWidget.NoEditTriggers)

        self.log = LogConsole()

        top = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(self.node)
        left.addLayout(local_form)
        top.addLayout(left)
        top.addLayout(filter_form)

        layout = QVBoxLayout()
        layout.addLayout(top)
        layout.addWidget(self.query_button)
        layout.addWidget(self.results_table, stretch=1)
        layout.addWidget(self.log, stretch=1)
        self.setLayout(layout)

        self._thread = None

    def _build_criteria(self) -> dict:
        criteria = {kw: "" for kw in _TOP_LEVEL_FIELDS}
        # Requesting the scheduled step sequence with empty sub-fields asks
        # the SCP to return scheduling info for every matching patient.
        step_template = {kw: "" for kw in _STEP_FIELDS}
        criteria["ScheduledProcedureStepSequence"] = [step_template]

        for key, edit in self.filter_edits.items():
            text = edit.text().strip()
            if not text:
                continue
            if key in _TOP_LEVEL_FIELDS or key == "AccessionNumber":
                criteria[key] = text
            else:
                # Step-level filters (Modality, AE title, date) belong inside
                # the sequence item, not at the top level.
                criteria["ScheduledProcedureStepSequence"][0][key] = text

        return criteria

    def _on_query_clicked(self) -> None:
        self.query_button.setEnabled(False)
        self._thread = run_in_background(
            net_ops.find_worklist,
            host=self.node.host(),
            port=self.node.port(),
            called_ae_title=self.node.ae_title(),
            calling_ae_title=self.local_ae_edit.text().strip(),
            criteria=self._build_criteria(),
            timeout=self.timeout_spin.value(),
            on_log=self.log.log,
            on_finished=self._on_finished,
            on_failed=self._on_failed,
        )

    def _on_finished(self, matches) -> None:
        self.query_button.setEnabled(True)
        rows = [_flatten_worklist_item(m.fields) for m in matches]

        columns: list[str] = []
        for fields in rows:
            for key in fields:
                if key not in columns:
                    columns.append(key)

        self.results_table.clear()
        self.results_table.setColumnCount(len(columns))
        self.results_table.setHorizontalHeaderLabels(columns)
        self.results_table.setRowCount(len(rows))
        for row_idx, fields in enumerate(rows):
            for col_idx, key in enumerate(columns):
                self.results_table.setItem(row_idx, col_idx, QTableWidgetItem(str(fields.get(key, ""))))
        self.results_table.resizeColumnsToContents()

    def _on_failed(self, message: str) -> None:
        self.query_button.setEnabled(True)
        self.log.error(f"Unexpected error: {message}")
