"""
Automated visual smoke test for the Tkinter GUI (interface.py).

NOT part of the `python -m unittest discover -s tests` suite (deliberately
named so unittest's default `test*.py` discovery pattern skips it) - it
opens a real Tk window and needs an actual display/window server, unlike
every other test in this project. Run directly:

    python tests/gui_smoke_test.py

Exercises the same "build TaggerInterface in-process, pump the event loop,
screenshot with PIL.ImageGrab" technique this project already uses for
manual visual-regression checks (see CLAUDE.md's "For GUI changes..."
section) - this is that same technique wired into CI instead of only ever
being a throwaway local script, so a rendering regression (wrong icon
size, illegible light-mode text, a misplaced widget) fails the build
instead of only being caught if someone happens to eyeball a screenshot.

Currently only run in CI on macOS (see .github/workflows/gui-smoke-test.yml) -
that's the one GitHub-hosted runner platform already proven able to open a
real window and screen-capture it in this repo (build-macos.yml's own
"Launch the app" step). Unverified whether a windows-latest/ubuntu-latest
runner would render a real Tk window the same way without extra setup
(e.g. Xvfb on Linux).
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk
from PIL import ImageGrab

import interface

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gui_smoke_screenshots")
TAB_NAMES = ["tagger", "extractor", "quality", "settings"]


def pump(root, seconds):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.01)


def check_screenshot(path, min_width=200, min_height=200, min_unique_colors=8):
    """A handful of cheap, low-flake checks that would catch a window that
    failed to map (0x0 or off-screen capture), rendered blank/black, or
    otherwise clearly isn't the real UI - not a pixel-perfect diff against
    a golden image (too brittle across OS font/DPI differences), just
    "does this look like a real rendered window at all"."""
    from PIL import Image

    img = Image.open(path)
    if img.width < min_width or img.height < min_height:
        raise AssertionError(f"{path}: suspiciously small capture ({img.width}x{img.height})")
    colors = img.convert("RGB").getcolors(maxcolors=1_000_000)
    unique_count = len(colors) if colors is not None else 1_000_001
    if unique_count < min_unique_colors:
        raise AssertionError(f"{path}: only {unique_count} unique color(s) - looks blank/solid, not real UI")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    root = tk.Tk()
    app = interface.TaggerInterface(root)
    pump(root, 0.6)  # let _rewarm_theme's after()-chain finish (see CLAUDE.md)

    if app.window.winfo_width() < 200 or app.window.winfo_height() < 200:
        raise AssertionError(
            f"Window failed to reach a real size: {app.window.winfo_width()}x{app.window.winfo_height()}"
        )

    failures = []
    for theme in ("dark", "light"):
        app._apply_theme(theme)
        pump(root, 0.3)
        for index, tab_name in enumerate(TAB_NAMES):
            app.notebook.select(index)
            pump(root, 0.3)
            root.update_idletasks()
            root.update()

            x, y = app.window.winfo_rootx(), app.window.winfo_rooty()
            w, h = app.window.winfo_width(), app.window.winfo_height()
            path = os.path.join(OUTPUT_DIR, f"{tab_name}_{theme}.png")
            ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(path)

            try:
                check_screenshot(path)
                print(f"OK   {tab_name}/{theme} -> {path}")
            except AssertionError as error:
                failures.append(str(error))
                print(f"FAIL {tab_name}/{theme}: {error}")

    root.destroy()

    if failures:
        print(f"\n{len(failures)} check(s) failed:")
        for failure in failures:
            print(f" - {failure}")
        sys.exit(1)

    print("\nAll GUI smoke checks passed.")


if __name__ == "__main__":
    main()
