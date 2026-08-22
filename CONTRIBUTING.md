# Contributing to Track Tidy

Thanks for taking the time to contribute! This is a small, mostly
solo-maintained project, so please keep that in mind - response times can
vary.

## Reporting a bug

- If a specific track got mismatched or tagged wrong, the in-app
  **"Report track..."** button (right-click a row) is faster than an
  Issue - it sends the exact info needed to fix the matching logic.
- For anything else (crash, UI bug, install/update problem), open an
  [Issue](../../issues) using the bug report template. Include your OS,
  the app version (shown in the bottom-right corner), and steps to
  reproduce.
- Found a security vulnerability? Please see [SECURITY.md](SECURITY.md)
  instead of opening a public Issue.

## Suggesting a feature

Open an [Issue](../../issues) using the feature request template.
Explain the problem you're trying to solve, not just the solution you
have in mind - it makes it easier to evaluate.

## Pull requests

Small, focused fixes are welcome (typos, small bugs, obvious
improvements). For anything larger, please open an Issue first to
discuss the approach before investing time in a PR - it may already be
in progress, or may not fit the project's direction.

Before submitting:
- Run the test suite: `python -m unittest discover -s tests`
- Keep the change focused - avoid unrelated refactors in the same PR
- Match the existing code style (see comments throughout `track_tidy.py`
  for the reasoning behind non-obvious decisions)

## Setup

See the [README](README.md#running-from-source) for how to run the app
from source.
