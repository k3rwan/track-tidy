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
verification, added in PR #103) is also no longer uploaded - the
verification code in `check_for_update`/`download_installer` is still
there and harmless (gracefully skips verification when no matching
asset exists), just permanently dormant unless that's revisited later.

Still flag before doing, even for the PR workflow:
- Anything hard to reverse or unusual for this workflow: force-push,
  deleting a branch/repo, rewriting git history, changing repo visibility
- Anything outside this project's normal dev loop

## Workflow pattern established in this project

- One small, focused PR per change (branch -> commit -> push -> PR -> merge).
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
- Never run real scans/processing against the user's actual `Desktop\music`
  library when testing - use isolated temp copies of `fart.wav` instead.
  (A prior session accidentally tagged 9 real library files this way.)
- `gh` CLI is at `/c/Program Files/GitHub CLI/gh.exe` (not on PATH in this
  shell).
- Distribution split: `k3rwan/track-tidy` (private, source) and
  `k3rwan/track-tidy-releases` (public, installer-only, no source code) -
  colleagues download from the second one.
