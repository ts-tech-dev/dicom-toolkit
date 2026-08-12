"""
ui/main_window.py
===================
Top-level window: one tab per tool, all listed here so it's obvious
what the app can do and how each piece is wired together.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QMessageBox, QTabWidget

from config import APP_NAME, APP_VERSION, ensure_app_data_dir
from ui.tab_batch import BatchTab
from ui.tab_convert_enhanced import ConvertEnhancedTab
from ui.tab_dataset_editor import DatasetEditorTab
from ui.tab_deidentify import DeidentifyTab
from ui.tab_echo import EchoTab
from ui.tab_mask import MaskTab
from ui.tab_nodes import NodesTab
from ui.tab_query_retrieve import QueryRetrieveTab
from ui.tab_send import SendTab
from ui.tab_storescp import StorageScpTab
from ui.tab_test_pattern import TestPatternTab
from ui.tab_validate import ValidateTab
from ui.tab_viewer import ViewerTab
from ui.tab_worklist import WorklistTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        ensure_app_data_dir()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.resize(1200, 800)

        self.storescp_tab = StorageScpTab()

        tabs = QTabWidget()
        tabs.addTab(EchoTab(), "C-ECHO")
        tabs.addTab(SendTab(), "Send (C-STORE)")
        tabs.addTab(QueryRetrieveTab(), "Query/Retrieve")
        tabs.addTab(WorklistTab(), "Worklist (MWL)")
        tabs.addTab(self.storescp_tab, "Storage SCP (Receiver)")
        tabs.addTab(ValidateTab(), "Validate")
        tabs.addTab(ViewerTab(), "Viewer")
        tabs.addTab(MaskTab(), "Mask")
        tabs.addTab(DeidentifyTab(), "De-identify")
        tabs.addTab(ConvertEnhancedTab(), "Enhanced -> Classic")
        tabs.addTab(DatasetEditorTab(), "Dataset Editor")
        tabs.addTab(TestPatternTab(), "Test Pattern Generator")
        tabs.addTab(BatchTab(), "Batch Tools")
        tabs.addTab(NodesTab(), "Node Presets")

        self.setCentralWidget(tabs)

        menu_bar = self.menuBar()
        help_menu = menu_bar.addMenu("Help")
        about_action = help_menu.addAction("About")
        about_action.triggered.connect(self._show_about)

    def _show_about(self) -> None:
        QMessageBox.information(
            self,
            f"About {APP_NAME}",
            f"{APP_NAME} v{APP_VERSION}\n\n"
            "A DICOM testing toolkit for PACS analysts: verification, "
            "send, query/retrieve, worklist, validation, viewing, "
            "masking, de-identification, Enhanced->Classic SOP "
            "conversion, a dataset editor, synthetic test data, and a "
            "local Storage SCP receiver.",
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override naming convention
        # The Storage SCP tab runs a background listener that must be shut
        # down cleanly, or the port can be left in a lingering TIME_WAIT
        # state. QTabWidget children don't get their own closeEvent when
        # the main window closes, so we stop it explicitly here.
        self.storescp_tab.shutdown()
        super().closeEvent(event)
