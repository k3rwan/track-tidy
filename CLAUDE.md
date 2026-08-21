# Working with Kevin on Track-Tidy

## Autonomy

Kevin has given standing permission to work autonomously on this project's
PR workflow, without asking for confirmation each time:
- Opening PRs
- Merging PRs after opening them
- Pushing branches

**Exception - always ask first:** building/publishing a release (bumping
the version, building the installer, `gh release create` on either
`track-tidy` or `track-tidy-releases`). This is the one step in the
workflow Kevin wants to explicitly approve each time, even though the PR
steps before it don't need approval.

**GPL compliance:** the project is licensed GPL-2.0-or-later (mutagen is
GPL and imported directly into the packaged app - see LICENSE /
THIRD-PARTY-NOTICES.md). v0.10 and v0.11 briefly attached a manually
generated source archive + a `.sha256` checksum file to each
`track-tidy-releases` GitHub release - Kevin asked for those to stop
appearing, and they were removed (`gh release delete-asset`). This is
still GPL-compliant without them: GitHub auto-generates "Source code
(zip)"/"(tar.gz)" links for every tagged release from the underlying git
tag, with no way to disable them (verified - no `gh release`/API option
for it) - that satisfies the source-availability requirement on its own,
so there's no need to manually attach a source archive going forward.
The `.sha256` checksum file (for the auto-updater's checksum
verification, added in PR #103) is also no longer uploaded - briefly
brought back after a 2026-08-21 security review, then Kevin asked for it
to stop again the same day once he saw it show up on a real release. The
verification code in `check_for_update`/`download_installer` is still
there and harmless (gracefully skips verification when no matching
asset exists), just permanently dormant unless that's revisited later.

Still flag before doing, even for the PR workflow:
- Anything hard to reverse or unusual for this workflow: force-push,
  deleting a branch/repo, rewriting git history, changing repo visibility
- Anything outside this project's normal dev loop

## Workflow pattern established in this project

**Batched PR/merge (as of 2026-08-12):** to economize Claude Code usage,
day-to-day changes no longer get their own branch+PR+merge cycle each.
Instead:
- All work happens directly on one long-lived local branch called `wip`
  (created off `main`). Each change is just a commit on `wip`, pushed to
  origin - no PR opened, no merge, no switching back to `main` in between.
- Skip the elaborate per-change verification ceremony (custom Tk smoke-test
  scripts, exhaustive test-plan writeups) for small/cosmetic changes - run
  the existing test suite and reason through correctness instead. Reserve
  real functional verification for changes touching actual logic
  (security, parsing, network, data integrity).
- When Kevin says "release": open ONE PR from `wip` covering everything
  accumulated since the last release, merge it into `main`, delete `wip`,
  create a fresh `wip` off the new `main` for the next batch - THEN run
  the normal release pipeline (version bump, CHANGELOG, build, tag,
  `gh release create`) from `main`, per the existing approval rule below.
  Version to release as: if `main`'s current `APP_VERSION` is already
  ahead of the last published release tag (Kevin sometimes bumps it
  himself right after a release, to mark "this is what's now in
  development"), release AS that version - don't bump again. Otherwise,
  bump it +1 on the last segment (e.g. dev is on 0.12, last release was
  0.12 -> release as 0.13). Auto-determine which case applies, no need
  to ask Kevin which number to use.
  **Immediately after every release finishes** (installer built, GitHub
  release published), bump `main`'s `APP_VERSION` (and `installer.iss`'s
  `MyAppVersion`) +1 again on the last segment, right away, unprompted -
  e.g. just released 0.13 -> dev becomes 0.14 - so `main`/Kevin's desktop
  is always sitting one version ahead of whatever's actually been
  published. This is a standing rule (confirmed twice - once explicitly
  asked for, once as a correction after this step was skipped), not a
  one-off - don't wait to be asked again.
  **Also upload two extra, stably-named copies of each installer**
  (`Track-Tidy-Setup-latest.exe`, `Track-Tidy-Setup-latest.dmg` - added
  2026-08-21) alongside the normal versioned ones, on every release. This
  is what `track-tidy-releases`' README's big "Download for
  Windows"/"Download for macOS" buttons link to
  (`.../releases/latest/download/Track-Tidy-Setup-latest.exe`/`.dmg`) so
  they always resolve to the current version without editing the README
  each release - a non-technical visitor coming from somewhere like
  Reddit gets one obvious button instead of having to pick the right
  asset off the versioned Releases list themselves.
- Test before committing: `python -m unittest discover -s tests` (run from
  the project root with the venv's Python).
- For GUI changes, actually launch the app and screenshot it rather than
  just trusting the code - this caught several real rendering bugs that
  unit tests alone missed (ttk `pack(in_=...)` not reparenting widgets,
  clam-theme focus-ring artifacts, off-screen dialog centering, dark-mode
  row text falling back to an unreadable dim grey). The reliable way to do
  this:
  - Write a throwaway script (scratchpad, never committed) that builds a
    `tk.Tk()` root + `TaggerInterface`, calls `root.update()` once so the
    window is actually mapped, populates rows via `_add_scan_row()` with
    hand-built info dicts (no real network scan needed), and screenshots
    with `PIL.ImageGrab.grab(bbox=...)` computed from
    `winfo_rootx()/rooty()/width()/height()` - all in the SAME Python
    process. This is self-contained and immune to the cross-process
    timing races that a separate PowerShell screenshot step runs into.
  - Launching the app via the Bash tool's background execution does NOT
    render a real window in this environment (it maps at ~0x0 size,
    `winfo_ismapped()` false) - if a cross-process launch is ever needed,
    use PowerShell `Start-Process` instead, which works correctly.
  - Never drive the real app with simulated keyboard input (`SendKeys` or
    similar) - focus isn't reliably on the target window in this
    environment, and keystrokes have leaked into the chat session itself
    more than once. Mouse clicks at absolute screen coordinates are fine
    (they target whatever's physically there); prefer calling the
    relevant method directly over simulating clicks at all when possible.
  - If a same-process test does `entry.focus_set()` +
    `entry.event_generate("<KeyRelease>")` to drive a search/filter entry
    (e.g. History window's search box), the synthetic event silently goes
    nowhere (`dialog.focus_get()` stays on the root window, 0 bindings
    fire) if `TaggerInterface.__init__`'s one-time startup animation is
    still in flight - `_rewarm_theme()` chains 3 `after()` calls
    (100ms/50ms/50ms) that end in `self.window.deiconify()`, and if that
    fires *during* the test's own `root.update()` calls it silently
    steals focus back to root. Pump the event loop for ~300-400ms right
    after constructing `TaggerInterface` (before touching any dialog)
    to let that chain finish, and `focus_set()`/`event_generate()` work
    exactly as expected afterward. Confirmed as a pure test artifact, not
    an app bug - real users take far longer than 200ms to open a dialog
    after launch.
- Never run real scans/processing against the user's actual `Desktop\music`
  library when testing - use isolated temp copies of `fart.wav` instead.
  (A prior session accidentally tagged 9 real library files this way.)
- `gh` CLI is at `/c/Program Files/GitHub CLI/gh.exe` (not on PATH in this
  shell).
- Distribution split: `k3rwan/track-tidy` (private, source) and
  `k3rwan/track-tidy-releases` (public, installer-only, no source code) -
  colleagues download from the second one.

## AcoustID audio-identification fallback

For a file whose filename/tags are too mangled for the normal text search
to even attempt (or that search comes up empty): `identify_via_acoustid()`
fingerprints the actual audio (via the bundled `fpcalc.exe`/`fpcalc`,
Chromaprint) and looks it up against the AcoustID web service, feeding a
confident result's artist/title back into the normal iTunes/SoundCloud
search (`_try_acoustid_correction()`) rather than fetching cover art
through AcoustID/MusicBrainz directly - reuses all the existing matching
logic instead of a second cover-fetching path.
- Only tried as a last resort (see `USE_ACOUSTID_FALLBACK`, on by
  default) - it's much slower (real audio analysis + a network call) than
  the fast text search, so running it for every file would meaningfully
  slow down a scan for no benefit on files that already match fine.
- Works out of the box via a single hardcoded API key
  (`ACOUSTID_API_KEY` in track_tidy.py, Kevin's own) - unlike SoundCloud,
  AcoustID API keys are meant to be per-application, not per-user, so
  nobody registers anything, and there's no per-user override anymore
  (removed - was Settings' "Use my own AcoustID API key..."
  button/dialog). Since it's shared by every user, watch for AcoustID
  lookups starting to fail for multiple users at once - that would mean
  the key's free-tier rate limit is being hit collectively.
- `fpcalc.exe`/`fpcalc` is bundled the same way as `ffmpeg.exe`/`ffmpeg`
  (gitignored, not committed - copy the binary from
  https://github.com/acoustid/chromaprint/releases into the project root
  before building). `find_ffmpeg()`/`find_fpcalc()` share one helper
  (`_find_bundled_tool`) that checks the platform-appropriate bundled
  name first, falling back to PATH.
- **Live lookup confirmed working end to end** with a real API key
  against real audio (Kevin's own real library, via user reports) - the
  integration itself works. One real report DID turn out to be a
  genuine false positive at a very high confidence score (0.97, about
  as high as AcoustID gives) - two stylistically-similar house tracks by
  different artists apparently fingerprinted close enough to collide.
  Not a bug to "fix" (the matching algorithm isn't ours to tune), so
  rows AcoustID identifies now get a "🎧" marker in the main table
  (`_build_row_values` in interface.py) until the user has reviewed the
  Artist/Title themselves - don't trust a high score alone as proof of
  correctness.

## Shared SoundCloud credentials

Works out of the box via embedded default app credentials
(`SOUNDCLOUD_DEFAULT_CLIENT_ID`/`_SECRET` in track_tidy.py,
base64-obfuscated like `ACOUSTID_API_KEY`/`DISCORD_REPORT_WEBHOOK_URL` -
same "not real secrecy" caveat) - Kevin's own app registration, used as
a fallback whenever a user hasn't saved their own credential (the
underlying `read_credential(...) or SOUNDCLOUD_DEFAULT_CLIENT_ID`
priority still exists in track_tidy.py, but Settings no longer exposes
any UI to set a personal override - removed, along with Spotify
entirely, for a simpler Settings tab).

**Accepted risk, explicitly confirmed with Kevin (unlike AcoustID):**
SoundCloud's developer terms discourage distributing an app's Client
Secret to end users - if it's ever extracted from the binary and
abused, they could suspend/revoke the credentials entirely, breaking
this for every user at once, not just whoever abused it. Watch for
SoundCloud auth suddenly failing for multiple users at once - that's
the signal this happened, and it'd need Kevin registering a new app and
swapping the embedded credentials.

`_check_cover_source_credentials_on_startup()` (the old "SoundCloud/
Spotify not configured" startup nag) was removed - credentials are now
always populated (real or the shared default), so it could never fire
again anyway.

## Spotify

Spotify was dropped entirely at first (iTunes + SoundCloud "cover it
well enough"), then re-added after a real report proved that wrong: a
French rap track (Alonzo/Tiakola) was on Spotify but in neither iTunes's
nor SoundCloud's index.

Initially wired in as an absolute last resort (after iTunes AND
SoundCloud both missed), but re-ordered again after a second real
report: for "Soolking - Guerilla", SoundCloud "succeeded" with a non-
official image (a radio freestyle photo) before Spotify ever got a
chance to offer the track's actual official cover. iTunes and Spotify
are both curated commercial catalogs; SoundCloud is community-uploaded
and far more prone to a wrong match (confirmed repeatedly this
session: Aqua/Barbie Girl, Black Eyed Peas, Soolking/Mi Amigo, Soolking/
Guerilla). Current priority order: **iTunes -> Spotify -> SoundCloud**
(`_search_one_source()` / `search_cover_manual()` / `scan_files()`),
including through the AcoustID-corrected retry. `USE_SPOTIFY` (Settings:
"Spotify" checkbox next to iTunes/SoundCloud, on by default) gates it
independently of the other two.

Uses the same shared-embedded-credentials pattern as SoundCloud
(`SPOTIFY_DEFAULT_CLIENT_ID`/`_SECRET`, Kevin's own app registration,
base64-obfuscated) with the same accepted ToS risk, confirmed with
Kevin back when Spotify was first added - re-verified still working
(HTTP 200) when re-adding it. No Settings UI for a personal override,
same as SoundCloud.

## Cross-platform (Windows/macOS)

Kevin is building this for DJs broadly, many of whom are on macOS, so the
codebase was deliberately ported off Windows-only APIs (as of the
"cross-platform" work session):

- **Credentials**: SoundCloud/Spotify client ID/secret now go through the
  `keyring` package (OS-native credential store: Windows Credential
  Manager, macOS Keychain) instead of the old DPAPI-encrypted files.
  `CLIENT_ID_FILE` etc. were renamed to `CLIENT_ID_KEY` etc. accordingly.
  `read_credential()` migrates an old PLAINTEXT legacy file automatically;
  it does NOT recover the DPAPI-encrypted files a prior version wrote
  (that decryption needed `ctypes.windll`, which doesn't exist off
  Windows) - upgrading from that specific version asks for SoundCloud/
  Spotify credentials once more, then it's seamless going forward.
- **Config dir**: `user_config_dir()` now uses `platformdirs` instead of
  a hardcoded `%APPDATA%` read.
- **OS integration**: `interface.py` has three small cross-platform
  helpers - `open_with_default_app()`, `reveal_in_file_manager()`,
  `play_short_sound()` - used everywhere instead of calling
  `os.startfile`/`explorer /select,`/`winsound` directly. `winsound` is
  now imported conditionally (`if sys.platform == "win32"`) since the
  module doesn't exist at all on macOS - a bare top-level import would
  have crashed the app immediately on launch there.
- **Dark title bar** (`_set_titlebar_dark`, DWM API) is Windows-only and
  now explicitly no-ops on other platforms - macOS already darkens its
  own title bar based on the system appearance, no equivalent needed.
- **Theming**: dark mode is built entirely on the `clam` ttk theme, which
  is NOT OS-specific - it should render identically on macOS. Light mode
  uses whichever theme is "native" per-OS (`vista`/`winnative` on
  Windows, `aqua` on macOS) - this was never hardcoded to "vista"
  specifically, so it should resolve correctly on macOS too. **Genuinely
  unverified**: aqua is known for ignoring far more ttk style overrides
  than Windows' native theme does - some light-mode-specific styling
  (`ReadonlyWhite.TEntry`, `LIGHT_TABLE_SELECT_BG`) may not visually
  apply the same way. Needs checking on real hardware.
- **Auto-updater**: `check_for_update()` looks for a `.dmg` release asset
  on macOS, `.exe` on Windows. The downloaded file is opened via
  `open_with_default_app()` rather than launched as a silent installer -
  on macOS this mounts the dmg in Finder (the normal macOS update flow:
  the user drags the app to Applications themselves), it doesn't install
  it automatically the way the Windows `.exe` does.
- **Build**: `build_mac.sh` mirrors `build_all.bat` for macOS (PyInstaller
  `--windowed` -> `.app`, then `hdiutil` -> `.dmg`). **Completely
  unverified** - written without access to real Mac hardware to run it
  on. Needs a real run (and a real `track-tidy_icon.icns`, which doesn't
  exist yet - `track-tidy_icon.png`/`.ico` are the only icon assets in
  the repo) before it can be trusted for an actual release.
- **Not done / explicitly out of scope so far**: macOS code signing and
  notarization (needs a paid Apple Developer account) - an unsigned/
  unnotarized `.app` triggers Gatekeeper's "unidentified developer"
  warning on first launch. A GitHub Actions macOS runner (real Apple
  hardware, no physical Mac needed) was discussed as the practical way
  to both build AND run the automated test suite on real macOS, but no
  workflow file exists yet - ask before assuming that's set up.
