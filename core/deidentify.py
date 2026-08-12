"""
core/deidentify.py
===================
De-identifies DICOM files following the shape of the DICOM PS3.15 Annex E
"Basic Application Level Confidentiality Profile": known PHI-bearing text
tags are removed or blanked, all patient-identifying UIDs are replaced
with new ones (consistently, so relationships between Study/Series/
Instance/Frame-of-Reference are preserved within a batch), overlay/curve
data is stripped (it can contain burned-in text), and the required
"this file has been de-identified" tags are added.

This is a practical subset of the full PS3.15 tag table (~50 of the most
commonly-PHI-bearing tags) rather than an exhaustive ~450-entry
implementation - good enough for testing/QA workflows, but always spot
-check output before treating it as safe to share outside your
environment.

Use a DeidentifySession to process more than one file so that the same
original UID/PatientID always maps to the same replacement value across
the whole batch (otherwise a de-identified study would end up with each
image as its own orphaned "study").
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Literal, Optional

import pydicom
from pydicom.dataset import Dataset
from pydicom.uid import generate_uid

DateHandling = Literal["remove", "shift", "keep"]

# ---------------------------------------------------------------------------
# Tags handled explicitly. "X" = remove entirely, "Z" = keep the tag but
# blank its value (mirrors PS3.15's X/Z action codes). PatientName/PatientID
# are handled specially so they can be replaced with a consistent pseudonym
# instead of just blanked.
# ---------------------------------------------------------------------------
_ACTION_X = [
    "OtherPatientIDs", "OtherPatientNames", "PatientBirthName", "PatientAddress",
    "PatientTelephoneNumbers", "PatientMotherBirthName", "EthnicGroup", "PatientComments",
    "ReferringPhysicianAddress", "ReferringPhysicianTelephoneNumbers", "PhysiciansOfRecord",
    "PerformingPhysicianName", "NameOfPhysiciansReadingStudy", "OperatorsName",
    "RequestingPhysician", "InstitutionName", "InstitutionAddress", "StationName",
    "DeviceSerialNumber", "CurrentPatientLocation", "PatientInstitutionResidence",
    "AdditionalPatientHistory", "MilitaryRank", "RequestAttributesSequence",
    "PersonName", "IssuerOfPatientID",
]
_ACTION_Z = [
    "ReferringPhysicianName", "AccessionNumber", "StudyID",
]

_DATE_TAGS = ["StudyDate", "SeriesDate", "AcquisitionDate", "ContentDate", "PatientBirthDate"]
_TIME_TAGS = ["StudyTime", "SeriesTime", "AcquisitionTime", "ContentTime", "PatientBirthTime"]

_DESCRIPTION_TAGS = ["StudyDescription", "SeriesDescription", "ProtocolName"]

# UID-valued tags that identify *equipment/software/coding schemes*, not a
# patient, so they must NOT be remapped - only patient/study/series/
# instance-identifying UIDs should change.
_KEEP_UID_KEYWORDS = {
    "SOPClassUID", "TransferSyntaxUID", "ImplementationClassUID",
    "MediaStorageSOPClassUID", "CodingSchemeUID", "SpecificCharacterSet",
    "ReferencedSOPClassUID",  # keep the *class* reference even if the instance ref is remapped
}

# Overlay data (group 0x6000-0x601E, even groups) and retired curve data
# (group 0x5000-0x50FE) can carry burned-in annotations/graphics and are
# stripped outright rather than inspected.
def _is_overlay_or_curve_group(group: int) -> bool:
    return (0x6000 <= group <= 0x60FF) or (0x5000 <= group <= 0x50FF)


@dataclass
class DeidentifyOptions:
    remove_private_tags: bool = True
    remove_overlays_curves: bool = True
    keep_descriptions: bool = True  # keep StudyDescription/SeriesDescription/ProtocolName
    date_handling: DateHandling = "remove"
    date_shift_days: int = 0  # only used when date_handling == "shift"
    patient_id_prefix: str = "ANON"  # generated pseudonyms look like ANON-3F9A1C
    fixed_patient_id: Optional[str] = None  # if set, every file gets this exact PatientID
    fixed_patient_name: Optional[str] = None  # if set, every file gets this exact PatientName


class DeidentifySession:
    """
    Holds the UID/PatientID remapping tables for one de-identification run
    so a whole folder of files (one study, or many) stays internally
    consistent - the same original StudyInstanceUID or PatientID always
    maps to the same replacement across every file processed by this
    session.
    """

    def __init__(self):
        self._uid_map: Dict[str, str] = {}
        self._patient_id_map: Dict[str, str] = {}
        self._patient_name_map: Dict[str, str] = {}

    def _new_uid(self, original: str) -> str:
        if original not in self._uid_map:
            self._uid_map[original] = generate_uid()
        return self._uid_map[original]

    def _pseudonym_id(self, original: str, prefix: str) -> str:
        if original not in self._patient_id_map:
            # Deterministic-looking but non-reversible short pseudonym,
            # derived from a hash so re-running with the *same* original ID
            # in a fresh session would still be reproducible if desired,
            # while not encoding the original value in any recoverable way.
            digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:8].upper()
            self._patient_id_map[original] = f"{prefix}-{digest}"
        return self._patient_id_map[original]

    def _pseudonym_name(self, original: str) -> str:
        if original not in self._patient_name_map:
            index = len(self._patient_name_map) + 1
            self._patient_name_map[original] = f"ANONYMOUS^{index:04d}"
        return self._patient_name_map[original]

    # -- core transform ---------------------------------------------------

    def deidentify_dataset(self, ds: Dataset, options: DeidentifyOptions) -> Dataset:
        """Mutates and returns `ds`. Caller is responsible for saving it."""

        # 1. Patient identity - replace with a consistent pseudonym (or a
        #    fixed value if the caller supplied one for this whole batch).
        original_id = str(ds.get("PatientID", ""))
        if options.fixed_patient_id:
            ds.PatientID = options.fixed_patient_id
        elif original_id:
            ds.PatientID = self._pseudonym_id(original_id, options.patient_id_prefix)

        original_name = str(ds.get("PatientName", ""))
        if options.fixed_patient_name:
            ds.PatientName = options.fixed_patient_name
        elif original_name:
            ds.PatientName = self._pseudonym_name(original_name)

        # 2. Explicit remove (X) / blank (Z) tag lists.
        for keyword in _ACTION_X:
            if keyword in ds:
                delattr(ds, keyword)
        for keyword in _ACTION_Z:
            if keyword in ds:
                setattr(ds, keyword, "")

        if not options.keep_descriptions:
            for keyword in _DESCRIPTION_TAGS:
                if keyword in ds:
                    setattr(ds, keyword, "")

        # 3. Dates/times.
        if options.date_handling == "remove":
            for keyword in _DATE_TAGS + _TIME_TAGS:
                if keyword in ds:
                    setattr(ds, keyword, "")
        elif options.date_handling == "shift":
            self._shift_dates(ds, options.date_shift_days)
        # "keep" -> leave dates/times untouched

        # 4. Remap every UID-valued element that identifies this
        #    patient/study/series/instance/frame-of-reference, consistently,
        #    rather than relying on a hardcoded tag list - anything with
        #    VR "UI" is a candidate unless it's an equipment/class/coding
        #    UID (see _KEEP_UID_KEYWORDS).
        for elem in ds:
            if elem.VR == "UI" and elem.keyword not in _KEEP_UID_KEYWORDS and elem.value:
                elem.value = self._new_uid(str(elem.value))

        # 5. Overlay/curve data - can contain burned-in graphics/text.
        if options.remove_overlays_curves:
            for tag in [t for t in ds.keys() if _is_overlay_or_curve_group(t.group)]:
                del ds[tag]

        # 6. Private tags - vendor-specific, unknown content, drop by default.
        if options.remove_private_tags:
            ds.remove_private_tags()

        # 7. Recurse into sequences (e.g. Referenced Study/Series Sequence,
        #    Request Attributes Sequence) so nested datasets get the same
        #    treatment instead of leaking PHI one level down.
        for elem in ds:
            if elem.VR == "SQ":
                for item in elem.value:
                    self.deidentify_dataset(item, options)

        # 8. Mark the file as de-identified, per PS3.15 requirements.
        ds.PatientIdentityRemoved = "YES"
        ds.DeidentificationMethod = "DICOM Toolkit basic profile de-identification"

        return ds

    @staticmethod
    def _shift_dates(ds: Dataset, days: int) -> None:
        import datetime

        for date_kw, time_kw in zip(_DATE_TAGS, _TIME_TAGS):
            raw = ds.get(date_kw)
            if not raw:
                continue
            try:
                dt = datetime.datetime.strptime(str(raw)[:8], "%Y%m%d")
                shifted = dt + datetime.timedelta(days=days)
                setattr(ds, date_kw, shifted.strftime("%Y%m%d"))
            except ValueError:
                pass  # malformed date - leave as-is rather than crash the batch

    # -- file-level convenience -------------------------------------------

    def deidentify_file(self, in_path: str, out_path: str, options: DeidentifyOptions) -> Dataset:
        ds = pydicom.dcmread(in_path)
        self.deidentify_dataset(ds, options)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        ds.save_as(out_path, enforce_file_format=True)
        return ds


def deidentify_file(in_path: str, out_path: str, options: Optional[DeidentifyOptions] = None) -> Dataset:
    """Convenience one-shot for a single file (no cross-file UID consistency needed)."""
    session = DeidentifySession()
    return session.deidentify_file(in_path, out_path, options or DeidentifyOptions())
