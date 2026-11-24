@echo off
:: Build the standalone binary for Teacher's Teammate in a clean venv.
::
:: The venv recipe -- a fresh venv with the pinned install, and why not the active one -- is
:: tools\build\make_venv.py, which build.sh and the CI action call too.
::
:: Usage (run from repo root):
::   tools\build\build.bat
setlocal
pushd "%~dp0..\.."
set "VENV_DIR=%CD%\.build_venv"

py -3.14 --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3.14 not found.
    echo        The bundle is built with Python 3.14 ^(see .python-version^).
    popd
    endlocal
    exit /b 1
)

echo ==^> Clearing stale build artifacts
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
rmdir /s /q pyinstaller-work 2>nul
rmdir /s /q teachers_teammate.egg-info 2>nul

echo ==^> Creating the build venv from the pins
py -3.14 tools\build\make_venv.py "%VENV_DIR%" --for build
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
