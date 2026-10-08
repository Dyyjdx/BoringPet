@echo off
rem ============================================================
rem  BoringPet launcher - administrator privileges (shows UAC).
rem
rem  Only needed for the "game boost" feature, which writes to the
rem  registry. For everyday use, run b-pet.bat instead.
rem
rem  How it works: re-launch itself through UAC, then hand over to
rem  b-pet.bat.
rem
rem  ASCII-only, CRLF line endings - see the note in build_exe.bat.
rem ============================================================
setlocal
cd /d "%~dp0"

rem Already elevated (high integrity)? Continue. Otherwise re-launch via UAC.
whoami /groups | findstr /i "S-1-16-12288" >nul 2>&1
if %errorlevel% neq 0 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

call "%~dp0b-pet.bat"
exit /b %errorlevel%
