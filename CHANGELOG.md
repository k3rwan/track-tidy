# Changelog

All notable changes to this project are documented here. Format loosely based
on [Keep a Changelog](https://keepachangelog.com/).

## [0.23.3]

### Fixed
- Windows and macOS: the app icon and both sound effects (fart/success)
  were being bundled at the wrong path inside the packaged app, so
  `os.path.exists()` silently failed to find them at runtime - Tk's
  default feather icon kept showing (in the taskbar and title bar) even
  in a build made with the correct PyInstaller flags, and the two sounds
  never played. The 0.23.2 fix only addressed a build that had omitted
  those flags entirely, not this separate path mismatch underneath.

## [0.23.2]

### Fixed
- Windows: the app icon (taskbar and window title bar) fell back to Tk's
  default feather icon instead of the Track Tidy logo. The 0.23 Windows
  installer had been built with a manual PyInstaller invocation that
  omitted the `--icon`/`--add-data` flags `build_exe.bat` normally passes,
  so the icon files never got bundled.

## [0.23]

### Added
- Smooth UI animations: new scan rows flash in, the progress bar glides
  toward its target instead of jumping, and removed rows fade out instead
  of vanishing instantly.
- An "Automatic (time of day)" appearance option (now the default for new
  installs) - light during the day, dark in the evening/night, rechecked
  periodically while the app stays open.
- A file whose name is flagged "unreleased" no longer defaults to
  auto-applying - the search still runs, but the row starts unchecked
  since there's no official release to have verified the match against.
- The Extractor tab's "Extract" button becomes "Cancel" while a folder is
  being flattened, and a Discord ping now fires either way (completed,
  cancelled, or failed).
- The "Only show tracks with no cover match" filter (and the matching
  post-scan count/Discord report) now shows a track that kept its own
  existing cover with no online match, instead of hiding it just because
  it happened to already have some cover art.
- A high no-cover-match rate (>15%, up from 10%) now automatically sends
  the whole batch of no-cover tracks to Discord (filename + current/
  detected tags), instead of a popup nudging toward reporting them one by
  one.
- A cover source (iTunes/Spotify/SoundCloud/AcoustID) getting rate-limited
  now pings Discord immediately, on top of the existing end-of-scan count.
- The "Scan complete" Discord embed now shows the no-cover-match rate as
  a percentage, not just a raw count.
- A "New install" Discord ping now includes a running count of unique
  users ever seen in the channel's history.

### Changed
- The right-click "Info" dialog is more compact (current/suggested tags
  each collapsed to one "Artist - Title" line, flags grouped together)
  and no longer shows a raw `True`/`(none)` for "Mention detected".
- "rework" is now recognized as a remix-type keyword everywhere "remix"/
  "edit"/"mix" already were, and a store's inline "/ Artist" credit
  inserted into a title (instead of the artist field) is now stripped
  before comparing - both were silently causing real, correct matches to
  be rejected.
- SoundCloud now accepts a candidate whose own remix credit already
  matches the expected artist, even if it disagrees with the (possibly
  simply wrong) remix name in the file's own filename.
- The developer's own account is no longer excluded from automatic
  Discord notifications.

### Fixed
- `exact_match()` now folds typographic/curly quotes to plain ASCII ones.
- A bare trailing version marker like `v2` (no dash) is now stripped from
  a title the same way `-v2` already was - it was otherwise polluting the
  search query itself.
- The GitHub release tag is now sanitized before being used to build the
  downloaded installer's temp file path.

## [0.22]

### Added
- A Discord notification now also fires when an existing install updates
  to a newer version (previously only a brand-new install pinged).

### Changed
- The "new install"/"app updated" Discord ping is now only marked as
  sent AFTER it actually succeeds - a failed attempt (no internet yet
  at launch, a transient block...) is retried on the next launch
  instead of being silently and permanently skipped.

### Fixed
- A file whose existing artist tag is wrong (e.g. a record label used
  as the artist instead of the real one) but whose title tag secretly
  contains "Artist - Title" now gets split correctly when the filename
  independently agrees - previously the search ran with the wrong,
  unsplit artist/title and never found a match.

## [0.21]

### Added
- Scans are now capped at 100 tracks while in beta - a folder with more
  gets an explanatory popup instead of being processed.
- A popup nudges toward the right-click "Report track..." action when
  more than 10% of a scanned folder's tracks have no cover match.

### Changed
- The AcoustID API key is no longer embedded in the source - moved to
  the same local, gitignored credentials file as the SoundCloud/Spotify/
  Discord defaults.
- Discord notifications (report track, scan complete, new install) now
  explicitly suppress mention parsing, so a filename/tag containing
  `@everyone` or a role mention can never trigger a real ping.
- Removed the "Processing complete" popup after Apply - the progress
  bar already shows "Done", so this just meant one more click through.
  The success chime still plays.

### Fixed
- Restoring a History entry for a track that originally had no
  artist/title tags now actually clears them, instead of leaving
  whatever a later Apply had written.
- The "Reset all settings to default" confirmation no longer references
  saved SoundCloud/AcoustID credentials - that personal-override UI was
  removed a while ago, so the mention was stale.

## [0.20]

### Added
- VirusTotal scan badge/link in the README, so a first-time downloader
  from Reddit/GitHub can verify the installer isn't flagged by any
  antivirus vendor.
- Scan-complete Discord notifications now include the no-cover-match
  count, the rate-limited-source count, and any source with an auth
  error - and fire even for a scan the user cancelled partway through
  (relabeled "Scan cancelled" instead of being skipped).

### Changed
- Source is now public on GitHub. Shared default credentials
  (SoundCloud/Spotify/Discord webhook) were moved out of the source
  entirely - now supplied only via a local, gitignored file or CI
  secrets - and the full git history was rewritten to scrub every past
  appearance of the old embedded values.
- Releases now live in this same repo instead of the old, separate
  `track-tidy-releases` repo, which has been retired.
- macOS CI builds now only target Apple Silicon - the Intel runner was
  chronically stuck in queue for 19-24h on every attempt.
- Repo root cleaned up: license/notice files moved into `licenses/`,
  icon/sound assets moved into `assets/`.

## [0.19]

### Added
- "Fix track file name" Settings toggle (on by default) - renames a file
  to "Artist - Title.ext" after a successful tag update.
- "Use Spotify as a cover source" Settings toggle (off by default) -
  Spotify's low rate-limit tier makes it unreliable enough to be opt-in
  now instead of always-on.
- Right-click "Fix Artist/Title..." context menu entry.

### Changed
- iTunes, Spotify, SoundCloud, and AcoustID all now proactively pace
  their own requests (roughly one every 1.5s) instead of only reacting
  after a rate limit already hit - far fewer mid-scan interruptions
  across every source.
- SoundCloud's OAuth token is now persisted across app restarts instead
  of being re-requested on every launch, easing pressure on its tight
  per-app/per-IP quota.
- Removed the end-of-scan summary popup and the Spotify rate-limit
  popup - both are journal-only now; "no cover" tracks stay reachable
  via the new right-click menu instead.
- Unchecking a row's Apply checkbox now also unchecks its Format
  (convert) checkbox, and vice versa.
- Embedded default API credentials (SoundCloud, Spotify, Discord
  webhook) are now AES-256-GCM encrypted instead of plain base64.

### Fixed
- Several real cover-matching bugs: a short artist disambiguator like
  "(UZ)" polluting the search query and derailing relevance ranking; a
  bracketed "[DJ Mix]" collection marker slipping past the DJ-mix
  compilation filter; a multi-artist SoundCloud match only requiring
  ONE of several expected artists to be present instead of all of them;
  a generic repost-account logo passed through as a real cover.
- WAV -> AIFF conversion no longer drops existing tags (genre, year,
  etc.) - FFmpeg's plain conversion silently discarded them all.
- A blank-artist SoundCloud search no longer accepts an overly loose
  match.
- Comma-spacing and a trailing period ("D.O.D." vs "D.O.D") are now
  tolerated when comparing artist/title.
- Fixed multi-file drag-and-drop only scanning the first dropped file.
- Fixed a UI double-space in "Developed by KEVZ".

## [0.18]

### Added
- Startup checks (once every 24h): whether the shared SoundCloud/Spotify/
  AcoustID credentials still authenticate, and whether iTunes/Spotify are
  blocked by a restrictive firewall/network filter - both pop up a
  warning if something's wrong.
- A warning now also pops up when iTunes hits its own rate limit during a
  scan (mirroring the existing SoundCloud one).

### Changed
- iTunes, Spotify, SoundCloud, and AcoustID are always on now - Settings
  no longer offers a way to disable any of them.
- Scan results now reveal into the table no faster than one per second
  (display pacing only - the actual scan still runs unthrottled in the
  background), instead of dozens of rows dumping in at once. Clicking
  Cancel bypasses the pacing and flushes everything instantly.
- Removed the client-side cooldown between "Report track" actions.
- Window title now shows "Track Tidy (beta)".

### Fixed
- A bare "(Remix)" is now treated as a generic, interchangeable mix
  label, same as "(Extended Mix)" - fixes titles where the store's
  actual release uses a plain "(Remix)" tag instead of the file's own
  wording.
- Fixed a title/artist mangling bug from a bare "-vN" version suffix
  with no leading space, which was mistaken for a real "Artist - Title"
  separator.
- Titles are now cleaned of DJ-pool "- <key> - <BPM>" suffixes, Windows'
  "(N)" duplicate-file marker, and a bare "M1"/"M2" mix-number marker -
  none of which are ever part of the real release.
- The Discord "Scan complete" notification is no longer sent for a no-op
  rescan (nothing new, nothing removed).
- SoundCloud/Spotify token rate limits now use a real timed cooldown
  (Spotify reads the server's actual Retry-After header; SoundCloud gets
  a self-imposed estimate, since it sends no such signal) instead of
  immediately retrying and re-tripping the limit.
- Removed a leftover debug log line that was printed for every scanned
  file.

## [0.17]

### Changed
- Removed the client-side cooldown between "Report track" actions.

### Fixed
- iTunes covers no longer come from a various-artists compilation (e.g.
  a "Best of ..." compilation) when a track only matches there and not
  on its own release - the real cover source (Spotify/SoundCloud) now
  gets a chance instead of a generic compilation cover winning first.
- Exact title matching now treats "Pt.III" and "Pt. III" (a space
  right after an abbreviating period) as equal - that one-space
  mismatch between a filename/tag and a store's listing was silently
  rejecting the real single and letting an unrelated DJ-mix
  compilation's cover win instead.

## [0.16]

### Added
- Spotify re-added as a cover source (iTunes -> Spotify -> SoundCloud
  priority) - a French rap track was found to be on Spotify but in
  neither iTunes's nor SoundCloud's index; SoundCloud was also seen
  "winning" with a non-official image before Spotify got a chance to
  offer the real cover.
- Known placeholder/wrong cover images are now automatically detected
  and stripped (perceptual-hash blacklist) when no source finds a real
  cover.
- A discreet link to Kevz's Instagram on the "Developped by KEVZ"
  credit.

### Changed
- SoundCloud no longer accepts fan-edit bootlegs (slowed + reverb,
  nightcore, sped up, etc.) or unsolicited remix/mashup/cover uploads
  as a match for the original track.
- Cover matching no longer rejects a correct release just because a
  collective/label name appears in one artist credit but not the
  other, or because of a leading "The" mismatch.
- The filename's remix title is now preferred over existing tags that
  already lost the remixer's name (moved into the artist field,
  title collapsed to a generic "(Remix)").
