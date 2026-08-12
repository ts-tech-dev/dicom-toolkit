"""
core/validate.py
=================
Checks a DICOM file for structural/content problems that commonly bite
PACS integrations: missing required tags, inconsistent pixel data
geometry, bad UIDs, malformed dates/times, undecodable pixel data, etc.

This is deliberately a *practical* testing-tool validator, not a full
IOD/module conformance checker against every SOP Class's exact tag
table (that would require shipping and maintaining PS3.3's module
tables). It catches the errors that actually break real-world
transfers and viewers.

Each problem found is a Finding with a severity:
    ERROR    - the file is broken / not standard-conformant
    WARNING  - technically allowed but likely to cause problems
    INFO     - notable but harmless (e.g. private tags present)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import pydicom
from pydicom.dataset import Dataset
from pydicom.errors import InvalidDicomError
from pydicom.uid import UID

Severity = str  # "ERROR" | "WARNING" | "INFO"


@dataclass
class Finding:
    severity: Severity
    message: str
    tag: Optional[str] = None


@dataclass
class ValidationReport:
    path: str
    findings: List[Finding] = field(default_factory=list)

    def add(self, severity: Severity, message: str, tag: Optional[str] = None) -> None:
        self.findings.append(Finding(severity, message, tag))

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "ERROR")

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "WARNING")

    @property
    def is_valid(self) -> bool:
        """True if no ERROR-level findings (WARNING/INFO are still worth reading)."""
        return self.error_count == 0


# Tags that should be present on essentially every DICOM object
# regardless of SOP Class/IOD, keyed to their DICOM Type (1 = required
# and must have a value, 2 = required but may be empty).
_REQUIRED_TYPE1 = ["SOPClassUID", "SOPInstanceUID", "StudyInstanceUID", "SeriesInstanceUID"]
_REQUIRED_TYPE2 = ["PatientID", "PatientName", "Modality", "StudyDate", "StudyTime"]

_VALID_PHOTOMETRIC = {
    "MONOCHROME1", "MONOCHROME2", "PALETTE COLOR", "RGB",
    "YBR_FULL", "YBR_FULL_422", "YBR_PARTIAL_420", "YBR_PARTIAL_422",
    "YBR_ICT", "YBR_RCT",
}

_UID_RE = re.compile(r"^\d+(\.\d+)*$")
_DA_RE = re.compile(r"^\d{8}$")  # YYYYMMDD
_TM_RE = re.compile(r"^\d{2}(\d{2}(\d{2}(\.\d{1,6})?)?)?$")  # HH[MM[SS[.FFFFFF]]]


def _check_uid(report: ValidationReport, keyword: str, value, severity: Severity = "ERROR") -> None:
    if value in (None, ""):
        return  # presence is checked separately; this only validates format
    s = str(value)
    if len(s) > 64:
        report.add(severity, f"{keyword} is {len(s)} chars, exceeds the 64-char UID limit", keyword)
    if not _UID_RE.match(s):
        report.add(severity, f"{keyword} '{s}' is not a well-formed UID (digits and dots only)", keyword)
    if s.endswith("."):
        report.add(severity, f"{keyword} '{s}' ends with a trailing dot", keyword)


def _check_date(report: ValidationReport, keyword: str, value) -> None:
    if not value:
        return
    if not _DA_RE.match(str(value)):
        report.add("WARNING", f"{keyword} '{value}' is not in DA format (YYYYMMDD)", keyword)


def _check_time(report: ValidationReport, keyword: str, value) -> None:
    if not value:
        return
    if not _TM_RE.match(str(value)):
        report.add("WARNING", f"{keyword} '{value}' is not in TM format (HHMMSS.FFFFFF)", keyword)


def _check_required_tags(report: ValidationReport, ds: Dataset) -> None:
    for keyword in _REQUIRED_TYPE1:
        if keyword not in ds:
            report.add("ERROR", f"Missing required tag {keyword} (Type 1)", keyword)
        elif getattr(ds, keyword, None) in (None, ""):
            report.add("ERROR", f"{keyword} is present but empty (Type 1 requires a value)", keyword)

    for keyword in _REQUIRED_TYPE2:
        if keyword not in ds:
            report.add("WARNING", f"Missing tag {keyword} (Type 2 - should be present, may be empty)", keyword)


def _check_file_meta(report: ValidationReport, ds: Dataset) -> None:
    fm = getattr(ds, "file_meta", None)
    if fm is None or len(fm) == 0:
        report.add("ERROR", "No File Meta Information group (0002,xxxx) - file may be missing the DICOM file header")
        return

    for keyword in ("MediaStorageSOPClassUID", "MediaStorageSOPInstanceUID", "TransferSyntaxUID"):
        if keyword not in fm:
            report.add("ERROR", f"File meta is missing {keyword}", keyword)

    if "MediaStorageSOPClassUID" in fm and "SOPClassUID" in ds:
        if str(fm.MediaStorageSOPClassUID) != str(ds.SOPClassUID):
            report.add(
                "ERROR",
                "File meta MediaStorageSOPClassUID does not match dataset SOPClassUID",
                "SOPClassUID",
            )
    if "MediaStorageSOPInstanceUID" in fm and "SOPInstanceUID" in ds:
        if str(fm.MediaStorageSOPInstanceUID) != str(ds.SOPInstanceUID):
            report.add(
                "ERROR",
                "File meta MediaStorageSOPInstanceUID does not match dataset SOPInstanceUID",
                "SOPInstanceUID",
            )

    ts = fm.get("TransferSyntaxUID")
    if ts is not None and not UID(str(ts)).is_valid:
        report.add("ERROR", f"TransferSyntaxUID '{ts}' is not a valid/known transfer syntax", "TransferSyntaxUID")


def _check_pixel_geometry(report: ValidationReport, ds: Dataset) -> None:
    if "PixelData" not in ds:
        return  # not an image object - nothing pixel-related to check

    for keyword in ("Rows", "Columns", "BitsAllocated", "BitsStored", "HighBit",
                     "SamplesPerPixel", "PixelRepresentation", "PhotometricInterpretation"):
        if keyword not in ds:
            report.add("ERROR", f"PixelData present but missing required image tag {keyword}", keyword)

    if "PhotometricInterpretation" in ds and ds.PhotometricInterpretation not in _VALID_PHOTOMETRIC:
        report.add(
            "WARNING",
            f"Unrecognized PhotometricInterpretation '{ds.PhotometricInterpretation}'",
            "PhotometricInterpretation",
        )

    if "BitsAllocated" in ds and "BitsStored" in ds:
        if ds.BitsStored > ds.BitsAllocated:
            report.add("ERROR", f"BitsStored ({ds.BitsStored}) > BitsAllocated ({ds.BitsAllocated})", "BitsStored")
    if "BitsStored" in ds and "HighBit" in ds:
        if ds.HighBit != ds.BitsStored - 1:
            report.add(
                "WARNING",
                f"HighBit ({ds.HighBit}) is not BitsStored-1 ({ds.BitsStored - 1}) - unusual but not always wrong",
                "HighBit",
            )

    samples = ds.get("SamplesPerPixel", 1)
    if samples > 1 and "PlanarConfiguration" not in ds:
        report.add("WARNING", "SamplesPerPixel > 1 but PlanarConfiguration is missing", "PlanarConfiguration")

    # Raw pixel data length check - only meaningful for *uncompressed*
    # transfer syntaxes. Compressed data is stored as encapsulated
    # fragments and won't match rows*cols*bytes, so skip it there.
    ts = getattr(ds.file_meta, "TransferSyntaxUID", None) if hasattr(ds, "file_meta") else None
    is_compressed = bool(ts) and UID(str(ts)).is_compressed
    if not is_compressed and all(k in ds for k in ("Rows", "Columns", "BitsAllocated", "SamplesPerPixel")):
        frames = int(ds.get("NumberOfFrames", 1) or 1)
        bytes_per_sample = (ds.BitsAllocated + 7) // 8
        expected = ds.Rows * ds.Columns * ds.SamplesPerPixel * bytes_per_sample * frames
        actual = len(ds.PixelData)
        # DICOM pads odd-length pixel data with a single trailing zero byte.
        if actual != expected and actual != expected + (expected % 2):
            report.add(
                "ERROR",
                f"PixelData length ({actual} bytes) does not match expected size from "
                f"Rows*Columns*Samples*BytesPerSample*Frames ({expected} bytes)",
                "PixelData",
            )

    # Try to actually decode the pixel data (catches codec-level corruption
    # and missing compression handlers). A missing optional codec plugin
    # (e.g. pylibjpeg for JPEG2000) is reported as INFO, not ERROR, since
    # it's an environment limitation, not proof the file is broken.
    try:
        _ = ds.pixel_array  # noqa: F841 - triggers full decode
    except Exception as exc:  # noqa: BLE001 - we want to catch and report *any* decode failure
        text = str(exc)
        if "pylibjpeg" in text or "gdcm" in text.lower() or "no available" in text.lower():
            report.add(
                "INFO",
                f"Could not verify pixel data decodes correctly - optional codec plugin missing ({text})",
                "PixelData",
            )
        else:
            report.add("ERROR", f"Pixel data failed to decode: {text}", "PixelData")


def _check_odd_length_and_charset(report: ValidationReport, ds: Dataset) -> None:
    scs = ds.get("SpecificCharacterSet", None)
    has_non_ascii = False
    try:
        name = str(ds.get("PatientName", ""))
        name.encode("ascii")
    except UnicodeEncodeError:
        has_non_ascii = True
    if has_non_ascii and not scs:
        report.add(
            "WARNING",
            "PatientName contains non-ASCII characters but SpecificCharacterSet is not set",
            "SpecificCharacterSet",
        )

    private_count = sum(1 for elem in ds if elem.tag.is_private)
    if private_count:
        report.add("INFO", f"{private_count} private tag(s) present", None)


def validate_file(path: str) -> ValidationReport:
    """Run every check against a single file and return the findings."""
    report = ValidationReport(path=path)

    p = Path(path)
    if not p.exists():
        report.add("ERROR", "File does not exist")
        return report

    with open(p, "rb") as fh:
        header = fh.read(132)
    has_preamble = len(header) == 132 and header[128:132] == b"DICM"
    if not has_preamble:
        report.add(
            "WARNING",
            "No 128-byte preamble / 'DICM' magic found - not a standard Part 10 file "
            "(will attempt to read anyway; some scanners write bare datasets)",
        )

    try:
        ds = pydicom.dcmread(str(p), force=True)
    except InvalidDicomError as exc:
        report.add("ERROR", f"Could not parse as DICOM: {exc}")
        return report
    except Exception as exc:  # noqa: BLE001 - report parse failures as findings, not crashes
        report.add("ERROR", f"Unexpected error reading file: {exc}")
        return report

    _check_file_meta(report, ds)
    _check_required_tags(report, ds)
    for keyword in ("SOPClassUID", "SOPInstanceUID", "StudyInstanceUID", "SeriesInstanceUID"):
        if keyword in ds:
            _check_uid(report, keyword, getattr(ds, keyword))
    _check_date(report, "StudyDate", ds.get("StudyDate"))
    _check_time(report, "StudyTime", ds.get("StudyTime"))
    _check_pixel_geometry(report, ds)
    _check_odd_length_and_charset(report, ds)

    return report


def validate_files(paths: List[str]) -> List[ValidationReport]:
    """Batch helper - validates every path and returns one report each."""
    return [validate_file(p) for p in paths]
