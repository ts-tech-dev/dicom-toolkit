"""
ui/main_window.py
===================
Top-level window: three tabs total, kept deliberately minimal.

  - Home:   the main page - open/view imaging, mask it, export it.
  - DIMSE:  everything that talks to a remote DICOM node via a DIMSE
             service (C-ECHO, Send, Query/Retrieve, Worklist, the
             Storage SCP receiver, Node Presets), picked from a dropdown.
  - Tools:  everything else that operates on local files (Validate,
             De-identify, Enhanced->Classic), also from a dropdown.

See ui/tab_group.py for the dropdown-plus-stack widget both grouped tabs
are built from, and ui/theme.py for the View > Theme (Light/Dark/Grey)
switcher wired up here.
"""

from __future__ import annotations

from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QTabWidget

from config import APP_NAME, APP_VERSION, ensure_app_data_dir
from ui.tab_convert_enhanced import ConvertEnhancedTab
from ui.tab_deidentify import DeidentifyTab
from ui.tab_echo import EchoTab
from ui.tab_group import GroupedTab
from ui.tab_home import HomeTab
from ui.tab_nodes import NodesTab
from ui.tab_query_retrieve import QueryRetrieveTab
from ui.tab_send import SendTab
from ui.tab_storescp import StorageScpTab
from ui.tab_validate import ValidateTab
from ui.tab_worklist import WorklistTab
from ui.theme import THEMES, apply_theme, load_saved_theme, save_theme


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        ensure_app_data_dir()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.resize(1200, 800)

        self.storescp_tab = StorageScpTab()

        dimse_tab = GroupedTab([
            ("C-ECHO", EchoTab()),
            ("Send (C-STORE)", SendTab()),
            ("Query/Retrieve", QueryRetrieveTab()),
            ("Worklist (MWL)", WorklistTab()),
            ("Storage SCP (Receiver)", self.storescp_tab),
            ("Node Presets", NodesTab()),
        ])

        tools_tab = GroupedTab([
            ("Validate", ValidateTab()),
            ("De-identify", DeidentifyTab()),
            ("Enhanced -> Classic", ConvertEnhancedTab()),
        ])

        tabs = QTabWidget()
        tabs.addTab(HomeTab(), "Home")
        tabs.addTab(dimse_tab, "DIMSE")
        tabs.addTab(tools_tab, "Tools")

        self.setCentralWidget(tabs)

        menu_bar = self.menuBar()

        view_menu = menu_bar.addMenu("View")
        theme_menu = view_menu.addMenu("Theme")
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        current_theme = load_saved_theme()
        for theme_name in THEMES:
            action = theme_menu.addAction(theme_name)
            action.setCheckable(True)
            action.setChecked(theme_name == current_theme)
            action.triggered.connect(lambda checked, name=theme_name: self._on_theme_selected(name))
            theme_group.addAction(action)

        help_menu = menu_bar.addMenu("Help")
        about_action = help_menu.addAction("About")
        about_action.triggered.connect(self._show_about)

    def _on_theme_selected(self, name: str) -> None:
        apply_theme(QApplication.instance(), name)
        save_theme(name)

    def _show_about(self) -> None:
        QMessageBox.information(
            self,
            f"About {APP_NAME}",
            f"{APP_NAME} v{APP_VERSION}\n\n"
            "A DICOM testing toolkit for PACS analysts: verification, "
            "send, query/retrieve, worklist, validation, viewing, "
            "masking, de-identification, Enhanced->Classic SOP "
            "conversion, and a local Storage SCP receiver.",
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override naming convention
        # The Storage SCP tab runs a background listener that must be shut
        # down cleanly, or the port can be left in a lingering TIME_WAIT
        # state. QTabWidget children don't get their own closeEvent when
        # the main window closes, so we stop it explicitly here.
        self.storescp_tab.shutdown()
        super().closeEvent(event)