- Manual search actions (Rescan, Fix-no-cover search) are now disabled
  while a scan/apply is running.
- The two "convert to..." checkboxes in Settings are now truly
  mutually exclusive, instead of only the other one being greyed out
  while still silently active underneath.
- The "Processing complete" summary now reports the real conversion
  target (MP3 or AIFF) per file instead of always claiming MP3.

### Fixed
- Fixed a "feat."/"ft." artist-name splitting regex bug that could
  leave a stray ". name" fragment and break otherwise-correct matches.
- The report-track failure message no longer blames "no internet"
  when the actual cause was a short client-side cooldown.
- Fixed a double-space rendering glitch before the "KEVZ" credit text.

## [0.15]

### Added
- "Remove cover" added to the right-click menu on the cover thumbnail -
  writes straight to disk immediately, same as the zoom popup's button.
- A popup now warns when SoundCloud or Spotify credentials are
  configured but authentication actually fails (e.g. an expired/wrong
  client) - previously only visible as a line buried in the log.
- Apply now shows a popup listing exactly which file(s) couldn't be
  processed and why (most commonly corrupted audio data), instead of
  that only ever showing up in the log.

### Changed
- One corrupted or otherwise unprocessable file no longer aborts the
  rest of an Apply batch - every file is now handled independently.
