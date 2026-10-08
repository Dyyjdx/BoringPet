@echo off
rem ============================================================
rem  BoringPet build script
rem
rem  Double-click this file. Output goes to dist\BoringPet\
rem  See the packaging guide for the full story.
rem
rem  This file is deliberately ASCII-only and uses CRLF line
rem  endings. Both matter for cmd.exe:
rem    * cmd.exe parses .bat files with the OEM codepage, so a
rem      Chinese .bat saved as UTF-8 gets mis-read; and because
rem      cmd seeks by byte offset, multi-byte characters can make
rem      it jump into the middle of a line and run garbage.
rem    * LF-only line endings make cmd.exe lose its place too
rem      (it starts executing fragments of lines).
rem  Keep this file ASCII with CRLF line endings.
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   BoringPet build
echo ============================================
echo.

set "PY="
if exist "%~dp0python\python.exe" set "PY=%~dp0python\python.exe"
if not defined PY if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set "PY=%%i"
if not defined PY goto :nopython

echo [1/5] Installing dependencies...
"%PY%" -m pip install -r requirements.txt || goto :fail
rem Plain "pip install pyinstaller": no --upgrade, so an already-installed
rem PyInstaller is reused instead of hitting the network on every build.
"%PY%" -m pip install pyinstaller || goto :fail

echo.
echo [2/5] Cleaning previous build output...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo [3/5] Running PyInstaller, this usually takes a few minutes...
"%PY%" -m PyInstaller BoringPet.spec --noconfirm || goto :fail

echo.
echo [4/5] Preparing the release folder...
set "OUT=dist\BoringPet"
if not exist "%OUT%" goto :noout

rem User data folders. The app creates these on first run too; doing it
rem here just makes the layout obvious to whoever receives the folder.
if not exist "%OUT%\memory" mkdir "%OUT%\memory"
if not exist "%OUT%\memory\diary" mkdir "%OUT%\memory\diary"
if not exist "%OUT%\recordings" mkdir "%OUT%\recordings"

rem config.json next to the exe, so the recipient can tune fps etc.
if exist "config.json" copy /y "config.json" "%OUT%\config.json" >nul

rem SAFETY: never ship settings.json - it holds the API key.
if exist "%OUT%\settings.json" del /q "%OUT%\settings.json"

rem Drop stray debug logs and private runtime state if any got copied in.
if exist "%OUT%\bubble_debug.log" del /q "%OUT%\bubble_debug.log"
if exist "%OUT%\divination_debug.log" del /q "%OUT%\divination_debug.log"
if exist "%OUT%\chat_state.json" del /q "%OUT%\chat_state.json"
if exist "%OUT%\dsh_session.json" del /q "%OUT%\dsh_session.json"
if exist "%OUT%\boringpet.log" del /q "%OUT%\boringpet.log"

echo.
echo [5/5] Done.
echo.
echo   Release folder: %OUT%
echo   Zip that whole folder and send it.
echo.
echo   Remind the recipient: on first run, right-click the pet,
echo   open Settings, and enter their own API key before chatting.
echo.
pause
exit /b 0

:nopython
echo.
echo [ERROR] No python found.
echo   Option 1: install Python 3.10+ and tick "Add python.exe to PATH".
echo   Option 2: put a python installation in the python\ subfolder.
echo.
pause
exit /b 1

:noout
echo.
echo [ERROR] %OUT% was not created - the build failed.
goto :fail

:fail
echo.
echo [FAILED] The build stopped. Read the errors above.
echo.
pause
exit /b 1
