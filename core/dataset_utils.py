"""
core/dataset_utils.py
======================
A Qt tree model over a pydicom Dataset, used by the Dataset Editor tab
to display every tag (including nested sequences) and let you edit
values or add/delete elements - useful for hand-crafting edge-case test
files (bad VRs, missing tags, oddball values) that the other tools
would otherwise refuse to create for you.
"""

from __future__ import annotations

from typing import Any, List, Optional

from pydicom.dataelem import DataElement
from pydicom.dataset import Dataset
from pydicom.tag import Tag

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt

_VALUE_COLUMN = 5

# VRs whose values should be coerced from the edit box's text into a
# number (or list of numbers, for multi-valued elements) rather than kept
# as a plain string - otherwise pydicom will reject the value when the
# file is saved.
_NUMERIC_VRS = {"US", "SS", "UL", "SL", "FL", "FD", "DS", "IS"}


def _coerce_value_for_vr(vr: str, raw: str) -> Any:
    parts = raw.split("\\") if "\\" in raw else [raw]

    def convert_one(text: str):
        if vr in ("US", "SS", "UL", "SL", "IS"):
            return int(text)
        if vr in ("FL", "FD", "DS"):
            return float(text)
        return text

    if vr in _NUMERIC_VRS:
        try:
            values = [convert_one(p) for p in parts]
        except ValueError:
            return raw  # let pydicom raise a clearer error on save than we would here
        return values[0] if len(values) == 1 else values

    return parts[0] if len(parts) == 1 else parts


class _Node:
    """One row in the tree: either a DataElement, or (for SQ items) a nested Dataset 'Item'."""

    __slots__ = ("dataset", "element", "parent", "children", "row_in_parent")

    def __init__(self, dataset: Optional[Dataset], element: Optional[DataElement], parent: Optional["_Node"]):
        self.dataset = dataset
        self.element = element
        self.parent = parent
        self.children: List["_Node"] = []
        self.row_in_parent = 0


def _build_children(node: _Node) -> None:
    ds = node.dataset
    if ds is None:
        return
    for i, elem in enumerate(ds):
        child = _Node(dataset=ds, element=elem, parent=node)
        child.row_in_parent = i
        node.children.append(child)
        if elem.VR == "SQ" and elem.value:
            for j, item in enumerate(elem.value):
                item_node = _Node(dataset=item, element=None, parent=child)
                item_node.row_in_parent = j
                child.children.append(item_node)
                _build_children(item_node)


def _value_to_display(elem: DataElement) -> str:
    if elem.VR == "SQ":
        n = len(elem.value) if elem.value else 0
        return f"<Sequence, {n} item(s)>"
    if elem.VR in ("OB", "OW", "OF", "OD", "UN") and elem.value is not None:
        length = len(elem.value) if hasattr(elem.value, "__len__") else 0
        return f"<binary data, {length} bytes>"
    try:
        return str(elem.value)
    except Exception:
        return "<unreadable>"


class DicomTreeModel(QAbstractItemModel):
    COLUMNS = ["Tag", "Keyword", "VR", "VM", "Length", "Value"]

    def __init__(self, dataset: Dataset, parent=None):
        super().__init__(parent)
        self.dataset = dataset
        self._root = _Node(dataset=dataset, element=None, parent=None)
        _build_children(self._root)

    def _rebuild(self) -> None:
        self.beginResetModel()
        self._root = _Node(dataset=self.dataset, element=None, parent=None)
        _build_children(self._root)
        self.endResetModel()

    # -- required QAbstractItemModel overrides -----------------------------

    def index(self, row, column, parent=QModelIndex()):
        parent_node = parent.internalPointer() if parent.isValid() else self._root
        if row < 0 or row >= len(parent_node.children):
            return QModelIndex()
        return self.createIndex(row, column, parent_node.children[row])

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        node: _Node = index.internalPointer()
        parent_node = node.parent
        if parent_node is None or parent_node is self._root:
            return QModelIndex()
        grandparent = parent_node.parent or self._root
        row = grandparent.children.index(parent_node)
        return self.createIndex(row, 0, parent_node)

    def rowCount(self, parent=QModelIndex()):
        parent_node = parent.internalPointer() if parent.isValid() else self._root
        return len(parent_node.children)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.COLUMNS[section]
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        node: _Node = index.internalPointer()
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == _VALUE_COLUMN and node.element is not None and node.element.VR != "SQ":
            return base | Qt.ItemIsEditable
        return base

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role not in (Qt.DisplayRole, Qt.EditRole):
            return None
        node: _Node = index.internalPointer()
        col = index.column()

        if node.element is None:
            return f"Item {node.row_in_parent}" if col == 0 else ""

        elem = node.element
        if col == 0:
            return f"({elem.tag.group:04X},{elem.tag.element:04X})"
        if col == 1:
            return elem.keyword or "(unknown)"
        if col == 2:
            return elem.VR
        if col == 3:
            return str(elem.VM)
        if col == 4:
            try:
                return str(len(elem.value)) if elem.value is not None else "0"
            except TypeError:
                return "1"
        if col == _VALUE_COLUMN:
            return _value_to_display(elem)
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid() or index.column() != _VALUE_COLUMN:
            return False
        node: _Node = index.internalPointer()
        if node.element is None or node.element.VR == "SQ":
            return False
        try:
            coerced = _coerce_value_for_vr(node.element.VR, str(value))
            node.dataset[node.element.tag].value = coerced
        except Exception:
            return False
        self.dataChanged.emit(index, index, [role])
        return True

    # -- editing helpers used by the Dataset Editor tab ---------------------

    def add_element(self, parent_index: QModelIndex, tag_str: str, vr: str, raw_value: str) -> bool:
        """Add a new element ('GGGG,EEEE' tag string) under the dataset at `parent_index` (or the root)."""
        parent_node = parent_index.internalPointer() if parent_index.isValid() else self._root
        target_ds = parent_node.dataset if parent_node.dataset is not None else self.dataset
        try:
            group_str, elem_str = tag_str.replace("(", "").replace(")", "").split(",")
            tag = Tag(int(group_str, 16), int(elem_str, 16))
            value = _coerce_value_for_vr(vr, raw_value)
            target_ds.add_new(tag, vr, value)
        except Exception:
            return False
        self._rebuild()
        return True

    def delete_element(self, index: QModelIndex) -> bool:
        if not index.isValid():
            return False
        node: _Node = index.internalPointer()
        if node.element is None or node.dataset is None:
            return False
        try:
            del node.dataset[node.element.tag]
        except Exception:
            return False
        self._rebuild()
        return True
