"""
ui/theme.py
=============
Three built-in appearances - Light, Dark, and Grey - applied as a QPalette
on top of the "Fusion" style. Fusion is used for all three (including
Light) because it's the only style Qt guarantees will actually honor a
custom QPalette consistently across platforms; native styles can ignore
palette colors for some widgets.

The active theme is persisted via config.Settings under the "theme" key
so it's remembered between runs.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from config import Settings

THEMES = ("Light", "Dark", "Grey")
DEFAULT_THEME = "Light"
_SETTINGS_KEY = "theme"


def _palette(colors: dict[str, str]) -> QPalette:
    p = QPalette()
    p.setColor(QPalette.Window, QColor(colors["window"]))
    p.setColor(QPalette.WindowText, QColor(colors["text"]))
    p.setColor(QPalette.Base, QColor(colors["base"]))
    p.setColor(QPalette.AlternateBase, QColor(colors["alt_base"]))
    p.setColor(QPalette.ToolTipBase, QColor(colors["tooltip_base"]))
    p.setColor(QPalette.ToolTipText, QColor(colors["text"]))
    p.setColor(QPalette.Text, QColor(colors["text"]))
    p.setColor(QPalette.Button, QColor(colors["button"]))
    p.setColor(QPalette.ButtonText, QColor(colors["text"]))
    p.setColor(QPalette.BrightText, QColor("#ff5555"))
    p.setColor(QPalette.Link, QColor(colors["link"]))
    p.setColor(QPalette.Highlight, QColor(colors["highlight"]))
    p.setColor(QPalette.HighlightedText, QColor(colors["highlighted_text"]))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(colors["disabled"]))
    p.setColor(QPalette.Disabled, QPalette.WindowText, QColor(colors["disabled"]))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(colors["disabled"]))
    return p


_PALETTE_COLORS = {
    "Light": {
        "window": "#f2f2f2", "text": "#202020", "base": "#ffffff",
        "alt_base": "#f7f7f7", "tooltip_base": "#ffffe1", "button": "#ececec",
        "link": "#2a6fdb", "highlight": "#2a6fdb", "highlighted_text": "#ffffff",
        "disabled": "#a0a0a0",
    },
    "Dark": {
        "window": "#2b2b2b", "text": "#e0e0e0", "base": "#232323",
        "alt_base": "#2b2b2b", "tooltip_base": "#3a3a3a", "button": "#3c3c3c",
        "link": "#5599ff", "highlight": "#3d7bd6", "highlighted_text": "#ffffff",
        "disabled": "#707070",
    },
    "Grey": {
        "window": "#9e9e9e", "text": "#1a1a1a", "base": "#b5b5b5",
        "alt_base": "#a8a8a8", "tooltip_base": "#eeeeee", "button": "#8c8c8c",
        "link": "#1a4fa0", "highlight": "#3d7bd6", "highlighted_text": "#ffffff",
        "disabled": "#5a5a5a",
    },
}


def apply_theme(app: QApplication, name: str) -> None:
    if name not in THEMES:
        name = DEFAULT_THEME
    app.setStyle("Fusion")
    app.setPalette(_palette(_PALETTE_COLORS[name]))


def load_saved_theme() -> str:
    return Settings().get(_SETTINGS_KEY, DEFAULT_THEME)


def save_theme(name: str) -> None:
    Settings().set(_SETTINGS_KEY, name)
