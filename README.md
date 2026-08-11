# Track-Tidy

A Windows desktop app that cleans up and tags your audio files automatically.

Given a folder of tracks named `Artist - Title.ext`, Track-Tidy:

- Parses the filename to fill in **Artist** / **Title** tags
- Fetches a matching **cover** online from iTunes, Spotify, and/or SoundCloud - each independently toggled in Settings (iTunes and SoundCloud on, Spotify off, by default), tried in that order until one finds a match - click a thumbnail to see it full-size, and import/remove a cover manually from that popup.
- Converts any non-MP3 format (WAV, FLAC, AAC, M4A, OGG, WMA, AIFF, OPUS...) to **MP3 (320 kbps)** before tagging - can be turned off in Settings, in which case `.wav` files (the only format taggable without converting) are skipped instead
- Right-click a row for more: Info, Rescan this file (or "Rescan selected" with multiple rows picked), Open file location, Move up/down, Report track... (sends the row's details to the developer), Remove from list
- "Only show tracks with no cover match" filter (Advanced) to quickly find what still needs attention
- Flattens folders, removes empty subfolders, and detects duplicate files
- Keeps a permanent history of every file it processes in `%APPDATA%\Track-Tidy\history.jsonl` - browsable (and restorable) from Settings ("View processing history")
- Checks for updates on startup and can download/install them directly from the app
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

iTunes needs no credentials and works out of the box. To use Spotify and/or
SoundCloud as cover sources, enter their API credentials via Settings →
"Spotify credentials..." / "SoundCloud credentials..." (a Client ID /
Client Secret from your own registered app - both are free, self-service:
[Spotify](https://developer.spotify.com/dashboard),
[SoundCloud](https://soundcloud.com/you/apps), Artist Pro account required
for the latter). This writes `spotifyClientID.txt` / `spotifyClientSecret.txt`
and `clientID.txt` / `clientSecret.txt` next to the app (in
`%APPDATA%\Track-Tidy\`, not tracked by git) - you can also create those
files yourself instead.

It also relies on `ffmpeg.exe` being present next to the script for format conversion.

## Running from source

```bash
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python interface.py
```
