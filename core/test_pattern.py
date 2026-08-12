"""
core/test_pattern.py
=====================
Generates synthetic, non-PHI DICOM images so you can exercise C-STORE,
Storage SCP, the viewer, masking, etc. without needing real patient data.

Every generated file uses obviously-fake identifiers (PatientName
"TEST^PATTERN", PatientID starting with "TEST-") and is written as
Secondary Capture Image Storage, which makes no claim about diagnostic
quality or modality-specific semantics - it's just a carrier for test
pixel data.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Literal

import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid

PatternName = Literal["gradient", "checkerboard", "noise", "solid"]


def _make_pixels(rows: int, cols: int, pattern: PatternName, frame_index: int = 0) -> np.ndarray:
    """Return an 8-bit grayscale numpy array (rows x cols) for the given pattern."""
    if pattern == "gradient":
        # Horizontal ramp 0-255, shifted a little per frame so a multi-frame
        # test file visibly animates when scrubbed in the viewer.
        ramp = np.linspace(0, 255, cols, dtype=np.uint8)
        shift = frame_index * 4
        ramp = np.roll(ramp, shift)
        return np.tile(ramp, (rows, 1))

    if pattern == "checkerboard":
        block = max(8, rows // 16)
        yy, xx = np.indices((rows, cols))
        offset = frame_index
        board = (((yy // block) + (xx // block) + offset) % 2) * 255
        return board.astype(np.uint8)

    if pattern == "noise":
        rng = np.random.default_rng(seed=frame_index)
        return rng.integers(0, 256, size=(rows, cols), dtype=np.uint8)

    if pattern == "solid":
        value = (frame_index * 25) % 256
        return np.full((rows, cols), value, dtype=np.uint8)

    raise ValueError(f"Unknown pattern: {pattern}")


def generate_test_dicom(
    out_path: str,
    rows: int = 512,
    cols: int = 512,
    pattern: PatternName = "gradient",
    num_frames: int = 1,
    modality: str = "OT",
    patient_id: str = "TEST-000001",
    patient_name: str = "TEST^PATTERN",
    study_description: str = "DICOM Toolkit synthetic test study",
    study_instance_uid: str | None = None,
    series_instance_uid: str | None = None,
) -> Dataset:
    """
    Build and save a synthetic Secondary Capture DICOM file (single or
    multi-frame). Returns the pydicom Dataset that was written.

    `study_instance_uid` / `series_instance_uid` can be passed in so the
    caller can generate several instances that belong to the same
    study/series (e.g. to test a multi-image C-STORE or a Q/R match).
    """
    frames = [_make_pixels(rows, cols, pattern, i) for i in range(num_frames)]
    pixel_array = frames[0] if num_frames == 1 else np.stack(frames, axis=0)

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = generate_uid()

    ds = Dataset()
    ds.file_meta = file_meta
    ds.SOPClassUID = file_meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID

    now = datetime.datetime.now()
    ds.StudyDate = now.strftime("%Y%m%d")
    ds.StudyTime = now.strftime("%H%M%S")
    ds.ContentDate = ds.StudyDate
    ds.ContentTime = ds.StudyTime

    ds.PatientName = patient_name
    ds.PatientID = patient_id
    ds.PatientBirthDate = ""
    ds.PatientSex = "O"

    ds.Modality = modality
    ds.StudyInstanceUID = study_instance_uid or generate_uid()
    ds.SeriesInstanceUID = series_instance_uid or generate_uid()
    ds.StudyDescription = study_description
    ds.SeriesDescription = f"Synthetic {pattern} pattern"
    ds.SeriesNumber = 1
    ds.InstanceNumber = 1
    ds.StudyID = "1"
    ds.AccessionNumber = ""
    ds.ConversionType = "SYN"  # Synthetic source, not a real digitized capture

    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.Rows = rows
    ds.Columns = cols
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    if num_frames > 1:
        ds.NumberOfFrames = num_frames

    ds.PixelData = pixel_array.tobytes()

    ds.is_little_endian = True
    ds.is_implicit_VR = False

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    ds.save_as(out_path, enforce_file_format=True)
    return ds
