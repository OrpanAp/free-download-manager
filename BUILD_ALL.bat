@echo off
setlocal
cd /d "%~dp0"
title Zephyr - full build

echo ===============================================
echo  Step 1 of 2: building Zephyr.exe
echo ===============================================
call build_exe.bat
if not exist "release\Zephyr.exe" (
  echo.
  echo Stopped: release\Zephyr.exe was not created. Fix the error above and run this again.
  pause
  exit /b 1
)

echo.
echo ===============================================
echo  Step 2 of 2: building Zephyr-Setup.exe
echo ===============================================
cd installer
call build_installer.bat
cd ..

echo.
echo ===============================================
echo  All done.
echo  - Portable app:  release\Zephyr.exe
echo  - Installer:     installer\Output\Zephyr-Setup.exe
echo ===============================================
pause
