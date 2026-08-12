# DICOM Toolkit

A single-executable DICOM testing toolkit for PACS analysts: verification,
sending, query/retrieve, modality worklist, file validation, image viewing,
pixel masking, de-identification, Enhanced-to-Classic SOP conversion, a raw
dataset editor, synthetic test data generation, and a local Storage SCP
receiver - all in one Windows `.exe`, no installer, no Python required on
the machine you run it on.

## Features / tabs

The app has three tabs. **Home** is the main page; **PACS Admin** and
**Tools** each hold a group of related tools behind a dropdown at the top
of the tab, so switching tools doesn't mean hunting across a long row of
tabs.

| Tab | Tool (dropdown, where applicable) | What it does |
|---|---|---|
| **Home** | *(no dropdown - this is the main page)* | Open a file or folder of imaging, browse it as a Patient -> Study -> Series -> Image tree, view it with window/level, zoom, pan, multi-frame (cine) scrubbing, and mouse-wheel/button scrubbing between images in a series; draw rectangles in Mask Mode to redact burned-in PHI - regions are kept per-image (switch images/series and back and they're still there to edit or delete) and can optionally be broadcast to every image in the series as you draw; export the current frame to PNG/JPG, or export the *whole study* at once - every image with regions gets masked, every image without any is copied through unchanged. |
| **PACS Admin** | C-ECHO | Verify a node is reachable and answering (DICOM "ping"). |
| | Send (C-STORE) | Push files/folders to a remote AE. |
| | Query/Retrieve | C-FIND a PACS at Patient/Study/Series/Image level, then C-MOVE or C-GET the results. |
| | Worklist (MWL) | Query a Modality Worklist SCP like a modality would. |
| | Storage SCP (Receiver) | Run a local listener to test what a device/server sends *to* you. |
| | Node Presets | Save/manage AE title + host + port profiles you use repeatedly. |
| **Tools** | Validate | Check files for structural errors: missing tags, bad pixel data, malformed UIDs/dates, etc. |
| | De-identify | PS3.15-style basic profile de-identification, consistent across a whole batch. |
| | Enhanced -> Classic | Split Enhanced MR/CT/PET multi-frame objects into classic single-frame instances, for viewers/PACS that don't support Enhanced SOP classes. |
| | Dataset Editor | Browse/edit every tag (including sequences) in a file; add or delete elements. |
| | Test Pattern Generator | Generate synthetic, non-PHI DICOM images/studies for testing the other tools. |
| | Batch Tools | Export a folder to PNG/JPG, or write a validation report file. |

## Running it (end users)

Just double-click `DicomToolkit.exe` - no Python, no dependencies, nothing
else to install. Every push to `main` automatically builds a fresh
`DicomToolkit.exe` via GitHub Actions; grab it from the latest successful
run under the repo's **Actions -> Build Windows exe** tab (the
`DicomToolkit-windows-exe` artifact) - no need to build it yourself unless
you're changing the source (see "Building the exe" below for that case).
Everything it saves between runs (node presets, settings) lives under
`%APPDATA%\DicomToolkit\`, not next to the exe - so the exe can be copied
anywhere, including read-only locations, and run by multiple Windows user
accounts independently.

**First run / Windows Firewall:** if you use the Storage SCP tab, Windows
will prompt to allow the app through the firewall the first time you start
listening. Allow it (at least on "Private" networks) or inbound C-STORE/
C-ECHO from other devices won't reach you.

**Appearance:** use **View -> Theme** in the menu bar to switch between
Light, Dark, and Grey. The choice is remembered between runs.

**Home tab controls:** scroll wheel over the image moves to the
previous/next image in the current series (Ctrl+Scroll zooms instead);
the same navigation is available via the Prev/Next Image buttons or
PgUp/PgDown. Left-drag adjusts window/level; right-drag pans; turning on
Mask Mode switches left-drag to drawing redaction rectangles instead.

**Ports below 1024** (e.g. setting Storage SCP to the "official" port 104)
require running as Administrator on Windows. The default receiver port,
11112, does not.

## Building the exe (developers / whoever maintains this)

Prerequisites: a Windows machine with Python 3.10+ installed (get it from
[python.org](https://python.org); check "Add python.exe to PATH" during
install).

1. Copy this whole project folder onto the Windows machine.
2. Double-click `build\build.bat` (or run it from a Command Prompt/
   PowerShell opened in the project folder).
3. Wait - it creates a `.venv`, installs `requirements.txt`, then runs
   PyInstaller. Takes a few minutes, mostly for PySide6.
4. The result is `dist\DicomToolkit.exe` - copy that one file anywhere.

To rebuild after changing the source, just re-run `build.bat`.

### Running from source instead (no packaging)

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

### Troubleshooting the build

- **"Python was not found on PATH"** - reinstall Python and check "Add
  python.exe to PATH", or run `build.bat` from a terminal where `python`
  already works.
- **The exe builds but won't start / closes instantly** - temporarily edit
  `build\dicom_toolkit.spec`, set `console=True`, rebuild, and run the exe
  from a Command Prompt (not by double-clicking) so you can read the Python
  traceback it prints. Set it back to `False` once fixed.
- **Antivirus flags the exe** - this is a common PyInstaller false-positive
  (an unsigned, single-file executable that unpacks itself at startup looks
  suspicious to some heuristics). Add an exclusion, or code-sign the exe if
  your organization has a certificate for that.

## Project layout

```
main.py                    entry point
config.py                  paths, defaults, settings persistence
core/                       all DICOM logic - no GUI code, reusable/testable on its own
  net_ops.py                 C-ECHO/STORE/FIND/MOVE/GET + Storage SCP (pynetdicom)
  validate.py                 file validator
  deidentify.py                PS3.15-style de-identification
  mask.py                        pixel redaction
  enhanced_convert.py             Enhanced -> Classic SOP splitting
  dataset_utils.py                 Qt tree model for the Dataset Editor
  test_pattern.py                   synthetic DICOM generator
  presets.py                         saved node connection profiles
