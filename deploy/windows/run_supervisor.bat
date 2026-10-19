@echo off
REM ==============================================================================
REM Funbot Systematic Solana Trading Engine - Production Supervisor Launcher
REM Launches the Python ProductionSupervisor in the project virtual environment.
REM ==============================================================================

cd /d "%~dp0\..\.."

echo ==============================================================================
echo [Funbot] Starting Production Supervisor in Windows Environment...
echo [Funbot] Directory: %CD%
echo ==============================================================================

if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
    set "PYTHON_EXE=python"
)

REM Set unbuffered stdout and local python path
set "PYTHONUNBUFFERED=1"
set "PYTHONPATH=."

"%PYTHON_EXE%" main.py supervisor --run %*

if %ERRORLEVEL% NEQ 0 (
    echo [Funbot] Supervisor exited with error code %ERRORLEVEL%.
)
