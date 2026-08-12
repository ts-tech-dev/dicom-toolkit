"""
config.py
=========
Central place for app-wide constants and the on-disk settings location.

Nothing DICOM-specific lives here except a few default values (default
calling AE title, default network timeout). Everything that needs to be
saved between runs (node presets, last-used folders, window size) is
written as plain JSON under the user's per-OS "app data" folder so the
packaged .exe can run from anywhere (e.g. Program Files, which is often
read-only) without needing write access next to the executable.
"""

import json
import os
import sys
from pathlib import Path

APP_NAME = "DICOM Toolkit"
APP_VERSION = "1.0.0"
APP_ORG = "PACS Analyst Tools"

# ---------------------------------------------------------------------------
# Default networking values. These are only *defaults* shown in the UI -
# every tab lets the user override AE titles, host, port, and timeout per
# operation, and saved "node presets" (core/presets.py) override these too.
# ---------------------------------------------------------------------------
DEFAULT_LOCAL_AE_TITLE = "DICOMTOOLKIT"
DEFAULT_REMOTE_AE_TITLE = "ANY-SCP"
DEFAULT_PORT = 104
DEFAULT_LOCAL_STORESCP_PORT = 11112
DEFAULT_NETWORK_TIMEOUT = 30  # seconds, applies to association/DIMSE timeouts

# Well-known DICOM UDP/TCP port note: port 104 requires elevated privileges
# on most OSes (it's a "well-known" port < 1024). We default the local
# Storage SCP to 11112 (the IANA-registered DICOM port) to avoid needing
# admin rights to run the receiver.


def _app_data_dir() -> Path:
    """
    Return a per-OS, per-user writable folder for settings/presets/logs.

    Windows:  %APPDATA%\\DicomToolkit
    macOS:    ~/Library/Application Support/DicomToolkit
    Linux:    ~/.config/DicomToolkit  (XDG_CONFIG_HOME if set)
    """
    folder_name = "DicomToolkit"

    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / folder_name
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / folder_name
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        return Path(base) / folder_name


APP_DATA_DIR = _app_data_dir()
PRESETS_FILE = APP_DATA_DIR / "node_presets.json"
SETTINGS_FILE = APP_DATA_DIR / "settings.json"
RECEIVED_FILES_DIR = APP_DATA_DIR / "received"  # default Storage SCP save folder
LOG_FILE = APP_DATA_DIR / "dicom_toolkit.log"


def ensure_app_data_dir() -> None:
    """Create the app-data folder (and the default received-files folder) if missing."""
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    RECEIVED_FILES_DIR.mkdir(parents=True, exist_ok=True)


class Settings:
    """
    Tiny JSON-backed key/value store for anything that should persist
    between runs but isn't a node preset (last-used folder, window
    geometry, default AE title, etc). Deliberately simple - this is a
    testing tool, not a product with a real config schema/migrations.
    """

    def __init__(self, path: Path = SETTINGS_FILE):
        self._path = path
        self._data: dict = {}
        self.load()

    def load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                # Corrupt or unreadable settings file: fall back to defaults
                # rather than crashing the whole app on startup.
                self._data = {}
        else:
            self._data = {}

    def save(self) -> None:
        ensure_app_data_dir()
        self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value
        self.save()


# Common transfer syntaxes offered in dropdowns throughout the UI.
# (Kept as plain UID strings here so this module doesn't need pydicom.)
COMMON_TRANSFER_SYNTAXES = {
    "Implicit VR Little Endian": "1.2.840.10008.1.2",
    "Explicit VR Little Endian": "1.2.840.10008.1.2.1",
    "Explicit VR Big Endian": "1.2.840.10008.1.2.2",
    "JPEG Baseline (Process 1)": "1.2.840.10008.1.2.4.50",
    "JPEG Lossless, Non-Hierarchical (Process 14)": "1.2.840.10008.1.2.4.70",
    "JPEG-LS Lossless": "1.2.840.10008.1.2.4.80",
    "JPEG 2000 Lossless": "1.2.840.10008.1.2.4.90",
    "JPEG 2000": "1.2.840.10008.1.2.4.91",
    "RLE Lossless": "1.2.840.10008.1.2.5",
}
