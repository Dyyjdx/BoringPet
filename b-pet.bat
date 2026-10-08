@echo off
rem ============================================================
rem  BoringPet launcher - normal privileges, no UAC prompt.
rem  Double-click this file.
rem
rem  Need the "game boost" feature? Use b-pet-admin.bat instead,
rem  because that one writes to the registry.
rem
rem  ASCII-only, CRLF line endings - see the note in build_exe.bat.
rem ============================================================
setlocal
cd /d "%~dp0"

rem --- 1) packaged build: prefer BoringPet.exe next to this file ---
if exist "%~dp0BoringPet.exe" goto :runexe

rem --- 2) source run: find a pythonw.exe so no console window pops up ---
set "PYW="
if exist "%~dp0python\pythonw.exe" set "PYW=%~dp0python\pythonw.exe"
if not defined PYW if exist "%~dp0.venv\Scripts\pythonw.exe" set "PYW=%~dp0.venv\Scripts\pythonw.exe"
if not defined PYW for /f "delims=" %%i in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%i"
if not defined PYW goto :nopython
if not exist "%~dp0main.py" goto :nomain

start "" "%PYW%" "%~dp0main.py"
exit /b 0

:runexe
start "" "%~dp0BoringPet.exe"
exit /b 0

:nopython
echo.
echo [BoringPet] No Python found.
echo   Option 1: use the packaged BoringPet.exe, put it next to this file.
echo   Option 2: install Python 3.10+ and tick "Add python.exe to PATH".
echo.
pause
exit /b 1

:nomain
echo.
echo [BoringPet] main.py is missing here, cannot start from source.
echo.
pause
exit /b 1