- Right-click "Rescan" no longer re-prompts for Artist/Title - it
  searches directly with whatever the table is already showing for
  that row (its override if already corrected, otherwise the detected
  value).
- Editing Title/Artist on an already-Applied row now shows a pending
  checkbox instead of the "done" checkmark, until the next Apply
  actually catches up.
- AcoustID lookups now use HTTPS instead of plain HTTP - cut down
  noticeably on "Connection aborted" resets seen in real scans.
- iTunes searches now share a scan-wide rate-limit cooldown, and run
  with lower concurrency - a real large-batch scan was hitting a
  genuine, sustained HTTP 429 that kept getting hammered independently
  by every concurrent/sequential search.

## [0.14]

### Added
- Scan summary now shows how many tracks were identified via AcoustID
  (audio fingerprinting), and which cover sources were actually
  searched vs. disabled in Settings.
- "Reset all settings to default" button in Settings.
- Tracks identified via AcoustID (rather than filename/tags) are
  flagged with a headphone marker in the table until reviewed - audio
  fingerprinting can still confidently misidentify a track between
  two stylistically similar songs.
- The developer now gets a Discord notification each time a scan
  finishes, in addition to the existing new-install notification.

### Changed
- AcoustID now works out of the box - no more registering your own
  API key, it's shared automatically.
