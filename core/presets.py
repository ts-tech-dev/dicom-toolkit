"""
core/presets.py
================
Saved connection profiles ("node presets") for PACS / modalities / any
DICOM AE you test against regularly, so you don't have to retype AE
title / host / port every time you switch tabs.

Stored as a flat JSON list at config.PRESETS_FILE, e.g.:

[
  {"name": "Test PACS", "ae_title": "TESTPACS", "host": "10.0.0.5",
   "port": 104, "description": "QA PACS in the imaging VLAN"},
  ...
]
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from config import PRESETS_FILE, ensure_app_data_dir


@dataclass
class NodePreset:
    name: str
    ae_title: str
    host: str
    port: int
    description: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:
        return f"{self.name}  ({self.ae_title} @ {self.host}:{self.port})"


class PresetManager:
    """Loads/saves the list of NodePresets and keeps it in memory."""

    def __init__(self, path=PRESETS_FILE):
        self._path = path
        self._presets: List[NodePreset] = []
        self.load()

    # -- persistence ---------------------------------------------------

    def load(self) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._presets = [NodePreset(**item) for item in raw]
            except (json.JSONDecodeError, OSError, TypeError):
                self._presets = []
        else:
            self._presets = []

    def save(self) -> None:
        ensure_app_data_dir()
        payload = [p.as_dict() for p in self._presets]
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # -- CRUD ------------------------------------------------------------

    def all(self) -> List[NodePreset]:
        return list(self._presets)

    def get(self, name: str) -> Optional[NodePreset]:
        return next((p for p in self._presets if p.name == name), None)

    def add_or_update(self, preset: NodePreset) -> None:
        existing = self.get(preset.name)
        if existing is not None:
            idx = self._presets.index(existing)
            self._presets[idx] = preset
        else:
            self._presets.append(preset)
        self.save()

    def delete(self, name: str) -> None:
        self._presets = [p for p in self._presets if p.name != name]
        self.save()
