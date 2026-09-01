<p align="center">
  <img src="assets/banner.png" alt="Track Tidy - auto-tags your DJ library: artist, title, and cover art, found automatically." width="700">
</p>

<p align="center">
  <a href="https://github.com/k3rwan/track-tidy/releases/download/0.28/Track-Tidy-Setup-0.28.exe"><img src="https://img.shields.io/badge/Download-0078D6?style=for-the-badge&logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI+PHBhdGggZmlsbD0iI2ZmZmZmZiIgZD0iTTMgM2g4LjV2OC41SDN6bTkuNSAwSDIxdjguNWgtOC41ek0zIDEyLjVoOC41VjIxSDN6bTkuNSAwSDIxVjIxaC04LjV6Ii8+PC9zdmc+" alt="Download for Windows"></a>
  &nbsp;
  <a href="https://github.com/k3rwan/track-tidy/releases/download/0.26.3/Track-Tidy-Setup-0.26.3.dmg"><img src="https://img.shields.io/badge/Download-000000?style=for-the-badge&logo=apple" alt="Download for macOS"></a>
</p>
<p align="center">
  <sub>Windows 0.28, macOS 0.26.3 - see <a href="../../releases/latest">Releases</a> for older versions.</sub>
</p>

<p align="center">
  <a href="https://www.virustotal.com/gui/file/36857d8adc196f1b0a8efc66ff836700e6460b62084985ded7b4399f451d7e1b/detection"><img src="https://img.shields.io/badge/Scanned%20on-VirusTotal-brightgreen?style=for-the-badge&logo=virustotal&logoColor=white" alt="Scanned on VirusTotal - view full report"></a>
</p>

> ⭐ If Track Tidy is useful to you, consider starring the repo - it's free and helps other DJs find it.

<p align="center">
  <img src="screenshots/tab-tagger.png" alt="Track Tidy's Tagger tab, showing scanned tracks ready to apply" width="600">
</p>

## What it does

Point it at a folder and it:
- Looks up the correct **artist, title, and cover art** for each track (iTunes,
  SoundCloud, and audio fingerprinting as a last resort for badly-named
  files).
- Writes the tags directly into the file - no more blank covers or
  "Track_Name (1).mp3" showing up in Rekordbox/Serato/etc.
- Optionally renames the file to a clean "Artist - Title" and converts WAV to
  AIFF (so cover art actually shows up for software that doesn't read it from
  WAV).
- Flags tracks worth a second look: no cover match found (⚠️), or an
  identification made from the audio itself rather than the filename (🎧).
- Flattens a messy folder of nested subfolders back into one flat folder
  (Extractor tab).
- Scans a library for low-quality files - low bitrate, clipping, overly
  quiet masters (Quality tab).
- Keeps a permanent history of every file it processes (browsable/restorable
  from Settings), so it never re-touches a file it's already tagged.
- Checks for updates on startup and can download/install them directly.
- Dark mode.

## Screenshots

<table align="center">
  <tr>
    <td align="center" width="33%">
      <img src="screenshots/tab-extractor.png" alt="Extractor tab: flattening a nested folder of files" width="260"><br>
      <sub><b>Extractor</b> - flattens nested subfolders</sub>
    </td>
    <td align="center" width="33%">
      <img src="screenshots/tab-quality.png" alt="Quality tab: bitrate/loudness analysis results, worst-first" width="260"><br>
      <sub><b>Quality</b> - flags low-bitrate/clipped/quiet files</sub>
    </td>
    <td align="center" width="33%">
      <img src="screenshots/tab-settings.png" alt="Settings tab: appearance, file handling, and app options" width="260"><br>
      <sub><b>Settings</b> - theme, conversion, history, updates</sub>
    </td>
  </tr>
</table>

## Is this safe?

Fair question for a random installer from someone you don't know - completely
understand the hesitation. A few things that should help:
- Track Tidy talks to the services it needs to do its job - iTunes and
  SoundCloud (to look up artist/title/cover art), AcoustID (audio
  fingerprinting, only used as a last resort when the filename alone doesn't
  give enough to search with), and GitHub (checking for updates). It never
  touches files outside the folder you point it at, and never uploads your
  actual audio anywhere.
- It also sends the developer small usage pings (your OS username, no
  other machine info) on install, on uninstall (Windows only), and after
  each scan/extraction/quality analysis. Separately, the in-app "Report
  track" button (only when you press it) sends that one track's info
  (title/artist/filename + its cover) to help fix mismatches - see
  [PRIVACY.md](PRIVACY.md) for the full breakdown of what's sent and when.
- The source code below is the real thing that gets built into the installer
  - nothing hidden.
- The Windows installer is scanned with
  [VirusTotal](https://www.virustotal.com/gui/file/36857d8adc196f1b0a8efc66ff836700e6460b62084985ded7b4399f451d7e1b/detection)
  on every release - click through for the full vendor-by-vendor report.

## Install

**Windows**
1. Go to [Releases](../../releases/latest)
2. Download `Track-Tidy-Setup-*.exe`
3. Run it and follow the prompts

**macOS** (Apple Silicon)
1. Go to [Releases](../../releases/latest)
2. Download `Track-Tidy-Setup-*.dmg`
3. Open it and drag Track Tidy into Applications
4. First launch: right-click the app → Open (it's not notarized yet, so
   Gatekeeper will warn about an "unidentified developer" the first time)

## Feedback / bugs

Open an [Issue](../../issues), or use the in-app "Report track" button on a
specific track that got tagged wrong.

---

## For developers

### How it works

1. Choose a folder
2. Click **Scan** — each file appears as it's analyzed, with a suggested pre-checked "Apply" state
   - Unchecked: shows the file's *current* tags
   - Checked: shows the *suggested* tags (inferred from the filename + fetched cover)
3. Click a checkbox/cell to toggle it, or double-click Title/Artist to edit manually
4. Click **Apply**

### Project layout

| File | Purpose |
|---|---|
| `src/track_tidy.py` | Core logic: filename parsing, tag reading/writing, format conversion, cover search (see the module docstring for the full breakdown) |
| `src/interface.py` | Tkinter GUI on top of `track_tidy.py` |

### Setup

iTunes, SoundCloud, and AcoustID all work out of the box - no accounts or API
keys to set up. The shared SoundCloud app relies on the developer's own
registered credentials, which aren't included in this source - building from
source gives you an app with no shared defaults, same as an unconfigured
user. See
`load_default_credentials()`'s docstring in `track_tidy.py` if you want to
supply your own via a local `default_credentials.json`.

It also relies on `ffmpeg`/`fpcalc` (format conversion, AcoustID
fingerprinting) being present at the project root - not included here, see
`find_ffmpeg()`/`find_fpcalc()`.

### Running from source

```bash
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python src\interface.py
```

### License

GNU General Public License v2 or later (GPL-2.0-or-later) - see
[LICENSE](LICENSE). This is required by `mutagen`, a GPL-2.0-or-later
dependency imported directly into the app. The bundled `ffmpeg` (invoked as a
separate process, not linked in) is GPLv3. See
[THIRD-PARTY-NOTICES.md](licenses/THIRD-PARTY-NOTICES.md) for every
dependency's license.
