@echo off
setlocal
cd /d "%~dp0"
title Building Zephyr.exe

set PY=
where python >nul 2>nul && set PY=python
if not defined PY where py >nul 2>nul && set PY=py -3
if not defined PY (
  for /d %%d in ("%LocalAppData%\Python\pythoncore-*-64") do if exist "%%d\python.exe" set PY="%%d\python.exe"
)
if not defined PY (
  for /d %%d in ("C:\Python3*") do if exist "%%d\python.exe" set PY="%%d\python.exe"
)

if not defined PY (
  echo Python was not found on PATH or in its usual install folders.
  echo Install Python 3.12 or 3.13 from python.org and tick "Add Python to PATH", then run this again.
  echo If it IS installed, open its folder in File Explorer, copy the full path, and tell me - I will hardcode it.
  pause
  exit /b 1
)
echo Using Python at: %PY%

rem --- create a self-contained virtual environment inside THIS project folder,
rem     so dependencies live here instead of in your global Python install ---
if not exist ".venv\Scripts\python.exe" (
  echo Creating a project-local virtual environment in ".venv" ...
  %PY% -m venv .venv
  if errorlevel 1 goto fail
)
set VENV_PY=.venv\Scripts\python.exe

echo Installing build tools and libraries into .venv (your global Python is left untouched)...
"%VENV_PY%" -m pip install --upgrade pip
"%VENV_PY%" -m pip install --upgrade pyinstaller PySide6 requests "yt-dlp[default]" imageio-ffmpeg
if errorlevel 1 goto fail

echo Building Zephyr.exe (takes a few minutes)...
"%VENV_PY%" -m PyInstaller --noconsole --onefile --clean --name Zephyr --icon icon.ico --add-data "icon.ico;." --collect-all yt_dlp --collect-all yt_dlp_ejs pydm.py
if errorlevel 1 goto fail

if not exist release mkdir release
copy /y dist\Zephyr.exe release\Zephyr.exe >nul
copy /y icon.ico release\icon.ico >nul
xcopy /e /i /y browser-extension release\browser-extension >nul

rem --- helpers that live NEXT TO Zephyr.exe in the "runtime" folder (keeps the exe small and fast to start) ---
if not exist release\runtime mkdir release\runtime
echo Adding ffmpeg - needed to merge video and audio...
"%VENV_PY%" -c "import imageio_ffmpeg,shutil; shutil.copyfile(imageio_ffmpeg.get_ffmpeg_exe(), r'release\runtime\ffmpeg.exe')"
if errorlevel 1 echo WARNING: could not add ffmpeg - merged video downloads will not work.
if not exist release\runtime\deno.exe (
  echo Downloading Deno - the JavaScript engine that YouTube downloads need...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing 'https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip' -OutFile 'release\runtime\deno.zip'; Expand-Archive -Force 'release\runtime\deno.zip' 'release\runtime'; Remove-Item 'release\runtime\deno.zip' } catch { Write-Host 'WARNING: could not download Deno - YouTube downloads may fail.' }"
)

echo.
echo Done! Your program is in the "release" folder: Zephyr.exe
echo (Built with a project-local .venv - your global Python packages were left alone.)
explorer release
pause
exit /b 0

:fail
echo.
echo Build failed. Scroll up to see the error. If PySide6 would not install, use Python 3.12 or 3.13.
pause
exit /b 1
