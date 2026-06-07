@echo off
:: Build the standalone binary for Teacher's Teammate in a CLEAN, ISOLATED venv.
::
:: Why a dedicated venv (not the active one)?
::   PyInstaller freezes whatever is importable in the running interpreter. If the
::   build runs inside a dev venv that also has the optional extras installed
::   (langchain, spaCy, PaddleOCR, PyMuPDF, PyQt6, ...), those get swept into the
::   binary -- bloating it and pulling in AGPL/GPL code (PyMuPDF, PyQt6).
::   Building in a fresh venv with ONLY the base runtime deps (pip install -e .)
::   guarantees the bundle matches third_party_licenses\. Optional OCR/LLM/privacy
::   features are installed by users at runtime via the in-app addon installer.
::
:: Usage (run from repo root):
::   tools\build\build.bat
setlocal
pushd "%~dp0..\.."
set "VENV_DIR=%CD%\.build_venv"

echo ==^> Clearing stale build artifacts
rmdir /s /q "%VENV_DIR%" 2>nul
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
rmdir /s /q pyinstaller-work 2>nul
rmdir /s /q teachers_teammate.egg-info 2>nul

echo ==^> Creating clean build venv (base runtime deps only)
python -m venv "%VENV_DIR%"
"%VENV_DIR%\Scripts\pip" install --quiet --upgrade pip
"%VENV_DIR%\Scripts\pip" install --quiet -e . "pyinstaller>=6.0"

echo ==^> Building standalone binary
"%VENV_DIR%\Scripts\python" tools\build\build_standalone.py
popd
endlocal
