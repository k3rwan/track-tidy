# Track-Tidy

A Windows desktop app that cleans up and tags your audio files automatically.

Given a folder of tracks named `Artist - Title.ext`, Track-Tidy:

- Parses the filename to fill in **Artist** / **Title** tags
- Fetches a matching **cover** online (iTunes / SoundCloud APIs)
- Converts any non-MP3 format (WAV, FLAC, AAC, M4A, OGG, WMA, AIFF, OPUS...) to **MP3 (320 kbps)** before tagging
- Flattens folders, removes empty subfolders, and detects duplicate files
- Keeps a permanent history of every file it processes (old/new filename and tags) in `%APPDATA%\Track-Tidy\history.jsonl`
- Dark mode (Settings tab): Light or Dark

## How it works

1. Choose a folder
2. Click **Scan** — each file appears as it's analyzed, with a suggested pre-checked "Apply" state
   - Unchecked: shows the file's *current* tags
   - Checked: shows the *suggested* tags (inferred from the filename + fetched cover)
3. Click a checkbox/cell to toggle it, or double-click Title/Artist to edit manually
4. Click **Apply**

## Project layout

| File | Purpose |
|---|---|
| `track_tidy.py` | Core logic: filename parsing, tag reading/writing, format conversion, cover search (see the module docstring for the full breakdown) |
| `interface.py` | Tkinter GUI on top of `track_tidy.py` |
| `Launch Track Tidy.bat` | Launches the app from the local `venv` |
| `build_exe.bat` / `build_all.bat` | Build a standalone `.exe` with PyInstaller |
| `installer.iss` | Inno Setup script to package the `.exe` into a Windows installer |

## Setup

Track-Tidy needs SoundCloud API credentials to fetch covers. Create these files
next to the app (in `%APPDATA%\Track-Tidy\`), not tracked by git:

- `clientID.txt` — your SoundCloud client ID
- `clientSecret.txt` — your SoundCloud client secret

It also relies on `ffmpeg.exe` being present next to the script for format conversion.

## Running from source

```bash
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python interface.py
```