- AIFF files no longer show a misleading "convert" checkbox in the
  Format column (already directly taggable, never needed to convert).
- Settings reorganized: "Sources" and "AcoustID" merged into one
  "Cover sources" section, and "Check for updates" / "View processing
  history" / "Reset settings" moved into their own new "App" section
  instead of floating below "Behavior" (renamed "File handling").
- Cover-search summary and "no cover" messaging now adapt to which
  sources are actually enabled, instead of implying a disabled source
  was searched and came up empty.
- AcoustID lookups now retry automatically on a transient network
  error instead of failing immediately.

### Fixed
- Fixed a false-positive cover match: a generic mix label like
  "(Original Mix)" could make SoundCloud match a completely different
  song by the same artist.
- Filenames using an en dash or em dash (–/—) instead of a hyphen as
  the Artist/Title separator are now parsed correctly.
- Dialogs no longer briefly flash as an empty black window before
  their content appears.
- Fixed the Settings tab's footer (credits/legal text) overlapping
  after the window grew past its original size.
- The journal now explains when a file is skipped from cover search
  because no artist could be determined from its filename or tags.

## [0.13]

### Added
- WAV files can now be tagged (and given a cover) directly, without
  being forced through an MP3 conversion.
- WAV files are now converted to AIFF by default before tagging (new
  "Convert WAV to AIFF" setting in Settings, on by default) - purely
  for cover-art compatibility with software that doesn't read embedded
  artwork from WAV at all (confirmed: Rekordbox), not sound quality
  (lossless) or tag support (both work directly either way).
- Ctrl+Z now also undoes a Title/Artist edit, including one already
  confirmed (previously only undid a removed row, and did nothing at
  all while still typing in the edit box).

