# Changelog

All notable changes to this project are documented here. Format loosely based
on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- "Check for updates" button in Settings - reports back either way (up to
  date / update available / check failed), unlike the silent startup check.
- The installer now detects an existing installation (via a fixed AppId) and
  asks to update instead of silently reinstalling with no explanation. Lets
  the user cancel instead of proceeding.
- Click a cover thumbnail in the table to see it full-size in a popup
  (click the popup, or press Escape, to close it). Hovering a cover that has
  one shows a small magnifier badge as a hint.
- "View processing history" button in Settings: a table of every file ever
  processed, most recent first. Each entry shows its old file/tags, with the
  applied (new) file/tags indented right below as a child row.
- "Restore selected" in the history window: reverts a file's tags and cover
  back to what they were before that run - writes to the file immediately.
  Requires the entry to have been logged by this version or later (older
  entries don't have enough information saved to locate the file again).
- Cover zoom popup: "Import cover..." (pick any image file) and "Remove
  cover" buttons, both writing straight to the file on disk immediately.
  The popup now also opens for a file with no cover yet, to import one.

### Removed
- The "Default (follow Windows)" appearance option - only Light/Dark remain.
  An old saved "system" preference from before falls back to Light.

### Fixed
- Switching between Light and Dark briefly left the window a few pixels off
  from its ideal size (the two ttk themes behind them use different widget
  padding) - the window now re-sizes itself immediately and correctly every
  time the theme changes, instead of keeping a stale/mismatched size.
- Dark mode's button/entry padding was much taller than the native theme's
  (26px difference in the window's total height), causing a visible jump
  when switching themes even after the resize above was made clean. Trimmed
  down to within 2px of the native size.
- Switching to Dark still showed a small downward shift even after the
  padding fix above (2px difference, but any window resize at all was
  visible). The window no longer resizes when the theme changes - the 2px
  of slack is absorbed silently instead.
- The dark title bar's forced repaint used `SetWindowPos(..., SWP_FRAMECHANGED)`
  - a real resize/reposition call even as a no-op, which Windows' window
  animations could turn into a visible jump on its own. Switched to
  `RedrawWindow`, a pure repaint call with no size/position semantics.
- A popup wider than the main window (e.g. the history table) could get
  centered partly or fully off-screen. All dialogs now share one centering
  helper that clamps to the screen bounds.
- The cover zoom popup's Import/Remove buttons were invisible: they were
  created with the dialog as their Tk parent but packed inside a separate
  frame via `pack(in_=...)`, which only moves geometry management, not the
  actual widget hierarchy - Tk never rendered them. Created directly inside
  their container frame instead.

### Changed
- The progress bar's completion text is now "Done," instead of "Done ✅" -
  a plain character instead of a colorful emoji that doesn't adapt to theme.

## [0.3]

### Fixed
- The update check pointed at `k3rwan/track-tidy` (the private source repo,
  always 404 for anyone without access) instead of `k3rwan/track-tidy-releases`
  (the public, installer-only repo colleagues actually download from).

### Added
- Auto-update check on startup: compares the running version against the
  latest GitHub release and, if a newer one exists, asks to open the
  download page.
- Installer filename now includes the version (`Track-Tidy-Setup-v{version}.exe`).
- Dark mode. New "Appearance" section in Settings: Default (follows Windows'
  own light/dark setting), Light, or Dark - persisted across restarts. Covers
  the whole app, including the native title bar (Windows 10 1809+ / 11).

### Changed
- `APP_VERSION` in `track_tidy.py` is now the single source of truth for the
  version shown in the GUI (previously a separately hardcoded string).
  `installer.iss` still needs its own `MyAppVersion` bumped to match on
  release, but at least isn't duplicated within that file anymore.

## [0.2]

### Fixed
- Tolerate a `(feat. X)` suffix when validating iTunes cover matches (iTunes
  often includes the featured artist in the title even when the file's own
  tags/filename don't).
- Retry on iTunes `HTTP 429` (rate limit) instead of silently giving up on a
  track that would otherwise have matched.
- Normalize iTunes/SoundCloud response text to NFC Unicode form - fixed both
  a crash on some accented titles and false match rejections caused by
  visually-identical but differently-encoded text (NFD vs NFC).
- Sync installer version (`installer.iss`) with the version shown in the GUI.

### Added
- Unit tests (`tests/test_track_tidy.py`) covering filename parsing and
  cover-match logic - 32 tests, no external test framework needed.
- `requirements.txt` for a reproducible install (previously undocumented,
  and missing `tkinterdnd2`).
- `CHANGELOG.md` (this file).
- A permanent history log: every file that's actually processed (tagged
  and/or renamed) gets one line appended to `%APPDATA%\Track-Tidy\history.jsonl`
  recording its old filename/tags, new filename/tags, whether the cover was
  updated, and whether it was converted to MP3.
- First public GitHub release (`v0.2`), with the installer attached.

### Changed
- Bumped the version shown in the GUI to `v0.2`.

### Investigated
- Parallelized the cover-search scan (`ThreadPoolExecutor`, 6 workers) to
  speed up scanning large libraries. Reverted after testing on a real 99-track
  library: firing requests that fast got the scan rate-limited by iTunes
  (`HTTP 429`, no retry at the time), causing tracks with a real match to end
  up with no cover. Scanning is sequential again until the retry/backoff
  fix above proves itself; parallel scanning may be worth revisiting on top
  of it.

## [0.1] - Initial release

### Added
- Initial version of Track-Tidy: tags audio files from their filename,
  fetches covers from iTunes/SoundCloud, converts non-MP3 formats to MP3.
- `README.md` with project overview, setup, and usage instructions.
