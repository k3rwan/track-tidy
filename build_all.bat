@echo off
setlocal

echo ===============================================
echo   Track Tidy - Full build (exe + installer)
echo ===============================================
echo.

REM --- Sanity checks ---
if not exist "venv_build" (
    echo [ERROR] venv_build not found in this folder.
    echo Create it first with:
    echo   py -3.12 -m venv venv_build
    echo   venv_build\Scripts\Activate.ps1
    echo   pip install -r requirements.txt pyinstaller
    echo.
    pause
    exit /b 1
)

if not exist "interface.py" (
    echo [ERROR] interface.py not found. Run this script from inside mp3-tagger.
    pause
    exit /b 1
)

if not exist "ffmpeg.exe" (
    echo [WARNING] ffmpeg.exe not found next to this script.
    echo WAV-to-MP3 conversion won't work for people who don't already have FFmpeg installed.
    echo Copy your ffmpeg.exe into this folder if you want it bundled in the installer.
    echo.
)

REM --- Clean previous build artifacts ---
echo Cleaning previous build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
del /q *.spec >nul 2>nul

REM --- Build the app with PyInstaller (using the dedicated Python 3.12 venv) ---
echo.
echo Activating build environment (venv_build)...
call venv_build\Scripts\activate.bat

echo.
echo Installing/updating dependencies...
pip install -r requirements.txt pyinstaller --quiet

echo.
echo Building the app (this can take a minute)...
pyinstaller --onedir --windowed --noconfirm ^
  --name "Track-Tidy" ^
  --icon "assets\track-tidy_icon.ico" ^
  --add-data "assets\track-tidy_icon.ico;." ^
  --add-data "assets\track-tidy_icon.png;." ^
  --add-data "assets\fart.wav;." ^
  --add-data "assets\success.wav;." ^
  --collect-all tkinterdnd2 ^
  --collect-all keyring ^
  interface.py

if not exist "dist\Track-Tidy\Track-Tidy.exe" (
    echo.
    echo [ERROR] Build failed - Track-Tidy.exe was not found in dist\
    pause
    exit /b 1
)

echo.
echo App built successfully: dist\Track-Tidy\Track-Tidy.exe

REM --- Compile the installer with Inno Setup (if installed) ---
echo.
echo Looking for Inno Setup Compiler (ISCC.exe)...
set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 7\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 7\ISCC.exe"

if "%ISCC%"=="" (
    for /d %%D in ("%ProgramFiles%\Inno Setup*") do if exist "%%D\ISCC.exe" set "ISCC=%%D\ISCC.exe"
)
if "%ISCC%"=="" (
    for /d %%D in ("%ProgramFiles(x86)%\Inno Setup*") do if exist "%%D\ISCC.exe" set "ISCC=%%D\ISCC.exe"
)

if "%ISCC%"=="" (
    echo [WARNING] Inno Setup Compiler not found automatically.
    echo Install it from https://jrsoftware.org/isdl.php, then open installer.iss
    echo manually and press F9 to compile - or just re-run this script afterwards.
    pause
    exit /b 0
)

echo Found: %ISCC%
echo.
echo Compiling the installer...
"%ISCC%" installer.iss

echo.
echo ===============================================
echo   Done!
echo   App:       dist\Track-Tidy\Track-Tidy.exe
echo   Installer: installer_output\Track-Tidy-Setup.exe
echo ===============================================
pause
