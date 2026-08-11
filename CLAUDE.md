# Working with Kevin on Track-Tidy

## Autonomy

Kevin has given standing permission to work autonomously on this project's
routine workflow, without asking for confirmation each time:
- Merging PRs after opening them
- Pushing branches
- Building the app/installer and publishing GitHub releases (both the
  private `track-tidy` source repo and the public `track-tidy-releases`
  distribution repo)

Still flag before doing, even here:
- Anything hard to reverse or unusual for this workflow: force-push,
  deleting a branch/repo, rewriting git history, changing repo visibility
- Anything outside this project's normal dev loop

## Workflow pattern established in this project

- One small, focused PR per change (branch -> commit -> push -> PR -> merge).
- Test before committing: `python -m unittest discover -s tests` (run from
  the project root with the venv's Python).
- For GUI changes, actually launch the app and screenshot it (via a
  background PowerShell capture, see prior session transcripts) rather than
  just trusting the code - this caught several real rendering bugs that
  unit tests alone missed (ttk `pack(in_=...)` not reparenting widgets,
  clam-theme focus-ring artifacts, off-screen dialog centering).
- Never run real scans/processing against the user's actual `Desktop\music`
  library when testing - use isolated temp copies of `fart.wav` instead.
  (A prior session accidentally tagged 9 real library files this way.)
- `gh` CLI is at `/c/Program Files/GitHub CLI/gh.exe` (not on PATH in this
  shell).
- Distribution split: `k3rwan/track-tidy` (private, source) and
  `k3rwan/track-tidy-releases` (public, installer-only, no source code) -
  colleagues download from the second one.
