@echo off
rem ============================================================
rem  BoringPet build script
rem
rem  Double-click this file. It produces:
rem    dist\BoringPet\        <- the runnable folder
rem    dist\BoringPet.zip     <- what you send to people
rem  See the packaging guide for the full story.
rem
rem  Usage:
rem    build_exe.bat          normal build (refuses to eat a live install)
rem    build_exe.bat force    delete dist\BoringPet even if it has data
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
rem Prefer a real Anaconda install over whatever `where python` finds
rem first (e.g. a sandbox/runtime python whose PySide6 DLLs may not load
rem on a normal machine). Adjust the path if Anaconda lives elsewhere.
if not defined PY if exist "D:\Anaconda\python.exe" set "PY=D:\Anaconda\python.exe"
if not defined PY for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set "PY=%%i"
if not defined PY goto :nopython

echo [1/6] Installing dependencies...
"%PY%" -m pip install -r requirements.txt || goto :fail
rem Plain "pip install pyinstaller": no --upgrade, so an already-installed
rem PyInstaller is reused instead of hitting the network on every build.
"%PY%" -m pip install pyinstaller || goto :fail

echo.
echo [2/6] Checking dist\BoringPet is safe to clean...
rem ------------------------------------------------------------------
rem SAFETY NET
rem dist\BoringPet is where the build output goes - but it is also a very
rem convenient place to actually RUN the app from. If someone has been
rem using it, it holds real recordings, music, memory and settings, and
rem the "rmdir /s /q dist" below would destroy all of it silently.
rem So: if it looks like a live install, stop and say so.
rem ------------------------------------------------------------------
if /i "%~1"=="force" goto :doclean
set "DIRTY="
if exist "dist\BoringPet\settings.json" set "DIRTY=1"
if exist "dist\BoringPet\chat_state.json" set "DIRTY=1"
if exist "dist\BoringPet\memory\memories.json" set "DIRTY=1"
if exist "dist\BoringPet\memory\raw_history.json" set "DIRTY=1"
dir /b "dist\BoringPet\recordings\*.mp4" >nul 2>&1 && set "DIRTY=1"
if defined DIRTY goto :dirtydist

:doclean
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo [3/6] Running PyInstaller, this usually takes a few minutes...
"%PY%" -m PyInstaller BoringPet.spec --noconfirm || goto :fail

echo.
echo [4/6] Preparing the release folder...
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
echo [5/6] Creating the zip...
rem tar.exe ships with Windows 10 1803+ and writes spec-compliant zip paths
rem (forward slashes). PowerShell's Compress-Archive writes backslashes,
rem which some non-Windows unzip tools turn into literal filename chars.
if exist "dist\BoringPet.zip" del /q "dist\BoringPet.zip"
pushd dist
tar.exe -a -c -f BoringPet.zip BoringPet
set "ZIPRC=%errorlevel%"
popd
if not "%ZIPRC%"=="0" (
    echo   [WARN] tar failed, zip it yourself: right-click dist\BoringPet
    echo          - Send to - Compressed folder
)

echo.
echo [6/6] Done.
echo.
echo   Folder: %OUT%
if exist "dist\BoringPet.zip" (
    for %%A in ("dist\BoringPet.zip") do echo   Zip   : %%~fA  (%%~zA bytes)
)
echo.
echo   Send the ZIP (or the whole folder). Before you do, double-check
echo   there is NO settings.json in it - that file holds the API key.
echo.
echo   Remind the recipient: on first run, right-click the pet,
echo   open Settings, and enter their own API key before chatting.
echo.
pause
exit /b 0

:dirtydist
echo.
echo [STOP] dist\BoringPet looks like a REAL installation, not build output.
echo        It contains settings.json / memory / recordings.
echo.
echo   Building would DELETE all of it, so this script stopped instead.
echo.
echo   Recommended fix - keep using that copy as your daily app:
echo       move "dist\BoringPet" "D:\BoringPetApp"
echo   then run this script again. Your recordings and music stay safe.
echo.
echo   Only if you really do not need that data:
echo       build_exe.bat force
echo.
pause
exit /b 1

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