ui/                        all GUI code (PySide6)
  main_window.py             assembles the three top-level tabs, View > Theme menu
  theme.py                     Light/Dark/Grey QPalette definitions + persistence
  tab_group.py                   dropdown-plus-stack widget PACS Admin/Tools are built from
  tab_home.py                     Home tab: Patient/Study/Series/Image browser, view, mask, export
  worker.py                        generic background-thread runner for network/batch jobs
  tab_*.py                           one file per PACS Admin / Tools entry
  widgets/                            reusable pieces (image viewer, node picker, log console)
assets/
  icon.ico / icon.png         app icon (window/taskbar icon + .exe icon)
  generate_icon.py             regenerates the two files above
build/
  dicom_toolkit.spec         PyInstaller build configuration
  build.bat                    one-click Windows build script
```

Every module has a docstring explaining what it does and, where relevant,
*why* it's built the way it is - start there if you're extending something.

## Scope and limitations (read before relying on this for compliance)

- **Validate** checks the errors that actually break real-world DICOM
  transfers and viewers (missing required tags, pixel data/geometry
  mismatches, bad UIDs/dates, undecodable pixel data). It is **not** a full
  IOD/module conformance checker against every SOP Class's exact PS3.3
  tag table.
- **De-identify** implements a practical subset (~50 tags) of DICOM PS3.15
  Annex E's Basic Application Level Confidentiality Profile, plus generic
  UID remapping and overlay/curve stripping. It is **not** the full ~450
  entry PS3.15 table. Always spot-check output (e.g. in the Dataset Editor
  or Viewer) before treating de-identified files as safe to share outside
  your environment - this is a testing tool, not a certified de-identification
  product.
- **Mask** and **Enhanced -> Classic** always write output as uncompressed
  Explicit VR Little Endian, since re-compressing edited pixel data isn't
  reliably possible with the compression encoders available in this
  environment. If you need the original compression back, recompress
  separately afterward.
- **Enhanced -> Classic** covers MR, CT, and PET Enhanced Image Storage
  (the three built on the shared Multi-frame Functional Groups module) and
  maps the most commonly-needed geometry/timing/windowing functional
  groups. Enhanced US Volume and other volumetric SOP classes aren't
  covered.
- **Home's "Apply Masks & Export Study"** applies the *pixel coordinates*
  you drew on one image to every image in the study, across all series.
  It does **not** rescale regions for images of a different size than the
  one you drew on (e.g. a differently-sized localizer/scout series in the
  same study) - `core.mask.apply_masks` clips out-of-bounds coordinates
  rather than erroring, but a region can end up covering the wrong area
  on a differently-sized image. Spot-check output across series before
  relying on it, or draw/export per-series if a study mixes image sizes.
