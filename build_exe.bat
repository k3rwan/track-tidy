@echo off
REM Builds a standalone Track-Tidy app with PyInstaller (folder mode).
REM Run this from inside your mp3-tagger folder (same place as interface.py).
REM
REM Using --onedir (a folder) instead of --onefile: a single-file .exe has to
REM re-extract itself into a temp folder every time it starts, and antivirus
REM software sometimes deletes files from that extraction (breaking Tkinter
REM with a "Tcl data directory not found" error). A folder avoids that entirely.

echo Installing/updating PyInstaller...
pip install pyinstaller tkinterdnd2 --break-system-packages -q

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
  interface.py

echo.
echo Done. Your app folder is: dist\Track-Tidy\
echo Run dist\Track-Tidy\Track-Tidy.exe to test it directly.
pause
