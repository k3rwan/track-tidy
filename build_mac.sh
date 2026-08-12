#!/bin/bash
# macOS build script for Track Tidy - mirrors build_all.bat's Windows flow.
#
# UNVERIFIED: written without access to a real Mac to run it on (this
# project is normally developed on Windows). Standard PyInstaller/hdiutil
# usage, but treat the first real run as a test, not a known-good script -
# see CLAUDE.md's "Cross-platform (Windows/macOS)" section for context.
#
# 1. Create the build venv first (once):
#      python3 -m venv venv_build
#      source venv_build/bin/activate
#      pip install -r requirements.txt pyinstaller
# 2. Run this script from the project root: ./build_mac.sh

set -e

echo "==============================================="
echo "  Track Tidy - macOS build (.app + .dmg)"
echo "==============================================="
echo

if [ ! -d "venv_build" ]; then
    echo "[ERROR] venv_build not found in this folder."
    echo "Create it first with:"
    echo "  python3 -m venv venv_build"
    echo "  source venv_build/bin/activate"
    echo "  pip install -r requirements.txt pyinstaller"
    exit 1
fi

if [ ! -f "interface.py" ]; then
    echo "[ERROR] interface.py not found. Run this script from the project root."
    exit 1
fi

if [ ! -f "ffmpeg" ]; then
    echo "[WARNING] No 'ffmpeg' binary (macOS build, no extension) found next to this script."
    echo "WAV-to-MP3 conversion won't work for people who don't already have FFmpeg installed."
    echo "Download a macOS ffmpeg build (e.g. from evermeet.cx or 'brew install ffmpeg' and copy"
    echo "the binary out) and place it here as './ffmpeg' if you want it bundled."
    echo
fi

echo "Cleaning previous build..."
rm -rf build dist ./*.spec

echo
echo "Activating build environment (venv_build)..."
source venv_build/bin/activate

echo
echo "Installing/updating dependencies..."
pip install -r requirements.txt pyinstaller --quiet

echo
echo "Building the app (this can take a minute)..."
ICON_ARGS=()
if [ -f "track-tidy_icon.icns" ]; then
    ICON_ARGS=(--icon "track-tidy_icon.icns")
else
    echo "[WARNING] track-tidy_icon.icns not found - building without a custom app icon."
    echo "Generate one from track-tidy_icon.png with 'iconutil' (see Apple's docs) if you want one."
fi

pyinstaller --windowed --noconfirm \
  --name "Track-Tidy" \
  "${ICON_ARGS[@]}" \
  --add-data "track-tidy_icon.ico:." \
  --add-data "track-tidy_icon.png:." \
  --add-data "fart.wav:." \
  --add-data "success.wav:." \
  --collect-all tkinterdnd2 \
  --collect-all keyring \
  interface.py

if [ ! -d "dist/Track-Tidy.app" ]; then
    echo
    echo "[ERROR] Build failed - Track-Tidy.app was not found in dist/"
    exit 1
fi

echo
echo "App built successfully: dist/Track-Tidy.app"

if [ -f "ffmpeg" ]; then
    echo
    echo "Bundling ffmpeg into the .app..."
    cp ffmpeg "dist/Track-Tidy.app/Contents/MacOS/ffmpeg"
    chmod +x "dist/Track-Tidy.app/Contents/MacOS/ffmpeg"
fi

echo
echo "Building the .dmg..."
mkdir -p installer_output
APP_VERSION="$(python3 -c "import track_tidy; print(track_tidy.APP_VERSION)")"
DMG_NAME="installer_output/Track-Tidy-Setup-v${APP_VERSION}.dmg"
rm -f "$DMG_NAME"
hdiutil create -volname "Track Tidy" -srcfolder "dist/Track-Tidy.app" -ov -format UDZO "$DMG_NAME"

echo
echo "==============================================="
echo "  Done!"
echo "  App:   dist/Track-Tidy.app"
echo "  Image: $DMG_NAME"
echo "==============================================="
