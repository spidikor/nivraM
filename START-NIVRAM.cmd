@echo off
setlocal
cd /d "%~dp0"
rem Current desktop source entrypoint; arguments are explicit, no .venv migration.
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3.12 -B scripts\check_environment.py >nul 2>nul
if errorlevel 1 goto use_python
py -3.12 -B scripts\windows_source.py launch %*
exit /b %errorlevel%
:use_python
python -B scripts\check_environment.py
if errorlevel 1 exit /b 1
python -B scripts\windows_source.py launch %*
exit /b %errorlevel%
