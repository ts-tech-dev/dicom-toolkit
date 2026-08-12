"""
core/enhanced_convert.py
=========================
Splits an Enhanced multi-frame MR/CT/PET Image (one SOP instance holding
every frame's pixel data plus per-frame geometry/timing in "Functional
Groups" sequences) into classic single-frame instances - one file per
frame, each a plain MR/CT/PET Image Storage object.

Why this exists: a lot of legacy PACS/viewers/workstations (and some
modality interfaces) only understand the "classic" single-frame image
SOP classes and choke on Enhanced Multi-frame objects. This lets you
take an Enhanced series and produce a classic-compatible copy for
interoperability testing, the same job tools like dcm4che's `emf2sf`
do.

Scope: MR, CT and PET Enhanced Image Storage - the three Enhanced IODs
built on the common "Multi-frame Functional Groups" module
(PS3.3 C.7.6.16), which is what makes one splitting routine workable for
all three. Enhanced US Volume and other enhanced/volumetric SOP classes
use a different module structure and aren't handled here.

Per-frame data lives in one of two places inside the source dataset:
  - SharedFunctionalGroupsSequence (5200,9229): one item, applies to
    every frame (e.g. pixel spacing that doesn't change slice-to-slice).
  - PerFrameFunctionalGroupsSequence (5200,9230): one item per frame,
    overrides/extends the shared item for that specific frame.
We merge shared + per-frame (per-frame wins) before mapping into the
classic flat tags each functional group corresponds to.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import (
    CTImageStorage,
    EnhancedCTImageStorage,
    EnhancedMRImageStorage,
    EnhancedPETImageStorage,
    ExplicitVRLittleEndian,
    MRImageStorage,
    PositronEmissionTomographyImageStorage,
    generate_uid,
)

LogFn = Callable[[str], None]


def _noop_log(_msg: str) -> None:
    pass


_ENHANCED_TO_CLASSIC = {
    str(EnhancedMRImageStorage): (MRImageStorage, "MR"),
    str(EnhancedCTImageStorage): (CTImageStorage, "CT"),
    str(EnhancedPETImageStorage): (PositronEmissionTomographyImageStorage, "PET"),
}

# Top-level tags that describe the *object/patient/study/equipment*, not an
# individual frame - these get copied as-is onto every output instance.
_COMMON_COPY_KEYWORDS = [
    "PatientName", "PatientID", "PatientBirthDate", "PatientSex", "PatientAge",
    "StudyInstanceUID", "StudyID", "StudyDate", "StudyTime", "StudyDescription",
    "AccessionNumber", "ReferringPhysicianName",
    "Modality", "Manufacturer", "ManufacturerModelName", "InstitutionName",
    "StationName", "SoftwareVersions", "MagneticFieldStrength", "DeviceSerialNumber",
    "PatientPosition", "BodyPartExamined",
    "Rows", "Columns", "BitsAllocated", "BitsStored", "HighBit",
    "PixelRepresentation", "SamplesPerPixel", "PhotometricInterpretation",
    "PlanarConfiguration",
]


def _merge_functional_group_item(shared: Dataset, per_frame: Dataset) -> Dataset:
    """Combine one shared-group item and one per-frame-group item; per-frame wins on conflicts."""
    merged = Dataset()
    for elem in shared:
        merged.add(elem)
    for elem in per_frame:
        merged.add(elem)
    return merged


def _apply_functional_groups(fg: Dataset, out: Dataset) -> None:
    """Map the functional-group sub-sequences we understand onto classic flat tags."""

    if "PixelMeasuresSequence" in fg and fg.PixelMeasuresSequence:
        pm = fg.PixelMeasuresSequence[0]
        if "PixelSpacing" in pm:
            out.PixelSpacing = pm.PixelSpacing
        if "SliceThickness" in pm:
            out.SliceThickness = pm.SliceThickness

    if "PlanePositionSequence" in fg and fg.PlanePositionSequence:
        pp = fg.PlanePositionSequence[0]
        if "ImagePositionPatient" in pp:
            out.ImagePositionPatient = pp.ImagePositionPatient

    if "PlaneOrientationSequence" in fg and fg.PlaneOrientationSequence:
        po = fg.PlaneOrientationSequence[0]
        if "ImageOrientationPatient" in po:
            out.ImageOrientationPatient = po.ImageOrientationPatient

    if "FrameVOILUTSequence" in fg and fg.FrameVOILUTSequence:
        voi = fg.FrameVOILUTSequence[0]
        if "WindowCenter" in voi:
            out.WindowCenter = voi.WindowCenter
        if "WindowWidth" in voi:
            out.WindowWidth = voi.WindowWidth

    if "PixelValueTransformationSequence" in fg and fg.PixelValueTransformationSequence:
        pvt = fg.PixelValueTransformationSequence[0]
        if "RescaleIntercept" in pvt:
            out.RescaleIntercept = pvt.RescaleIntercept
        if "RescaleSlope" in pvt:
            out.RescaleSlope = pvt.RescaleSlope
        if "RescaleType" in pvt:
            out.RescaleType = pvt.RescaleType

    if "FrameContentSequence" in fg and fg.FrameContentSequence:
        fc = fg.FrameContentSequence[0]
        if "StackID" in fc:
            out.StackID = fc.StackID
        if "InStackPositionNumber" in fc:
            out.InStackPositionNumber = fc.InStackPositionNumber
        if "TemporalPositionIndex" in fc:
            out.TemporalPositionIdentifier = fc.TemporalPositionIndex
        if "FrameAcquisitionDateTime" in fc and fc.FrameAcquisitionDateTime:
            dt = str(fc.FrameAcquisitionDateTime)
            out.AcquisitionDate = dt[:8]
            out.AcquisitionTime = dt[8:]

    # MR-specific functional groups.
    if "MREchoSequence" in fg and fg.MREchoSequence:
        echo = fg.MREchoSequence[0]
        if "EffectiveEchoTime" in echo:
            out.EchoTime = echo.EffectiveEchoTime
    if "MRTimingAndRelatedParametersSequence" in fg and fg.MRTimingAndRelatedParametersSequence:
        timing = fg.MRTimingAndRelatedParametersSequence[0]
        if "RepetitionTime" in timing:
            out.RepetitionTime = timing.RepetitionTime
        if "FlipAngle" in timing:
            out.FlipAngle = timing.FlipAngle

    # CT-specific functional groups.
    if "CTExposureSequence" in fg and fg.CTExposureSequence:
        exposure = fg.CTExposureSequence[0]
        if "ExposureTimeInms" in exposure:
            out.ExposureTime = exposure.ExposureTimeInms
        if "XRayTubeCurrentInmA" in exposure:
            out.XRayTubeCurrent = exposure.XRayTubeCurrentInmA


def convert_enhanced_to_classic(ds: Dataset, log: LogFn = _noop_log) -> List[Dataset]:
    """
    Split one Enhanced MR/CT/PET Image dataset into a list of classic
    single-frame Datasets (one per frame, all sharing one new
    SeriesInstanceUID). Does not write anything to disk - see
    convert_file() for that.
    """
    sop_class = str(ds.SOPClassUID)
    if sop_class not in _ENHANCED_TO_CLASSIC:
        raise ValueError(
            f"SOPClassUID {sop_class} is not a supported Enhanced MR/CT/PET Image "
            f"Storage class - nothing to convert."
        )
    classic_sop_class, modality_label = _ENHANCED_TO_CLASSIC[sop_class]

    n_frames = int(ds.get("NumberOfFrames", 1) or 1)
    log(f"Converting Enhanced {modality_label} Image ({n_frames} frame(s)) to classic {modality_label} Image Storage ...")

    shared_items = ds.get("SharedFunctionalGroupsSequence", [Dataset()])
    shared = shared_items[0] if shared_items else Dataset()
    per_frame_seq = ds.get("PerFrameFunctionalGroupsSequence", None)
    per_frame_items = list(per_frame_seq) if per_frame_seq else [Dataset()] * n_frames

    pixel_array = ds.pixel_array  # shape: (frames, rows, cols[, samples])

    new_series_uid = generate_uid()
    base_series_number = int(ds.get("SeriesNumber", 1) or 1)

    outputs: List[Dataset] = []
    for i in range(n_frames):
        out = Dataset()
        file_meta = FileMetaDataset()
        file_meta.MediaStorageSOPClassUID = classic_sop_class
        file_meta.MediaStorageSOPInstanceUID = generate_uid()
        file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        file_meta.ImplementationClassUID = generate_uid()
        out.file_meta = file_meta

        out.SOPClassUID = classic_sop_class
        out.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID

        for keyword in _COMMON_COPY_KEYWORDS:
            if keyword in ds:
                setattr(out, keyword, getattr(ds, keyword))

        out.SeriesInstanceUID = new_series_uid
        out.SeriesNumber = base_series_number + 900  # offset so it doesn't collide with the original series
        out.SeriesDescription = f"{ds.get('SeriesDescription', '')} (converted from Enhanced {modality_label})".strip()
        out.InstanceNumber = i + 1
        out.ImageType = ["DERIVED", "SECONDARY"]

        fg_item = per_frame_items[i] if i < len(per_frame_items) else Dataset()
        merged_fg = _merge_functional_group_item(shared, fg_item)
        _apply_functional_groups(merged_fg, out)

        frame_pixels = pixel_array[i] if n_frames > 1 else pixel_array
        out.PixelData = np.ascontiguousarray(frame_pixels).tobytes()
        out.is_little_endian = True
        out.is_implicit_VR = False

        outputs.append(out)

    log(f"Produced {len(outputs)} classic {modality_label} Image instance(s) in new series {new_series_uid}")
    return outputs


def convert_file(in_path: str, out_dir: str, log: LogFn = _noop_log) -> List[str]:
    """Read `in_path`, convert it, and save each resulting frame as its own file under `out_dir`."""
    ds = pydicom.dcmread(in_path)
    outputs = convert_enhanced_to_classic(ds, log=log)

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    written: List[str] = []
    for out_ds in outputs:
        fp = out_path / f"{out_ds.SOPInstanceUID}.dcm"
        out_ds.save_as(fp, enforce_file_format=True)
        written.append(str(fp))
    return written