### Changed
- "Convert everything to MP3" now defaults to off.
- The "Format" column header checkbox no longer deselects tracks when
  unchecked - only the "Apply" header does that (a track that won't be
  touched shouldn't keep a pending conversion queued; the reverse
  doesn't hold).
- The update check now runs before the SoundCloud/Spotify "not
  configured" nag at startup.

### Fixed
- WAV files also get RIFF INFO tags (title/artist) alongside ID3 now,
  so they show up correctly in Windows Explorer and DJ software that
  doesn't read ID3 from WAV.
- Filename parsing fixed for patterns like "Artist - Title ft. X -
  (Suffix)" - a featured-artist credit no longer gets stuck in the
  title, and a trailing bootleg/remix suffix no longer gets misread as
  the artist.
- Cover search no longer settles for a different, unrelated official
  release when a specific named remix/bootleg was asked for - checks
  more candidates per source and requires the named credit to actually
  be present in the match.
- The packaged app no longer crashes at launch with "No module named
  'keyring'".
- The auto-updater retries a connection reset during download
  (including mid-transfer) instead of failing on the first hiccup.
- The "no audio file found" messages now mention formats beyond
  MP3/WAV.
- The progress bar no longer keeps showing a previous run's "Done"
  after editing an already-processed row.
- The legal notice and Extractor intro text now use the full available
  width instead of leaving an unused gap on the right.

## [0.12]

### Added
- Track Tidy is now cross-platform: credential storage, config directory
  resolution, and OS integrations (opening files, revealing them in the
  file manager, playing sounds) all work on macOS as well as Windows.
- Processing history: right-click "Delete" (with a confirmation prompt),
  Ctrl+Z to undo a removed row, alternating row colors, a folder icon
  next to file paths.
- Restoring a moved file from Processing history now searches for it
  automatically; if it truly can't be found, you're asked to locate it
  manually instead of the restore just failing.
- Right-click "Rescan" now opens the Artist/Title correction dialog
  instead of blindly re-searching with the existing tags.
- The number of audio files in a chosen folder is now shown right after
  Browse, instead of only after Scan.

### Changed
- Processing history only lists files that were actually applied, shows
  "Restored" on an entry after it's been restored, and no longer has a
  "Reset" option (right-click Delete replaces it).
- The scan-summary and "fix missing cover" dialogs were merged into one.

### Fixed
- Apply no longer locks tracks that weren't selected, so they can still
  be selected afterward.
- "Open file location" works again.
- The Apply/Format header checkboxes stay in sync with each other and
  with the individual row checkboxes.
- No summary popup appears after a cancelled scan.
- Several dark-mode and spacing fixes in Processing history and the
  Advanced section.

## [0.11]

### Added
- The installer now shows a mandatory license-acceptance page (GPL)
  before installing, instead of the license only being discoverable
  after the fact.
- On first launch, the app notifies the developer via Discord with
  your Windows username (once per Windows account) - disclosed in
  Settings' legal notice text.

### Changed
- "Report track..." sends immediately again, with no confirmation
  popup (reverted from v0.10).

### Fixed
- Fixed a "vv0.10"-style double "v" in the update-download dialog text
  and temp filename.

## [0.10]

### Added
- "Report track..." now shows exactly what's about to be sent (your
  Windows username, file name, current/suggested tags, cover source,
  and that a cover image may be attached) and asks for confirmation
  before sending, instead of sending immediately with no disclosure.
  Reports now include your Windows username, so reports from different
  people aren't all anonymous.
- The auto-updater verifies the downloaded installer's checksum against
  the release before running it, when the release provides one.
- Settings: a note about the project's license, and a "View license &
  third-party notices" link.

### Changed
- Triple-click on Title/Artist in the scan table now plays the file in
  your default audio player (double-click still opens the rename edit,
  as before).
- SoundCloud/Spotify credentials are now encrypted at rest (tied to
  your Windows account) instead of stored as plain text.

### Legal
- Track Tidy is now licensed under the GNU General Public License v2
  or later, since it bundles mutagen (GPL-2.0-or-later) and FFmpeg
  (GPLv3) - see LICENSE and THIRD-PARTY-NOTICES.md. A source archive
  is attached to this release to satisfy the GPL's source-availability
  requirement.

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
