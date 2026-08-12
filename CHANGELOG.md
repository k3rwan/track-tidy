# Changelog

All notable changes to this project are documented here. Format loosely based
on [Keep a Changelog](https://keepachangelog.com/).

## [0.9]

### Added
- "Fix Artist/Title and search again" popup for tracks the scan couldn't
  find a cover for - correct a misdetected artist or title and retry
  the cover search without leaving the app.
- A "- - - N track(s) found - - -" summary row while searching/filtering
  the track table, and Ctrl+A now selects every currently visible track.
- Confirmation popup before Apply, explaining that it will overwrite the
  original artist/title/cover info (recoverable from history).

### Changed
- Cover-source summary is now shown at the end of the scan instead of at
  the end of Apply.
- iTunes searches now run in parallel during a scan (Spotify/SoundCloud
  stay sequential), noticeably speeding up large scans.
- "Check for updates" and "View processing history" buttons are stacked
  vertically again.
- Checkboxes/radio buttons and the scrollbar now look identical in light
  and dark mode (previously light mode used Windows' native controls,
  dark mode different clam-drawn ones); the checkbox's checked mark is
  now a custom-drawn checkmark, and the box is smaller than the first
  redesign.
- The scan summary and "no cover found" popups are now shown one after
  another instead of at the same time.

### Fixed
- iTunes searches now retry on a transient HTTP 403 (previously only
  HTTP 429 was retried), fixing some tracks that failed to find a cover
  on the first attempt.

## [0.8]

### Added
- Spotify as a third cover source alongside iTunes and SoundCloud, each
  independently toggled via three checkboxes in Settings -> Sources
  (iTunes and SoundCloud on, Spotify off, by default), tried in that
  order until one finds a match. iTunes and Spotify are mutually
  exclusive (checking Spotify silently unchecks iTunes; re-checking
  iTunes shows an explanatory popup and reverts).
- Search bar and a Reset button (with confirmation) in the processing
  history window.
- "Show log section" toggle in Settings -> Behavior (off by default) -
  the Log section in the Tagger tab is hidden entirely unless enabled.
- Internet connectivity indicator ("Online"/"Offline") in Settings,
  checked at launch (with a one-time warning popup if offline) and
  every 30s while the app runs.

### Changed
- Track table visual overhaul: taller rows, bigger cover thumbnails,
  wider Title/Artist columns, narrower MP3 column, wider window,
  softer selection highlight in light mode, and a tooltip on
  Title/Artist text that's too long to fit its column.
- Settings reorganized: Sources (the three checkboxes plus SoundCloud/
  Spotify credential buttons) is now its own category, separate from
  Behavior.
- "Advanced" section toggle is now a gear icon instead of the word
  "Advanced".
- Apply button redesigned: a separate "N tracks selected" label plus a
  wider "Apply" button on the same row, replacing the old
  "Apply changes - N/total" button text.
- "Extracter" tab renamed to "Extractor" (typo).
- Dark mode: refreshed color palette (distinct background/panel/
  list-area/list-header shades) and removed the dated 3D bevel
  borders (flat 1px borders, or none at all, relying on color
  contrast instead).

### Fixed
- Artist/title matching now tolerates missing spaces in artist names
  (e.g. a SoundCloud handle like "SpicyMarket") and dash-form generic
  mix suffixes (e.g. "- Radio Edit" instead of "(Radio Edit)") that
  previously caused a real match to be rejected.
- "Select all" (the header checkbox) no longer selects tracks hidden
  by the "Only show tracks with no cover match" filter.
- The end-of-Apply summary now correctly counts Spotify-sourced
  covers (previously miscounted as "kept original cover" or "no cover
  at all").
- Scan progress text no longer zero-pads ("Scan - 5/100" instead of
  "Scan - 05/100").
- Dark mode: Checkbutton/Radiobutton hover no longer shows
  near-unreadable low-contrast text.
- Fixed a Windows font-fallback bug that made the checkbox glyph
  render differently in the table header vs. table rows.
- Switching Light/Dark theme no longer causes a visible layout shift
  the very first time it's switched (native theme metrics are now
  warmed up at startup).
- The version label no longer overlaps the Apply button.

## [0.7]

### Added
- "Search covers on SoundCloud" and "Convert everything to MP3 (320 kbps)"
  toggles in Settings, both on by default. Turning off SoundCloud skips it
  entirely (no auth attempt either). Turning off auto-convert makes `.wav`
  files - the only format taggable without converting - get skipped during
  scanning instead of being tagged in place; a warning explains this before
  the change takes effect.
- "Only show tracks with no cover match" checkbox in Advanced: filters the
  table down to rows with no online match, combinable with the search box.
  Hidden tracks with a cover are summarized as a
  "- - - N track(s) with cover - - -" row at the bottom instead of just
  disappearing.
- Right-click menu: multi-select support for rescanning - selecting several
  rows first turns "Rescan this file" into "Rescan selected (N)", running
  one shared search instead of one per file.
- "Apply" now warns first if the search box and/or the no-cover checkbox is
  currently hiding some scanned files, since it always processes everything
  regardless of what's visible.

### Changed
- iTunes is now always queried before SoundCloud (previously reversed for
  remixes) - conserves SoundCloud's request quota, since iTunes turned out
  to find the correct cover for nearly every remix in practice.
- SoundCloud Client ID/Secret moved out of the Settings tab into their own
  popup dialog, opened via a "SoundCloud credentials..." button.
- Settings tab reordered: Appearance, Behavior, SoundCloud account, Check
  for updates, View processing history.

### Removed
- "Delete album tag" - no longer wanted.

### Fixed
- iTunes cover matching rejected several kinds of otherwise-correct
  matches: filename-sanitized characters (e.g. "BLOND_ISH" vs the real
  "BLOND:ISH"), a featured artist credited only in the returned title
  instead of the artist field, missing accents (e.g. "Bolemvn" vs
  "Bolémvn"), and - for heavily-remixed songs - the specific remix getting
  buried outside the top search result. iTunes search now also checks up
  to 10 candidates (not just the first), strips punctuation from the query
  (which was hurting relevance ranking), and retries with the remix
  qualifier kept in the query when the plain search doesn't find a match.
- A SoundCloud result containing an emoji could crash the whole search
  with a console encoding error (`log=print`, i.e. CLI usage only - the
  packaged app logs through its own Journal widget, unaffected) - added
  `safe_print()` as the module's default logger instead.
- Dark mode: every other table row's text rendered as a barely-readable
  dim grey instead of the theme's real (light) text color.

## [0.6]

### Added
- Updates can now be installed directly from the app: "Update available"
  offers to download and install right away (with a progress bar) instead
  of sending you to a browser. Falls back to the old open-in-browser flow
  if a release ever has no installer asset attached. Takes effect starting
  with the update *after* this one - whichever version you're updating
  from still uses its own (older) update-check code for this one jump.
- "Info" in the row right-click menu: a read-only summary popup (current
  vs. suggested tags, cover match source, detected mention, apply/convert
  state).

### Fixed
- Native dialogs (message boxes, including the startup update-available
  prompt) centered on the screen instead of over the app window - every
  call site now explicitly parents them to the window (or the specific
  popup) that triggered them.
- Noticeably empty gap between the "Advanced" toggle and the scan results
  table's column headers - tightened the table's top padding.

## [0.5]

### Added
- "Report track..." in the row right-click menu: sends the file name,
  current/suggested tags, cover-match status, and the existing/suggested
  cover images to a Discord webhook - a quick way to flag a problem track
  (e.g. no cover found) for the developer to investigate.
- "Rescan this file" in the row right-click menu: re-runs the online cover
  search for just that one row (e.g. after fixing SoundCloud credentials, or
  to retry a match) without rescanning the whole folder.
- "Move up" / "Move down" / "Remove from list" in the row right-click menu
  (the last one mirrors the existing Delete key).

### Fixed
- iTunes cover search never specified a store, so it defaulted to the US
  store - where a lot of French content (especially explicit-tagged rap)
  isn't licensed and returned zero results even though it's on the French
  store. Now searches the FR store.
- The table thumbnail/cover zoom popup showed no cover at all for a row
  where the file already had a good existing cover but no online match was
  found - even though the file's own cover is actually kept untouched in
  that case. The preview now matches what Apply really does.

### Changed
- Row right-click menu reorganized: Rescan this file / Open file location,
  then Move up / Move down, then Report track..., then Remove from list.

## [0.4]

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
- The progress bar's completion text is now "Done ✓" instead of "Done ✅" -
  a plain checkmark character instead of a colorful emoji that doesn't
  adapt to theme.
- History window: split the combined "Artist - Title" column into separate
  Artist and Title columns.
- History window: only the old-info (parent) row of each entry can be
  selected now - clicking (or ctrl/shift-clicking) the "Applied" child row
  selects its parent instead, since Restore always acts on the old info.
  `Ctrl+A` selects every entry. "Restore selected" now restores all
  selected entries at once, with a summary if any fail.

### Fixed (pre-v0.4 cleanup)
- `fart.wav` and `success.wav` (the UI's two sound effects) were accidentally
  matched by `.gitignore`'s `*.wav` rule and were never actually committed -
  a fresh clone couldn't build a working installer without them. Added an
  exception for these two specific files and committed them.
- Extraction (`_run_extraction`) logged with `log=print` instead of
  `log=self._append_to_journal` like every other background task. Since
  stdout is redirected to `os.devnull` in the frozen .exe, every per-file
  "Extracted: ..." line (and any per-file error) was completely invisible to
  the user - only the final summary dialog showed. Now shows in the Journal
  like everything else.

### Changed (pre-v0.4 cleanup)
- Removed `clean_filename()` from `track_tidy.py` - dead code, no longer
  called from anywhere.
- Fixed `interface.py`'s module docstring, which referenced a `tagger.py`
  file that doesn't exist (`tagger` is just the local import alias for
  `track_tidy`).
- Added section-header comments throughout `interface.py` (theme/dialog
  helpers, update check, drag and drop, table rendering, cover zoom,
  history window, etc.) - it had none beyond a handful of inconsistent
  ones, unlike `track_tidy.py`'s systematic numbered sections. Also moved
  two small methods (`_open_soundcloud_registration`, `_toggle_journal_section`)
  next to the sibling methods they actually belong with.
- Consolidated the near-duplicate "update available" dialog (startup check
  vs. manual button) into one `_offer_update()` helper.
- Consolidated 6 duplicated `threading.Thread(target=..., daemon=True); thread.start()`
  call sites into one `_run_in_background()` helper.
- Added `tagger.invalidate_soundcloud_token()` instead of `interface.py`
  reaching into `track_tidy`'s underscore-prefixed "private" module globals
  directly to force a token refresh after credentials change.

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
