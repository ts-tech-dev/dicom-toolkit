"""
core/net_ops.py
================
GUI-independent wrappers around pynetdicom for every DICOM network
operation this toolkit exposes. Each function takes a `log` callback
(``str -> None``) that it calls with human-readable progress lines, and
either returns a small dataclass or a list of dicts with the structured
result. The UI layer runs these on a background QThread and pipes `log`
into a Qt signal - nothing in this file imports Qt, so it can also be
unit-tested or reused from a plain script.

Operations provided:
    echo()               C-ECHO SCU        "ping" a node
    send()                 C-STORE SCU        push files to a node
    find()                   C-FIND SCU          Study/Series/Image/Patient level query
    find_worklist()            C-FIND SCU          Modality Worklist query
    move()                        C-MOVE SCU          ask a PACS to send studies to a 3rd AE
    get()                          C-GET SCU            pull studies directly to us over one association
    StorageSCP                       local C-STORE (+ optional C-ECHO) receiver, start/stop-able
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, List, Optional

import pydicom
from pydicom.dataset import Dataset
from pydicom.errors import InvalidDicomError
from pydicom.uid import UID

from pynetdicom import AE, evt
from pynetdicom.presentation import AllStoragePresentationContexts, build_context
from pynetdicom.sop_class import (  # type: ignore
    ModalityWorklistInformationFind,
    PatientRootQueryRetrieveInformationModelFind,
    PatientRootQueryRetrieveInformationModelGet,
    PatientRootQueryRetrieveInformationModelMove,
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelGet,
    StudyRootQueryRetrieveInformationModelMove,
    Verification,
)

from config import DEFAULT_NETWORK_TIMEOUT

LogFn = Callable[[str], None]


def _noop_log(_msg: str) -> None:
    pass


# ---------------------------------------------------------------------------
# Status code -> human text. DIMSE status codes are 16-bit values; 0x0000 is
# always success, 0xFF00/0xFF01 are "pending" (more results coming, C-FIND/
# C-GET/C-MOVE only), and anything in 0xA7xx/0xC0xx-ish ranges is a failure.
# We don't try to be exhaustive - just readable for a testing tool.
# ---------------------------------------------------------------------------
def describe_status(status: Optional[Dataset]) -> str:
    if status is None or not hasattr(status, "Status"):
        return "No response (association/timeout error - see log above)"
    code = status.Status
    if code == 0x0000:
        return "Success"
    if code in (0xFF00, 0xFF01):
        return "Pending"
    if code == 0xB000:
        return "Warning - Sub-operations complete, one or more failures"
    if code == 0xFE00:
        return "Cancelled"
    return f"Failure (status=0x{code:04X})"


def _new_ae(calling_ae_title: str, timeout: int) -> AE:
    """Build an AE with consistent timeouts - shared by every SCU function."""
    ae = AE(ae_title=calling_ae_title)
    ae.acse_timeout = timeout
    ae.dimse_timeout = timeout
    ae.network_timeout = timeout
    return ae


# ---------------------------------------------------------------------------
# C-ECHO
# ---------------------------------------------------------------------------
@dataclass
class EchoResult:
    success: bool
    message: str
    round_trip_ms: Optional[float] = None


def echo(
    host: str,
    port: int,
    called_ae_title: str,
    calling_ae_title: str,
    timeout: int = DEFAULT_NETWORK_TIMEOUT,
    log: LogFn = _noop_log,
) -> EchoResult:
    """Send a single C-ECHO (DICOM 'ping') and report whether the node replied."""
    ae = _new_ae(calling_ae_title, timeout)
    ae.add_requested_context(Verification)

    log(f"Associating with {called_ae_title} @ {host}:{port} for C-ECHO ...")
    start = time.monotonic()
    assoc = ae.associate(host, port, ae_title=called_ae_title)

    if not assoc.is_established:
        log("Association rejected, aborted, or never connected.")
        return EchoResult(False, "Association failed - check host/port/AE title and firewall")

    try:
        status = assoc.send_c_echo()
        elapsed_ms = (time.monotonic() - start) * 1000
        msg = describe_status(status)
        log(f"C-ECHO response: {msg} ({elapsed_ms:.1f} ms)")
        return EchoResult(status is not None and status.Status == 0x0000, msg, elapsed_ms)
    finally:
        assoc.release()


# ---------------------------------------------------------------------------
# C-STORE (send)
# ---------------------------------------------------------------------------
@dataclass
class SendFileResult:
    path: str
    success: bool
    message: str


def _iter_dicom_files(paths: Iterable[str]) -> List[str]:
    """Expand a mix of file paths and directory paths into a flat file list."""
    out: List[str] = []
    for p in paths:
        pth = Path(p)
        if pth.is_dir():
            for f in sorted(pth.rglob("*")):
                if f.is_file():
                    out.append(str(f))
        elif pth.is_file():
            out.append(str(pth))
    return out


def send(
    paths: Iterable[str],
    host: str,
    port: int,
    called_ae_title: str,
    calling_ae_title: str,
    timeout: int = DEFAULT_NETWORK_TIMEOUT,
    log: LogFn = _noop_log,
) -> List[SendFileResult]:
    """
    C-STORE a list of files and/or directories (directories are scanned
    recursively) to a remote AE. Builds the minimal set of presentation
    contexts needed (one per unique SOP Class UID / Transfer Syntax UID
    combination actually present in the files) since an association is
    capped at 128 presentation contexts by the DICOM standard.
    """
    files = _iter_dicom_files(paths)
    if not files:
        log("No files found to send.")
        return []

    # First pass: read every file's header (fast - not the pixel data) so
    # we know which SOP Class / Transfer Syntax pairs we need contexts for,
    # and so a broken file is reported instead of silently skipped later.
    loaded: List[tuple[str, Dataset]] = []
    results: List[SendFileResult] = []
    contexts_needed: dict[str, set] = {}
    for f in files:
        try:
            ds = pydicom.dcmread(f)
        except (InvalidDicomError, OSError) as exc:
            results.append(SendFileResult(f, False, f"Could not read as DICOM: {exc}"))
            continue
        if not hasattr(ds, "file_meta") or "TransferSyntaxUID" not in ds.file_meta:
            results.append(SendFileResult(f, False, "Missing file meta / Transfer Syntax UID"))
            continue
        sop_class = str(ds.SOPClassUID)
        ts = str(ds.file_meta.TransferSyntaxUID)
        contexts_needed.setdefault(sop_class, set()).add(ts)
        loaded.append((f, ds))

    if not loaded:
        log("No readable DICOM files among the given paths.")
        return results

    ae = _new_ae(calling_ae_title, timeout)
    for sop_class, ts_set in contexts_needed.items():
        ae.add_requested_context(sop_class, list(ts_set))

    log(f"Associating with {called_ae_title} @ {host}:{port} to send {len(loaded)} file(s) ...")
    assoc = ae.associate(host, port, ae_title=called_ae_title)
    if not assoc.is_established:
        log("Association rejected, aborted, or never connected.")
        for f, _ in loaded:
            results.append(SendFileResult(f, False, "Association failed"))
        return results

    try:
        for f, ds in loaded:
            status = assoc.send_c_store(ds)
            ok = status is not None and status.Status == 0x0000
            msg = describe_status(status)
            log(f"  {'OK' if ok else 'FAIL'}  {os.path.basename(f)} -> {msg}")
            results.append(SendFileResult(f, ok, msg))
    finally:
        assoc.release()

    return results


# ---------------------------------------------------------------------------
# C-FIND (Query/Retrieve)
# ---------------------------------------------------------------------------
_QR_FIND_MODELS = {
    "PATIENT": PatientRootQueryRetrieveInformationModelFind,
    "STUDY": StudyRootQueryRetrieveInformationModelFind,
}
_QR_MOVE_MODELS = {
    "PATIENT": PatientRootQueryRetrieveInformationModelMove,
    "STUDY": StudyRootQueryRetrieveInformationModelMove,
}
_QR_GET_MODELS = {
    "PATIENT": PatientRootQueryRetrieveInformationModelGet,
    "STUDY": StudyRootQueryRetrieveInformationModelGet,
}


def _build_identifier(query_level: str, criteria: dict) -> Dataset:
    """
    Build a C-FIND/C-MOVE/C-GET identifier dataset.

    `criteria` is a plain dict of {DICOM keyword: value}. Use "" (empty
    string) for a tag you want returned but aren't filtering on - that's
    a "universal match" per the DICOM standard. QueryRetrieveLevel is set
    from `query_level` automatically.
    """
    ident = Dataset()
    ident.QueryRetrieveLevel = query_level
    for keyword, value in criteria.items():
        setattr(ident, keyword, value)
    return ident


@dataclass
class FindMatch:
    fields: dict  # DICOM keyword -> value, for every element present in the response


def find(
    host: str,
    port: int,
    called_ae_title: str,
    calling_ae_title: str,
    query_level: str,
    criteria: dict,
    root_model: str = "STUDY",
    timeout: int = DEFAULT_NETWORK_TIMEOUT,
    log: LogFn = _noop_log,
) -> List[FindMatch]:
    """
    Run a C-FIND. `query_level` is one of PATIENT/STUDY/SERIES/IMAGE.
    `root_model` selects the Patient Root or Study Root information model
    (Study Root is what almost every modern PACS expects).
    """
    model = _QR_FIND_MODELS[root_model]
    ae = _new_ae(calling_ae_title, timeout)
    ae.add_requested_context(model)

    identifier = _build_identifier(query_level, criteria)
    log(f"C-FIND ({root_model} root, level={query_level}) to {called_ae_title} @ {host}:{port} ...")

    assoc = ae.associate(host, port, ae_title=called_ae_title)
    matches: List[FindMatch] = []
    if not assoc.is_established:
        log("Association rejected, aborted, or never connected.")
        return matches

    try:
        for status, identifier_response in assoc.send_c_find(identifier, model):
            msg = describe_status(status)
            if status is None:
                log("No response from peer (timeout?)")
                break
            if status.Status in (0xFF00, 0xFF01) and identifier_response is not None:
                fields = {
                    elem.keyword: elem.value
                    for elem in identifier_response
                    if elem.keyword
                }
                matches.append(FindMatch(fields))
            elif status.Status != 0x0000:
                log(f"C-FIND failure/warning: {msg}")
    finally:
        assoc.release()

    log(f"C-FIND complete: {len(matches)} match(es).")
    return matches


def find_worklist(
    host: str,
    port: int,
    called_ae_title: str,
    calling_ae_title: str,
    criteria: dict,
    timeout: int = DEFAULT_NETWORK_TIMEOUT,
    log: LogFn = _noop_log,
) -> List[FindMatch]:
    """
    Query a Modality Worklist SCP (used by modalities to pull scheduled
    procedure steps). Unlike Q/R C-FIND there's no QueryRetrieveLevel -
    the identifier is built straight from `criteria` (typically including
    an empty ScheduledProcedureStepSequence to request scheduling info back).
    """
    ae = _new_ae(calling_ae_title, timeout)
    ae.add_requested_context(ModalityWorklistInformationFind)

    identifier = Dataset()
    for keyword, value in criteria.items():
        setattr(identifier, keyword, value)

    log(f"C-FIND (Modality Worklist) to {called_ae_title} @ {host}:{port} ...")
    assoc = ae.associate(host, port, ae_title=called_ae_title)
    matches: List[FindMatch] = []
    if not assoc.is_established:
        log("Association rejected, aborted, or never connected.")
        return matches

    try:
        for status, identifier_response in assoc.send_c_find(identifier, ModalityWorklistInformationFind):
            if status is None:
                log("No response from peer (timeout?)")
                break
            if status.Status in (0xFF00, 0xFF01) and identifier_response is not None:
                fields = {
                    elem.keyword: elem.value
                    for elem in identifier_response
                    if elem.keyword
                }
                matches.append(FindMatch(fields))
            elif status.Status != 0x0000:
                log(f"MWL C-FIND failure/warning: {describe_status(status)}")
    finally:
        assoc.release()

    log(f"Worklist query complete: {len(matches)} scheduled item(s).")
    return matches


# ---------------------------------------------------------------------------
# C-MOVE
# ---------------------------------------------------------------------------
@dataclass
class MoveProgress:
    completed: int = 0
    failed: int = 0
    warning: int = 0
    remaining: int = 0
    final_status: str = ""


def move(
    host: str,
    port: int,
    called_ae_title: str,
    calling_ae_title: str,
    move_destination_ae_title: str,
    query_level: str,
    criteria: dict,
    root_model: str = "STUDY",
    timeout: int = DEFAULT_NETWORK_TIMEOUT,
    log: LogFn = _noop_log,
) -> MoveProgress:
    """
    Ask the remote PACS (`called_ae_title`) to push matching studies/series
    to a third AE (`move_destination_ae_title`) via C-STORE sub-operations.
    That destination AE must already be registered on the PACS and have a
    Storage SCP listening - this call only triggers the transfer, it does
    not receive the images itself. To pull images directly into this tool,
    use get() instead, or run the Storage SCP tab as the move destination.
    """
    model = _QR_MOVE_MODELS[root_model]
    ae = _new_ae(calling_ae_title, timeout)
    ae.add_requested_context(model)

    identifier = _build_identifier(query_level, criteria)
    log(
        f"C-MOVE ({root_model} root, level={query_level}) to {called_ae_title} "
        f"@ {host}:{port}, destination AE = {move_destination_ae_title} ..."
    )

    assoc = ae.associate(host, port, ae_title=called_ae_title)
    progress = MoveProgress()
    if not assoc.is_established:
        log("Association rejected, aborted, or never connected.")
        progress.final_status = "Association failed"
        return progress

    try:
        last_status = None
        for status, _ in assoc.send_c_move(identifier, move_destination_ae_title, model):
            if status is None:
                log("No response from peer (timeout?)")
                break
            last_status = status
            progress.completed = getattr(status, "NumberOfCompletedSuboperations", progress.completed)
            progress.failed = getattr(status, "NumberOfFailedSuboperations", progress.failed)
            progress.warning = getattr(status, "NumberOfWarningSuboperations", progress.warning)
            progress.remaining = getattr(status, "NumberOfRemainingSuboperations", progress.remaining)
            if status.Status in (0xFF00, 0xFF01):
                log(
                    f"  ... in progress: completed={progress.completed} "
                    f"failed={progress.failed} warning={progress.warning} "
                    f"remaining={progress.remaining}"
                )
        progress.final_status = describe_status(last_status)
    finally:
        assoc.release()

    log(f"C-MOVE finished: {progress.final_status} "
        f"(completed={progress.completed}, failed={progress.failed}, warning={progress.warning})")
    return progress


# ---------------------------------------------------------------------------
# C-GET (pull images directly to us, over the same association)
# ---------------------------------------------------------------------------
def get(
    host: str,
    port: int,
    called_ae_title: str,
    calling_ae_title: str,
    query_level: str,
    criteria: dict,
    save_dir: str,
    root_model: str = "STUDY",
    timeout: int = DEFAULT_NETWORK_TIMEOUT,
    log: LogFn = _noop_log,
) -> MoveProgress:
    """
    Like move(), but the matching instances are sent back to us directly
    over the *same* association (no separate destination AE needed).
    We register a C-STORE handler on our own AE so it can accept the
    sub-operation stores the remote node initiates as part of the C-GET.
    """
    model = _QR_GET_MODELS[root_model]
    ae = _new_ae(calling_ae_title, timeout)
    ae.add_requested_context(model)
    # C-GET requires us to also support storing whatever SOP classes might
    # come back, so negotiate the standard storage contexts too.
    for ctx in AllStoragePresentationContexts:
        ae.add_requested_context(ctx.abstract_syntax)

    save_path = Path(save_dir)
    save_path.mkdir(parents=True, exist_ok=True)

    def _on_c_store(event):
        ds = event.dataset
        ds.file_meta = event.file_meta
        sop_uid = str(ds.get("SOPInstanceUID", "unknown"))
        out_file = save_path / f"{sop_uid}.dcm"
        ds.save_as(out_file, enforce_file_format=True)
        log(f"  received & saved: {out_file.name}")
        return 0x0000  # Success

    handlers = [(evt.EVT_C_STORE, _on_c_store)]

    identifier = _build_identifier(query_level, criteria)
    log(f"C-GET ({root_model} root, level={query_level}) to {called_ae_title} @ {host}:{port} ...")

    assoc = ae.associate(host, port, ae_title=called_ae_title, evt_handlers=handlers)
    progress = MoveProgress()
    if not assoc.is_established:
        log("Association rejected, aborted, or never connected.")
        progress.final_status = "Association failed"
        return progress

    try:
        last_status = None
        for status, _ in assoc.send_c_get(identifier, model):
            if status is None:
                log("No response from peer (timeout?)")
                break
            last_status = status
            progress.completed = getattr(status, "NumberOfCompletedSuboperations", progress.completed)
            progress.failed = getattr(status, "NumberOfFailedSuboperations", progress.failed)
            progress.warning = getattr(status, "NumberOfWarningSuboperations", progress.warning)
            progress.remaining = getattr(status, "NumberOfRemainingSuboperations", progress.remaining)
        progress.final_status = describe_status(last_status)
    finally:
        assoc.release()

    log(f"C-GET finished: {progress.final_status} "
        f"(completed={progress.completed}, failed={progress.failed}, warning={progress.warning})")
    return progress


# ---------------------------------------------------------------------------
# Storage SCP - local receiver, used to test devices/servers that push
# images *to* you, and as the destination for C-MOVE tests.
# ---------------------------------------------------------------------------
class StorageSCP:
    """
    A start/stop-able local DICOM receiver. Accepts C-ECHO (verification)
    and C-STORE (for every standard storage SOP class) and saves incoming
    instances to `save_dir` as ``<SOPInstanceUID>.dcm``.

    Usage:
        scp = StorageSCP("MYAE", 11112, "/path/to/save", log=print)
        scp.start()
        ...
        scp.stop()
    """

    def __init__(
        self,
        ae_title: str,
        port: int,
        save_dir: str,
        log: LogFn = _noop_log,
        on_receive: Optional[Callable[[str, Dataset], None]] = None,
    ):
        self.ae_title = ae_title
        self.port = port
        self.save_dir = Path(save_dir)
        self.log = log
        self.on_receive = on_receive
        self._ae: Optional[AE] = None
        self._server = None  # pynetdicom ThreadedAssociationServer
        self.received_count = 0

    @property
    def is_running(self) -> bool:
        return self._server is not None

    def start(self) -> None:
        if self.is_running:
            return
        self.save_dir.mkdir(parents=True, exist_ok=True)

        ae = AE(ae_title=self.ae_title)
        ae.supported_contexts = AllStoragePresentationContexts
        ae.add_supported_context(Verification)
        # Also accept Modality Worklist and Q/R Find/Move/Get as *supported*
        # contexts is unnecessary for a plain receiver, so we keep this to
        # storage + verification, which covers "does this device push
        # images correctly" testing.

        handlers = [
            (evt.EVT_C_STORE, self._on_c_store),
            (evt.EVT_C_ECHO, self._on_c_echo),
            (evt.EVT_CONN_OPEN, self._on_conn_open),
        ]

        self._ae = ae
        # block=False runs the server's accept loop on a background thread
        # inside pynetdicom, so start() returns immediately and the GUI
        # stays responsive.
        self._server = ae.start_server(("0.0.0.0", self.port), block=False, evt_handlers=handlers)
        self.log(f"Storage SCP listening on port {self.port} as AE '{self.ae_title}' -> saving to {self.save_dir}")

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server = None
            self.log("Storage SCP stopped.")

    def _on_conn_open(self, event):
        try:
            peer = event.assoc.requestor.address
        except Exception:
            peer = "unknown"
        self.log(f"Incoming connection from {peer}")

    def _on_c_echo(self, event):
        self.log(f"Received C-ECHO from {event.assoc.requestor.ae_title}")
        return 0x0000

    def _on_c_store(self, event):
        ds = event.dataset
        ds.file_meta = event.file_meta
        sop_uid = str(ds.get("SOPInstanceUID", "unknown"))
        out_file = self.save_dir / f"{sop_uid}.dcm"
        try:
            ds.save_as(out_file, enforce_file_format=True)
        except Exception as exc:  # noqa: BLE001 - must always return a DIMSE status
            self.log(f"  FAILED to save incoming instance: {exc}")
            return 0xA700  # Refused: Out of Resources

        self.received_count += 1
        who = event.assoc.requestor.ae_title
        self.log(f"  [{self.received_count}] stored {out_file.name} (from {who})")
        if self.on_receive:
            self.on_receive(str(out_file), ds)
        return 0x0000
