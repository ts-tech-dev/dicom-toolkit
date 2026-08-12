@echo off
REM ===========================================================================
REM  DICOM Toolkit - Windows build script
REM ===========================================================================
REM  Builds a single DicomToolkit.exe from the Python source. Run this on
REM  Windows (double-click it in File Explorer, or run it from a Command
REM  Prompt / PowerShell) - it can be run from anywhere, it always operates
REM  on the project folder this script lives inside.
REM
REM  What it does:
REM    1. Creates a private virtual environment (.venv) so this doesn't
REM       touch any other Python installation on your machine.
REM    2. Installs everything in requirements.txt into it.
REM    3. Runs PyInstaller against build\dicom_toolkit.spec to produce
REM       dist\DicomToolkit.exe - one file, no installer needed, copy it
REM       anywhere on a Windows machine and run it.
REM ===========================================================================

setlocal
cd /d "%~dp0\.."

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found on PATH.
    echo Install Python 3.10 or newer from https://python.org - during
    echo install, make sure "Add python.exe to PATH" is checked.
    pause
    exit /b 1
)

echo.
echo === [1/3] Creating virtual environment (.venv) ===
python -m venv .venv
if errorlevel 1 (
    echo Failed to create the virtual environment. See the error above.
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat

echo.
echo === [2/3] Installing dependencies (this can take a few minutes) ===
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install dependencies. See the error above.
    pause
    exit /b 1
)

echo.
echo === [3/3] Building DicomToolkit.exe (this can take a few minutes) ===
pyinstaller --noconfirm build\dicom_toolkit.spec
if errorlevel 1 (
    echo Build failed. See the error above, and the "Troubleshooting the
    echo build" section of README.md.
    pause
    exit /b 1
)

echo.
echo ===========================================================================
echo  Done. The executable is at: dist\DicomToolkit.exe
echo  Copy that single file anywhere on a Windows machine and run it - no
echo  Python installation is required on the target machine.
echo ===========================================================================
pause
