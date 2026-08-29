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
    echo "Format conversion (WAV->MP3/AIFF) and WAV RIFF INFO tag writing won't work for"
    echo "people who don't already have FFmpeg installed. A plain 'brew install ffmpeg' binary"
    echo "is NOT enough on its own - it's dynamically linked against other Homebrew-installed"
    echo "libraries and will fail to launch without them. Either download a genuinely static"
    echo "build (e.g. evermeet.cx), or run 'brew install ffmpeg dylibbundler' and:"
    echo "  dylibbundler -od -b -x ./ffmpeg -d ./ffmpeg-libs -p \"@executable_path/ffmpeg-libs/\""
    echo "(see build-macos.yml, which does exactly this in CI) before placing the result here."
    echo
fi

if [ ! -f "fpcalc" ]; then
    echo "[WARNING] No 'fpcalc' binary (macOS build, no extension) found next to this script."
    echo "The optional AcoustID audio-identification fallback won't work without it. Download"
    echo "a prebuilt binary from https://github.com/acoustid/chromaprint/releases and place it"
    echo "here as './fpcalc'. UNVERIFIED whether it needs the same dylibbundler treatment as"
    echo "Homebrew's ffmpeg above (not tested on real macOS) - check with 'otool -L ./fpcalc'"
    echo "for any /opt/homebrew or /usr/local paths before trusting it works standalone."
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
if [ -f "assets/track-tidy_icon.icns" ]; then
    ICON_ARGS=(--icon "assets/track-tidy_icon.icns")
else
    echo "[WARNING] assets/track-tidy_icon.icns not found - building without a custom app icon."
    echo "Generate one from assets/track-tidy_icon.png with 'iconutil' (see Apple's docs) if you want one."
fi

pyinstaller --windowed --noconfirm \
  --name "Track-Tidy" \
  "${ICON_ARGS[@]}" \
  --add-data "assets/track-tidy_icon.ico:assets" \
  --add-data "assets/track-tidy_icon.png:assets" \
  --add-data "assets/fart.wav:assets" \
  --add-data "assets/success.wav:assets" \
  --add-data "assets/extractor-before-dark.png:assets" \
  --add-data "assets/extractor-before-light.png:assets" \
  --add-data "assets/extractor-after-dark.png:assets" \
  --add-data "assets/extractor-after-light.png:assets" \
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
    # If ffmpeg was prepared with dylibbundler (e.g. from a Homebrew
    # install - see build-macos.yml), its resolved dependency .dylibs live
    # alongside it in ./ffmpeg-libs, and ffmpeg's own link paths point at
    # "@executable_path/ffmpeg-libs/" - that folder must end up in the
    # exact same place relative to the bundled binary (Contents/MacOS/) or
    # ffmpeg won't find them at runtime.
    if [ -d "ffmpeg-libs" ]; then
        cp -R ffmpeg-libs "dist/Track-Tidy.app/Contents/MacOS/ffmpeg-libs"
    fi
fi

if [ -f "default_credentials.json" ]; then
    echo
    echo "Bundling shared default credentials into the .app..."
    cp default_credentials.json "dist/Track-Tidy.app/Contents/MacOS/default_credentials.json"
fi

if [ -f "fpcalc" ]; then
    echo
    echo "Bundling fpcalc into the .app..."
    cp fpcalc "dist/Track-Tidy.app/Contents/MacOS/fpcalc"
    chmod +x "dist/Track-Tidy.app/Contents/MacOS/fpcalc"
fi

# PyInstaller already ad-hoc-signs the .app as part of the build (arm64
# refuses to even launch an unsigned binary) - but every cp above just
# dropped new files straight into Contents/MacOS/ afterwards, which
# invalidates that signature's sealed resource list without any error or
# warning at build time. A same-machine launch (see build-macos.yml's own
# launch check) doesn't care and still runs fine, but a REAL downloaded
# .dmg gets the quarantine attribute set on open, and Gatekeeper's much
# stricter check on quarantined + Apple Silicon then rejects the broken
# signature outright ("'Track-Tidy' is damaged and can't be opened" - no
# "Open Anyway" option at all, unlike the milder unidentified-developer
# prompt an unsigned-but-INTACT app would normally get). Real report: a
# colleague's M4 Mac hit exactly this on the 0.26.2 .dmg. Re-signing here,
# after every file is already in place, fixes it - still not a real
# Developer ID signature or notarization (see CLAUDE.md), so first launch
# still needs the right-click-Open workaround the README documents, but at
# least that workaround is reachable again instead of a hard block.
echo
echo "Re-signing the app (ad-hoc) now that every extra file is bundled..."
codesign --force --deep --sign - "dist/Track-Tidy.app"

echo
echo "Building the .dmg..."
mkdir -p installer_output
APP_VERSION="$(python3 -c "import track_tidy; print(track_tidy.APP_VERSION)")"
DMG_NAME="installer_output/Track-Tidy-Setup-${APP_VERSION}.dmg"
rm -f "$DMG_NAME"
# hdiutil create can fail with a transient "Resource busy" right after
# PyInstaller finishes writing the .app (seen in CI - something else, e.g.
# Spotlight, briefly holding a lock on the freshly-written folder) - retry
# a few times with a short pause instead of failing the whole build over it.
for attempt in 1 2 3; do
    if hdiutil create -volname "Track Tidy" -srcfolder "dist/Track-Tidy.app" -ov -format UDZO "$DMG_NAME"; then
        break
    fi
    if [ "$attempt" = 3 ]; then
        echo "[ERROR] hdiutil create failed after 3 attempts."
        exit 1
    fi
    echo "hdiutil create failed (attempt $attempt/3) - retrying in 5s..."
    sleep 5
done

echo
echo "==============================================="
echo "  Done!"
echo "  App:   dist/Track-Tidy.app"
echo "  Image: $DMG_NAME"
echo "==============================================="
