"""
core/mask.py
=============
Manual pixel redaction: black out rectangular regions of a DICOM image
(e.g. burned-in patient demographics on an ultrasound or secondary
capture screen-grab) that de-identify.py can't touch because it only
knows about DICOM *tags*, not pixels.

Because we decode pixel data to redact it and there's no guarantee a
lossy/lossless re-compression encoder is available for every original
transfer syntax, masked output is always re-saved uncompressed
(Explicit VR Little Endian). That's a deliberate, documented trade-off
for a testing tool - if you need the exact original compression back,
recompress separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np
import pydicom
from pydicom.dataset import Dataset
from pydicom.uid import ExplicitVRLittleEndian


@dataclass
class Rect:
    """A redaction rectangle in pixel coordinates (0,0 = top-left of the image)."""
    x: int
    y: int
    width: int
    height: int


def apply_masks(
    ds: Dataset,
    regions: Sequence[Rect],
    frame_indices: Optional[Sequence[int]] = None,
    fill_value: int = 0,
    mark_burned_in_annotation_removed: bool = True,
) -> Dataset:
    """
    Black out `regions` on `ds`'s pixel data, in place, across the given
    frames (all frames if `frame_indices` is None and the image is
    multi-frame). Returns `ds` for convenience.
    """
    if "PixelData" not in ds:
        raise ValueError("Dataset has no PixelData to mask")

    arr = ds.pixel_array.copy()  # decode once; safe to mutate this copy

    n_frames = int(ds.get("NumberOfFrames", 1) or 1)
    is_multiframe = n_frames > 1
    targets = list(frame_indices) if frame_indices is not None else (
        list(range(n_frames)) if is_multiframe else [0]
    )

    rows, cols = ds.Rows, ds.Columns
    for frame_idx in targets:
        frame_view = arr[frame_idx] if is_multiframe else arr
        for r in regions:
            x0 = max(0, min(r.x, cols))
            y0 = max(0, min(r.y, rows))
            x1 = max(0, min(r.x + r.width, cols))
            y1 = max(0, min(r.y + r.height, rows))
            if x1 > x0 and y1 > y0:
                frame_view[y0:y1, x0:x1, ...] = fill_value

    # Re-encode as uncompressed Explicit VR Little Endian (see module
    # docstring for why we don't try to preserve the original compression).
    ds.PixelData = np.ascontiguousarray(arr).tobytes()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    if ds.get("SamplesPerPixel", 1) > 1:
        # pydicom's pixel_array decode always yields color-by-pixel
        # (interleaved) arrays, which is what we just wrote back out.
        ds.PlanarConfiguration = 0

    if mark_burned_in_annotation_removed:
        ds.BurnedInAnnotation = "NO"

    return ds


def mask_file(
    in_path: str,
    out_path: str,
    regions: Sequence[Rect],
    frame_indices: Optional[Sequence[int]] = None,
    fill_value: int = 0,
) -> Dataset:
    ds = pydicom.dcmread(in_path)
    apply_masks(ds, regions, frame_indices=frame_indices, fill_value=fill_value)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    ds.save_as(out_path, enforce_file_format=True)
    return ds
