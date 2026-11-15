@echo off
:: Build the standalone binary for Teacher's Teammate in a CLEAN, ISOLATED venv.
::
:: Why a dedicated venv (not the active one)?
::   PyInstaller freezes whatever is importable in the running interpreter. If the
::   build runs inside a dev venv that also has the optional extras installed
::   (langchain, spaCy, ...), those get swept into the binary -- bloating it and
::   pulling in code whose licence the bundle does not carry.
::   Building in a fresh venv with ONLY the base runtime deps (pip install -e .)
::   guarantees the bundle matches third_party_licenses\. Optional OCR/LLM/privacy
::   features are installed by users at runtime via the in-app addon installer.
::
:: Why the constraints file?
::   Every version comes from requirements_test_lock.txt plus
::   tools\build\requirements_platform.txt, so two builds of the same commit produce
::   the same bundle and the same license list. check_pins.py proves pip followed it.
::
:: Usage (run from repo root):
::   tools\build\build.bat
setlocal
pushd "%~dp0..\.."
set "VENV_DIR=%CD%\.build_venv"
set "CONSTRAINTS=%CD%\pyinstaller-work\constraints.txt"

py -3.14 --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3.14 not found.
    echo        The bundle is built with Python 3.14 ^(see .python-version^).
    popd
    endlocal
    exit /b 1
)

echo ==^> Clearing stale build artifacts
rmdir /s /q "%VENV_DIR%" 2>nul
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
rmdir /s /q pyinstaller-work 2>nul
rmdir /s /q teachers_teammate.egg-info 2>nul

echo ==^> Writing the pinned constraints
py -3.14 tools\build\make_constraints.py
if errorlevel 1 goto :fail

echo ==^> Creating clean build venv (base runtime deps only)
py -3.14 -m venv "%VENV_DIR%"
"%VENV_DIR%\Scripts\pip" install --quiet --upgrade -c "%CONSTRAINTS%" pip
"%VENV_DIR%\Scripts\pip" install --quiet -c "%CONSTRAINTS%" -e . pyinstaller pyinstaller-hooks-contrib
if errorlevel 1 goto :fail

echo ==^> Checking that every installed package is pinned
"%VENV_DIR%\Scripts\python" tools\build\check_pins.py
if errorlevel 1 goto :fail

echo ==^> Building standalone binary
"%VENV_DIR%\Scripts\python" tools\build\build_standalone.py
if errorlevel 1 goto :fail

popd
endlocal
exit /b 0

:fail
popd
endlocal
exit /b 1
