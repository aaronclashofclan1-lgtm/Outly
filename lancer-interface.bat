@echo off
rem Start the Outly desktop interface.
rem Uses the local virtual environment when present, otherwise the PATH Python.
rem Startup failures are written to data\interface-erreur.log.
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    set "PYTHON_GUI=.venv\Scripts\pythonw.exe"
    set "PYTHON_CONSOLE=.venv\Scripts\python.exe"
) else (
    set "PYTHON_GUI=pythonw"
    set "PYTHON_CONSOLE=python"
)

start "" "%PYTHON_GUI%" -m outly.gui
if errorlevel 1 (
    echo.
    echo The interface could not start.
    echo Run it with the console to see the error:
    echo     "%PYTHON_CONSOLE%" -m outly.gui
    echo.
    pause
)
