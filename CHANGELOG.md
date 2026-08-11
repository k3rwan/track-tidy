# Changelog

All notable changes to this project are documented here. Format loosely based
on [Keep a Changelog](https://keepachangelog.com/).

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
