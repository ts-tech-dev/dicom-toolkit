"""
ui/tab_deidentify.py
======================
De-identification tab: run core/deidentify.py's PS3.15-style basic
profile over one or more files/folders, writing results to an output
folder. Uses one DeidentifySession per run so PatientID/UIDs stay
consistent across an entire batch (e.g. de-identifying a whole study
folder keeps every file pointing at the same new StudyInstanceUID).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.deidentify import DeidentifyOptions, DeidentifySession
from ui.widgets.log_console import LogConsole
from ui.worker import run_in_background


def _deidentify_batch(paths, out_dir, options, log=None):
    files: list[str] = []
    for p in paths:
        pth = Path(p)
        if pth.is_dir():
            files.extend(str(f) for f in sorted(pth.rglob("*")) if f.is_file())
        elif pth.is_file():
            files.append(str(pth))

    session = DeidentifySession()
    ok, failed = 0, 0
    for f in files:
        out_path = Path(out_dir) / Path(f).name
        try:
            session.deidentify_file(f, str(out_path), options)
            if log:
                log(f"  OK   {Path(f).name}")
            ok += 1
        except Exception as exc:  # noqa: BLE001 - keep going through the rest of the batch
            if log:
                log(f"  FAIL {Path(f).name}: {exc}")
            failed += 1
    return ok, failed


class DeidentifyTab(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self.file_list = QListWidget()
        self.add_files_button = QPushButton("Add Files...")
        self.add_folder_button = QPushButton("Add Folder...")
        self.clear_button = QPushButton("Clear")
        self.add_files_button.clicked.connect(self._on_add_files)
        self.add_folder_button.clicked.connect(self._on_add_folder)
        self.clear_button.clicked.connect(self.file_list.clear)
        file_buttons = QHBoxLayout()
        for b in (self.add_files_button, self.add_folder_button, self.clear_button):
            file_buttons.addWidget(b)

        self.output_dir_edit = QLineEdit()
        self.output_browse_button = QPushButton("Browse...")
        self.output_browse_button.clicked.connect(self._on_browse_output)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_dir_edit)
        output_row.addWidget(self.output_browse_button)

        self.remove_private_check = QCheckBox("Remove private tags")
        self.remove_private_check.setChecked(True)
        self.remove_overlay_check = QCheckBox("Remove overlay/curve data")
        self.remove_overlay_check.setChecked(True)
        self.keep_descriptions_check = QCheckBox("Keep Study/Series descriptions")
        self.keep_descriptions_check.setChecked(True)

        self.date_handling_combo = QComboBox()
        self.date_handling_combo.addItems(["remove", "shift", "keep"])
        self.date_shift_spin = QSpinBox()
        self.date_shift_spin.setRange(-3650, 3650)
        self.date_shift_spin.setValue(0)

        self.patient_id_prefix_edit = QLineEdit("ANON")
        self.fixed_patient_id_edit = QLineEdit()
        self.fixed_patient_id_edit.setPlaceholderText("(leave blank to auto-generate per patient)")
        self.fixed_patient_name_edit = QLineEdit()
        self.fixed_patient_name_edit.setPlaceholderText("(leave blank to auto-generate per patient)")

        options_form = QFormLayout()
        options_form.addRow(self.remove_private_check)
        options_form.addRow(self.remove_overlay_check)
        options_form.addRow(self.keep_descriptions_check)
        options_form.addRow("Date handling:", self.date_handling_combo)
        options_form.addRow("Date shift (days):", self.date_shift_spin)
        options_form.addRow("Pseudonym ID prefix:", self.patient_id_prefix_edit)
        options_form.addRow("Fixed PatientID (optional):", self.fixed_patient_id_edit)
        options_form.addRow("Fixed PatientName (optional):", self.fixed_patient_name_edit)
        options_box = QGroupBox("Options")
        options_box.setLayout(options_form)

        self.run_button = QPushButton("De-identify")
        self.run_button.clicked.connect(self._on_run_clicked)

        self.log = LogConsole()

        layout = QVBoxLayout()
        layout.addLayout(file_buttons)
        layout.addWidget(self.file_list, stretch=1)
        layout.addWidget(options_box)
        layout.addWidget(self._wrap(output_row, "Output folder:"))
        layout.addWidget(self.run_button)
        layout.addWidget(self.log, stretch=1)
        self.setLayout(layout)

        self._thread = None

    @staticmethod
    def _wrap(row_layout, label_text) -> QWidget:
        box = QGroupBox(label_text)
        box.setLayout(row_layout)
        return box

    def _on_add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Select DICOM files")
        for f in files:
            self.file_list.addItem(f)

    def _on_add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select folder (scanned recursively)")
        if folder:
            self.file_list.addItem(folder)

    def _on_browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Output folder")
        if folder:
            self.output_dir_edit.setText(folder)

    def _build_options(self) -> DeidentifyOptions:
        return DeidentifyOptions(
            remove_private_tags=self.remove_private_check.isChecked(),
            remove_overlays_curves=self.remove_overlay_check.isChecked(),
            keep_descriptions=self.keep_descriptions_check.isChecked(),
            date_handling=self.date_handling_combo.currentText(),
            date_shift_days=self.date_shift_spin.value(),
            patient_id_prefix=self.patient_id_prefix_edit.text().strip() or "ANON",
            fixed_patient_id=self.fixed_patient_id_edit.text().strip() or None,
            fixed_patient_name=self.fixed_patient_name_edit.text().strip() or None,
        )

    def _on_run_clicked(self) -> None:
        paths = [self.file_list.item(i).text() for i in range(self.file_list.count())]
        out_dir = self.output_dir_edit.text().strip()
        if not paths:
            self.log.warning("Add files or a folder first.")
            return
        if not out_dir:
            self.log.warning("Choose an output folder first.")
            return

        self.run_button.setEnabled(False)
        self._thread = run_in_background(
            _deidentify_batch,
            paths=paths,
            out_dir=out_dir,
            options=self._build_options(),
            on_log=self.log.log,
            on_finished=self._on_finished,
            on_failed=self._on_failed,
        )

    def _on_finished(self, result) -> None:
        self.run_button.setEnabled(True)
        ok, failed = result
        if failed:
            self.log.warning(f"De-identification complete: {ok} succeeded, {failed} failed.")
        else:
            self.log.success(f"De-identification complete: {ok} succeeded, 0 failed.")

    def _on_failed(self, message: str) -> None:
        self.run_button.setEnabled(True)
        self.log.error(f"Unexpected error: {message}")
