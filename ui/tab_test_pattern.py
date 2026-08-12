"""
ui/tab_test_pattern.py
========================
Test pattern generator tab: create synthetic, non-PHI DICOM images
(core/test_pattern.py) to exercise every other tab without needing real
patient data - a small "study" of N instances sharing one Study/Series
UID is handy for testing batch sends, Q/R matching, or a Storage SCP.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.test_pattern import generate_test_dicom
from pydicom.uid import generate_uid


class TestPatternTab(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self.rows_spin = QSpinBox()
        self.rows_spin.setRange(16, 4096)
        self.rows_spin.setValue(512)
        self.cols_spin = QSpinBox()
        self.cols_spin.setRange(16, 4096)
        self.cols_spin.setValue(512)

        self.pattern_combo = QComboBox()
        self.pattern_combo.addItems(["gradient", "checkerboard", "noise", "solid"])

        self.frames_spin = QSpinBox()
        self.frames_spin.setRange(1, 500)
        self.frames_spin.setValue(1)

        self.instances_spin = QSpinBox()
        self.instances_spin.setRange(1, 200)
        self.instances_spin.setValue(1)

        self.modality_edit = QLineEdit("OT")
        self.patient_id_edit = QLineEdit("TEST-000001")
        self.patient_name_edit = QLineEdit("TEST^PATTERN")

        self.output_dir_edit = QLineEdit()
        self.output_browse_button = QPushButton("Browse...")
        self.output_browse_button.clicked.connect(self._on_browse_output)

        form = QFormLayout()
        form.addRow("Rows:", self.rows_spin)
        form.addRow("Columns:", self.cols_spin)
        form.addRow("Pattern:", self.pattern_combo)
        form.addRow("Frames per instance:", self.frames_spin)
        form.addRow("Instances in study:", self.instances_spin)
        form.addRow("Modality:", self.modality_edit)
        form.addRow("Patient ID:", self.patient_id_edit)
        form.addRow("Patient Name:", self.patient_name_edit)
        form.addRow("Output folder:", self.output_dir_edit)
        form.addRow("", self.output_browse_button)

        self.generate_button = QPushButton("Generate")
        self.generate_button.clicked.connect(self._on_generate)

        self.status_label = QPushButton("")
        self.status_label.setEnabled(False)
        self.status_label.setFlat(True)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.generate_button)
        layout.addWidget(self.status_label)
        layout.addStretch()
        self.setLayout(layout)

    def _on_browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Output folder")
        if folder:
            self.output_dir_edit.setText(folder)

    def _on_generate(self) -> None:
        out_dir = self.output_dir_edit.text().strip()
        if not out_dir:
            QMessageBox.warning(self, "No output folder", "Choose an output folder first.")
            return

        n = self.instances_spin.value()
        study_uid = generate_uid()
        series_uid = generate_uid()

        for i in range(n):
            out_path = Path(out_dir) / f"test_pattern_{i + 1:03d}.dcm"
            generate_test_dicom(
                str(out_path),
                rows=self.rows_spin.value(),
                cols=self.cols_spin.value(),
                pattern=self.pattern_combo.currentText(),
                num_frames=self.frames_spin.value(),
                modality=self.modality_edit.text().strip() or "OT",
                patient_id=self.patient_id_edit.text().strip() or "TEST-000001",
                patient_name=self.patient_name_edit.text().strip() or "TEST^PATTERN",
                study_instance_uid=study_uid,
                series_instance_uid=series_uid,
            )

        self.status_label.setText(f"Generated {n} instance(s) in {out_dir}")
        QMessageBox.information(self, "Done", f"Generated {n} instance(s) in:\n{out_dir}")
