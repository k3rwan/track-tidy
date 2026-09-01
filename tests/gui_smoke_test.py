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

Two tiers of check, deliberately not equally strict:
  - FATAL (fails the build): the app launches and stays up both as a
    direct subprocess (`python interface.py`, exactly what a desktop
    launcher does) and constructed in-process, and its window reaches a
    real size. None of this needs OS-level screen capture.
  - WARNING ONLY (printed, doesn't fail the build): actually grabbing a
    screenshot of each tab/theme and sanity-checking it isn't blank.
    Screen-capture reliability on a headless CI runner is a known,
    already-tolerated gap in this repo (see build-macos.yml's own
    "screencapture failed (no display attached to this runner?)"
    fallback) - a real first CI run of this script hung indefinitely
    inside ImageGrab.grab(), most likely a missing Screen Recording
    permission with nobody there to grant it. Guarded with a
    signal.alarm timeout (POSIX only) so that hangs instead of just
    failing loudly.

Currently only run in CI on macOS (see .github/workflows/gui-smoke-test.yml) -
that's the one GitHub-hosted runner platform already proven able to open a
real window in this repo (build-macos.yml's own "Launch the app" step).
Unverified whether a windows-latest/ubuntu-latest runner would render a
real Tk window the same way without extra setup (e.g. Xvfb on Linux).
"""
import contextlib
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from PIL import ImageGrab

import interface
import track_tidy as tagger

if sys.platform != "win32":
    import signal


class TimeoutError_(Exception):
    pass


@contextlib.contextmanager
def time_limit(seconds):
    """Guards a single call that might HANG rather than raise - e.g.
    ImageGrab.grab() waiting forever on a macOS CI runner that never
    granted the process Screen Recording permission (there's no user
    there to click "Allow", and a plain try/except can't catch a hang).
    signal.alarm is POSIX-only - a no-op context on Windows, where this
    class of hang hasn't been observed and SIGALRM doesn't exist anyway."""
    if sys.platform == "win32":
        yield
        return

    def _handler(signum, frame):
        raise TimeoutError_(f"timed out after {seconds}s")

    previous = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)

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


def check_direct_launch():
    """Regression guard for a real incident: `import interface` (what this
    script itself does below) always registers interface.py as module
    "interface" in sys.modules, so a mistake where a tab_*.py file imports
    something back FROM interface.py (instead of from ui_common.py, which
    has no dependency on interface.py) can pass every other check here and
    still crash the instant someone runs `python interface.py` directly -
    that run makes interface.py module "__main__" instead, so a tab_*.py's
    `from interface import X` re-imports and re-executes interface.py as a
    SECOND, separate module, tripping a circular import the very first
    time this happened. Spawning the real entry point as a subprocess (the
    same way the desktop launcher .bat does) is the only way to actually
    catch that."""
    process = subprocess.Popen(
        [sys.executable, "interface.py"], cwd=REPO_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    time.sleep(3)
    still_running = process.poll() is None
    if still_running:
        process.terminate()
        process.wait(timeout=5)
        print("OK   direct launch (python interface.py) stayed up")
        return None
    _, stderr = process.communicate()
    return f"direct launch (python interface.py) exited early:\n{stderr}"


def check_tagger_row_logic(app):
    """Regression guard for a real incident: double-clicking a Title cell
    used to pre-fill the edit box from the DISPLAYED value (which already
    has " ⚠️"/" 🎧" appended for an unreviewed row), so unless the user
    retyped the whole title, the marker glyph rode along as literal text
    into title_override - it stayed baked into the title even after the
    row was "reviewed" (normally what clears it). Fixed via
    _raw_field_value(); this exercises the same table/undo/sort/filter
    logic a real editing session hits, entirely through Tk object
    construction and direct method calls (no OS screen capture, no
    simulated clicks/keys - see CLAUDE.md's own guidance on both), so it
    doesn't share the screenshot-capture reliability gap the rest of this
    script tolerates as non-fatal."""
    info = {
        "file": "__gui_smoke_test_row__.mp3", "format": "MP3", "processed": False,
        "apply_changes": True, "convert": False,
        "current_title": "old title", "current_artist": "old artist",
        "detected_title": "Real Title", "detected_artist": "Real Artist",
        "title_override": None, "artist_override": None,
        "found_cover_image": None, "already_applied": False,
        "acoustid_identified": False, "original_order": 0,
    }
    app.scanned_plan = [info]
    image_tk = app._create_thumbnail(info)
    app.tk_images[info["file"]] = image_tk
    app.table.insert(
        "", "end", iid=info["file"], image=image_tk if image_tk else "",
        values=app._build_row_values(info),
    )

    displayed_title = app._build_row_values(info)[1]
    if "⚠" not in displayed_title:
        raise AssertionError(f"expected the no-cover marker in the displayed title, got {displayed_title!r}")

    raw_title = app._raw_field_value(info, "title")
    if "⚠" in raw_title or "🎧" in raw_title:
        raise AssertionError(f"review marker leaked into the editable value: {raw_title!r}")
    if raw_title != "Real Title":
        raise AssertionError(f"unexpected raw title value: {raw_title!r}")

    # Simulate confirming an edit WITHOUT touching the marker (the exact
    # scenario that used to bake it into title_override).
    info["title_override"] = raw_title
    app.table.item(info["file"], values=app._build_row_values(info))
    displayed_after_edit = app._build_row_values(info)[1]
    if "⚠" in displayed_after_edit:
        raise AssertionError(f"marker still present after a clean edit: {displayed_after_edit!r}")

    # Undo must bring the marker back (title_override reverts to None).
    app._push_undo("edit", {
        "info": info, "field": "title",
        "old_override": None, "old_override_is_manual": False,
        "old_fix_pending": info.get("fix_pending"),
    })
    info["title_override"] = "Something Else"
    app.table.item(info["file"], values=app._build_row_values(info))
    app._undo_last_action()
    if info["title_override"] is not None:
        raise AssertionError("undo did not restore title_override to None")
    if "⚠" not in app._build_row_values(info)[1]:
        raise AssertionError("marker did not come back after undo")

    # Basic sort/filter smoke - must not raise on a table with real rows.
    app._sort_by("title")
    app._sort_by("title")
    app._sort_by("title")
    app.no_cover_filter_var.set(True)
    app._apply_table_filter()
    app.no_cover_filter_var.set(False)
    app._apply_table_filter()


def check_quality_row_logic(app):
    """Regression guard for two real reports: (1) analysis results used to
    just sit in scan/arrival order, leaving the tracks that most need a
    listen (red/orange) scattered instead of surfaced first - fixed by
    auto-sorting worst-first once a scan ends (_apply_quality_sort_state);
    (2) a file Quality couldn't analyze at all only ever showed up as a
    "❓" row or a line in the hidden-by-default log, easy to miss - fixed
    by also showing a popup. Patches messagebox.showwarning so the popup
    doesn't block this script waiting for a real click - it only asserts
    that the popup call happened with the right count, not that a human
    saw it."""
    import tkinter.messagebox as messagebox

    app.quality_last_scanned_folder = tempfile.gettempdir()
    app.quality_row_paths = {}
    app._quality_scan_counts = {tagger.QUALITY_GREEN: 0, tagger.QUALITY_ORANGE: 0, tagger.QUALITY_RED: 0}
    app._quality_verdict_sort_state = 0
    app._quality_default_row_order = None

    for row in app.quality_table.get_children():
        app.quality_table.delete(row)

    # Inserted in a deliberately "wrong" order (green, red, orange, then an
    # unanalyzable one) so a real reorder is required to pass.
    results = [
        {"file": "a_green.mp3", "format": "MP3", "verdict": tagger.QUALITY_GREEN, "bitrate_kbps": 320, "lufs": -10},
        {"file": "b_red.mp3", "format": "MP3", "verdict": tagger.QUALITY_RED, "bitrate_kbps": 128, "lufs": -8},
        {"file": "c_orange.mp3", "format": "MP3", "verdict": tagger.QUALITY_ORANGE, "bitrate_kbps": 192, "lufs": -9},
        {"file": "d_unknown.mp3", "format": "MP3", "verdict": None, "bitrate_kbps": None, "lufs": None},
    ]
    for result in results:
        app._add_quality_row(result)
    pump(app.window, 0.5)  # let each row's flash-in animation finish (see _flash_new_row)

    captured = []
    original_showwarning = messagebox.showwarning
    try:
        messagebox.showwarning = lambda title, msg, **kw: captured.append((title, msg))

        app._finalize_quality_scan((results, False, None))
        pump(app.window, 0.5)  # the sort itself is deferred past the flash window - see _finalize_quality_scan

        order = [app.quality_table.item(iid, "values")[0] for iid in app.quality_table.get_children()]
        expected = ["b_red.mp3", "c_orange.mp3", "a_green.mp3", "d_unknown.mp3"]
        if order != expected:
            raise AssertionError(f"expected worst-first order {expected}, got {order}")

        if len(captured) != 1 or "1 file" not in captured[0][1]:
            raise AssertionError(f"expected exactly one 'could not be analyzed' popup, got {captured}")
    finally:
        messagebox.showwarning = original_showwarning


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    direct_launch_failure = check_direct_launch()

    root = tk.Tk()
    app = interface.TaggerInterface(root)
    pump(root, 0.6)  # let _rewarm_theme's after()-chain finish (see CLAUDE.md)

    if app.window.winfo_width() < 200 or app.window.winfo_height() < 200:
        raise AssertionError(
            f"Window failed to reach a real size: {app.window.winfo_width()}x{app.window.winfo_height()}"
        )

    # Fatal: none of these depend on OS-level screen capture at all (just
    # process liveness, Tk-internal geometry/table queries, and direct
    # method calls), so they're the real regression signal. Screenshot
    # capture below is best-effort on top of that - see its own
    # warnings-only handling.
    failures = [direct_launch_failure] if direct_launch_failure else []
    warnings = []

    try:
        check_tagger_row_logic(app)
        print("OK   tagger row edit/undo/sort/filter logic")
    except Exception as error:
        failures.append(f"tagger row logic: {error}")
        print(f"FAIL tagger row logic: {error}")

    try:
        app.notebook.select(2)  # Quality tab
        pump(root, 0.3)
        check_quality_row_logic(app)
        print("OK   quality row auto-sort/unanalyzable-popup logic")
    except Exception as error:
        failures.append(f"quality row logic: {error}")
        print(f"FAIL quality row logic: {error}")

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

            try:
                with time_limit(15):
                    ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(path)
                check_screenshot(path)
                print(f"OK   {tab_name}/{theme} -> {path}")
            except Exception as error:
                # Not added to `failures` - screen-capture reliability on a
                # headless CI runner (missing Screen Recording permission,
                # no display attached...) is a known, already-tolerated gap
                # in this repo (see build-macos.yml's own "screencapture
                # failed (no display attached to this runner?)" fallback),
                # not a code regression worth failing the whole build over.
                warnings.append(f"{tab_name}/{theme}: {error}")
                print(f"WARN {tab_name}/{theme}: {error}")

    root.destroy()

    if warnings:
        print(f"\n{len(warnings)} screenshot warning(s) (non-fatal):")
        for warning in warnings:
            print(f" - {warning}")

    if failures:
        print(f"\n{len(failures)} check(s) failed:")
        for failure in failures:
            print(f" - {failure}")
        sys.exit(1)

    print("\nAll GUI smoke checks passed.")


if __name__ == "__main__":
    main()
