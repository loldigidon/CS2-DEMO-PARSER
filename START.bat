@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    echo [setup] Creating local Python environment...
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3.11 -m venv ".venv" >nul 2>nul
    )
    if not exist "%VENV_PYTHON%" (
        python -m venv ".venv"
    )
    if not exist "%VENV_PYTHON%" (
        echo [error] Python 3.11, 3.12 or 3.13 is required.
        pause
        exit /b 1
    )
)

"%VENV_PYTHON%" -c "import awpy, pandas, pyarrow, PIL, zstandard" >nul 2>nul
if errorlevel 1 (
    echo [setup] Installing project dependencies. This is needed only once...
    "%VENV_PYTHON%" -m pip install --disable-pip-version-check -e .
    if errorlevel 1 (
        echo [error] Dependency installation failed.
        pause
        exit /b 1
    )
)

"%VENV_PYTHON%" launcher.py %*
if errorlevel 1 pause

endlocal
