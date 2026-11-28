@echo off
setlocal
cd /d "%~dp0"
title Build Zephyr-Setup.exe

where ISCC >nul 2>nul
if errorlevel 1 (
  echo Inno Setup is not installed.
  echo 1. Download it once, free: https://jrsoftware.org/isdl.php  (click through with defaults)
  echo 2. Run this script again.
  pause
  exit /b 1
)
if not exist "..\release\Zephyr.exe" (
  echo release\Zephyr.exe not found. Run build_exe.bat in the folder above first.
  pause
  exit /b 1
)


echo Compiling installer...
ISCC setup.iss
if errorlevel 1 (
  echo Build failed. Scroll up for the error.
  pause
  exit /b 1
)
echo.
echo Done. installer\Output\Zephyr-Setup.exe is ready to share and run.
explorer Output
pause
