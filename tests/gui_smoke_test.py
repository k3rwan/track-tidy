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

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

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
        [sys.executable, os.path.join("src", "interface.py")], cwd=REPO_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    time.sleep(3)
    still_running = process.poll() is None
    if still_running:
        process.terminate()
        process.wait(timeout=5)
        print("OK   direct launch (python src/interface.py) stayed up")
        return None
    _, stderr = process.communicate()
    return f"direct launch (python src/interface.py) exited early:\n{stderr}"


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


def check_bpm_key_display(app):
    """Regression guard for the BPM/Key feature's display: it's rendered
    as a SECOND LINE inside the Title cell (no new table column, no
    window-width change - see TAGGER_TABLE_ROW_HEIGHT), not a suffix on
    the same line like the ⚠️/🎧 markers. Exercises _build_row_values()
    directly rather than a real scan (no ffmpeg/analysis needed here -
    that's covered separately in test_track_tidy.py's
    EstimateBpmAndKeyTests)."""
    info = {
        "file": "__gui_smoke_test_bpm_row__.mp3", "format": "MP3", "processed": False,
        "apply_changes": True, "convert": False,
        "current_title": "old title", "current_artist": "old artist",
        "detected_title": "Real Title", "detected_artist": "Real Artist",
        "title_override": None, "artist_override": None,
        "found_cover_image": b"not empty", "already_applied": False,
        "acoustid_identified": False, "original_order": 0,
        "bpm": 128.0, "camelot_key": "8A", "duplicate_of": None,
    }
    displayed_title = app._build_row_values(info)[1]
    if "\n128 BPM - 8A" not in displayed_title:
        raise AssertionError(f"expected a '128 BPM - 8A' second line in the title, got {displayed_title!r}")

    # No bpm/key detected (or feature off) -> no second line at all, so a
    # plain row looks exactly like it did before this feature existed.
    info["bpm"] = None
    info["camelot_key"] = None
    displayed_title = app._build_row_values(info)[1]
    if "\n" in displayed_title:
        raise AssertionError(f"expected no second line when bpm/key are None, got {displayed_title!r}")


def check_duplicate_marker_and_row_tag(app):
    """Regression guard for the duplicate-detection UI: a row with
    duplicate_of set gets the "\U0001f501" marker on the title (NOT
    cleared by a title edit, unlike ⚠️/\U0001f3a7 - see
    _duplicate_marker's own docstring) and the "dup_row" background tag,
    applied the same way _finalize_find_duplicates does (not a real
    fingerprinting pass - that's covered separately in
    test_track_tidy.py's DuplicateDetectionTests)."""
    info = {
        "file": "__gui_smoke_test_dup_row__.mp3", "format": "MP3", "processed": False,
        "apply_changes": True, "convert": False,
        "current_title": "old title", "current_artist": "old artist",
        "detected_title": "Real Title", "detected_artist": "Real Artist",
        "title_override": None, "artist_override": None,
        "found_cover_image": b"not empty", "already_applied": False,
        "acoustid_identified": False, "original_order": 0,
        "bpm": None, "camelot_key": None, "duplicate_of": None,
    }
    app.scanned_plan.append(info)
    image_tk = app._create_thumbnail(info)
    app.tk_images[info["file"]] = image_tk
    app.table.insert(
        "", "end", iid=info["file"], image=image_tk if image_tk else "",
        values=app._build_row_values(info), tags=("even_row",),
    )

    displayed_title = app._build_row_values(info)[1]
    if "\U0001f501" in displayed_title:
        raise AssertionError("duplicate marker present before duplicate_of was even set")

    # Mirrors _finalize_find_duplicates's own row-marking logic.
    info["duplicate_of"] = "some_other_track.mp3"
    app._refresh_row(info)
    current_tags = app.table.item(info["file"], "tags")
    if "dup_row" not in current_tags:
        app.table.item(info["file"], tags=tuple(current_tags) + ("dup_row",))

    displayed_title = app._build_row_values(info)[1]
    if "\U0001f501" not in displayed_title:
        raise AssertionError(f"expected the duplicate marker once duplicate_of is set, got {displayed_title!r}")

    final_tags = app.table.item(info["file"], "tags")
    if "dup_row" not in final_tags:
        raise AssertionError(f"expected 'dup_row' among the row's tags, got {final_tags}")

    # Unlike ⚠️/\U0001f3a7, a title edit must NOT clear the duplicate marker -
    # it's a fact about the audio, not something a title override "resolves".
    info["title_override"] = "Real Title"
    displayed_title_after_edit = app._build_row_values(info)[1]
    if "\U0001f501" not in displayed_title_after_edit:
        raise AssertionError("duplicate marker incorrectly cleared by a title edit")


def check_always_on_top_toggle(app):
    """Pin button (_toggle_always_on_top) flips the window's real
    -topmost attribute, its pin_button color, and always_on_top_var in
    lockstep. always_on_top_var is deliberately never restored from
    settings.json (Kevin's call - a forgotten pin from a previous session
    should never carry over), so it must always start False here,
    regardless of whatever this machine's real, un-isolated settings.json
    (see this file's own known limitation) happens to hold."""
    initial = bool(app.window.attributes("-topmost"))
    if initial:
        raise AssertionError("always_on_top should always start False - it must never be restored from settings.json")

    app._toggle_always_on_top()
    flipped = bool(app.window.attributes("-topmost"))
    if flipped == initial:
        raise AssertionError("toggling the pin button should flip the window's -topmost attribute")
    if bool(app.always_on_top_var.get()) != flipped:
        raise AssertionError("always_on_top_var should match the window's actual -topmost attribute")

    app._toggle_always_on_top()
    restored = bool(app.window.attributes("-topmost"))
    if restored != initial:
        raise AssertionError("toggling back should restore the original -topmost state")


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


def check_quality_progress_bar_placement(app):
    """Regression guard for two real bugs found while moving Quality's
    progress bar to the bottom of the tab (to match Tagger's own
    placement):

    1. _start_quality_scan packed the canvas but never called
       _adjust_window_height() afterwards, unlike Tagger's
       _show_scan_progress_bar (tab_tagger.py) and unlike Quality's own
       _reset_quality, which already called it on the way back down.
       quality_table_frame's Treeview has a real minimum height (unlike
       a plain Frame), so pack couldn't shrink it to free up space on
       its own - the window needed to actually grow, which never
       happened, so the bar had nowhere to render at all (not just in
       the wrong place - genuinely invisible).

    2. Fixing #1 alone still wasn't enough in real use: once a real scan
       produces its first verdict, _update_quality_summary_strip() packs
       ANOTHER new widget (the green/orange/red counts, between the
       buttons and the table) with the exact same missing-adjustment
       bug - real report: the bar disappeared again in actual use once
       that strip showed up, competing for space in a window that was
       only grown to fit the bar alone. Triggered directly here (rather
       than waiting on a real background scan, which an earlier version
       of this test did - real ffmpeg analysis + threading made it slow
       and, on a loaded CI runner, occasionally hang outright).
    """
    tmp_dir = tempfile.mkdtemp()

    app._reset_quality()
    pump(app.window, 0.1)
    if app.quality_progress_canvas.winfo_ismapped():
        raise AssertionError("progress bar should start out hidden")
    height_before = app.window.winfo_height()

    # Stub out the actual background analysis (real ffmpeg + threading -
    # unnecessary here and, on a slower/loaded CI runner, a source of
    # flaky timing) so _start_quality_scan's own synchronous pack/resize
    # logic - the thing that actually had the bug - can be checked
    # directly and deterministically, the same way check_quality_row_logic
    # above drives _add_quality_row/_finalize_quality_scan directly rather
    # than waiting on a real scan.
    original_run_scan = app._run_quality_scan
    app._run_quality_scan = lambda *a, **kw: None
    try:
        app.quality_folder_var.set(tmp_dir)
        app._start_quality_scan()
        pump(app.window, 0.2)

        if not app.quality_progress_canvas.winfo_ismapped():
            raise AssertionError("progress bar never became visible after starting a scan")
        if app.quality_progress_canvas.winfo_width() <= 1:
            raise AssertionError(f"progress bar has no real width ({app.quality_progress_canvas.winfo_width()}px) - never laid out")
        # Was strictly "<=" (must grow) before Tagger's own table got
        # taller rows for the BPM/Key second line - the window (one
        # shared height across every tab) can now already be tall enough
        # from Tagger's own content alone to fit Quality's progress bar
        # with no further growth needed. What actually matters is that it
        # never SHRINKS to make room (still checked below via a real
        # mapped/positioned bar) - confirmed empirically that this can
        # legitimately be an equality now, not just a fluke.
        if app.window.winfo_height() < height_before:
            raise AssertionError("window should not shrink when the progress bar appears")

        canvas_top = app.quality_progress_canvas.winfo_rooty()
        table_bottom = app.quality_table_frame.winfo_rooty() + app.quality_table_frame.winfo_height()
        if canvas_top < table_bottom:
            raise AssertionError("progress bar should sit BELOW the table (Tagger's placement), not above/inside it")

        # Directly trigger the summary strip's own pack() (bug #2 above) -
        # this is what a real scan's first verdict does, without needing
        # to wait for one.
        app._quality_scan_counts = {tagger.QUALITY_GREEN: 1, tagger.QUALITY_ORANGE: 0, tagger.QUALITY_RED: 0}
        app._update_quality_summary_strip()
        pump(app.window, 0.1)
        if not app.quality_summary_frame.winfo_ismapped():
            raise AssertionError("summary strip never appeared")
        if not app.quality_progress_canvas.winfo_ismapped() or app.quality_progress_canvas.winfo_width() <= 1:
            raise AssertionError("progress bar was pushed out once the summary strip appeared alongside it")
    finally:
        app._run_quality_scan = original_run_scan
        # _start_quality_scan disabled these for the run and _reset_quality
        # doesn't re-enable them itself (normally only called once a scan
        # has already finished and re-enabled them via
        # _finalize_quality_scan - bypassed here since the scan itself was
        # stubbed to a no-op) - leaving them disabled would leak into
        # whichever check runs next (e.g. check_quality_drag_and_drop's
        # own quality_browse_button state check).
        app.quality_browse_button.configure(state="normal")
        app.quality_reset_button.configure(state="normal")
        app._set_tabs_locked(False)  # _start_quality_scan locks tab-switching too
        app._reset_quality()
        pump(app.window, 0.1)

    if app.quality_progress_canvas.winfo_ismapped():
        raise AssertionError("progress bar should be hidden again after _reset_quality")
    if app.window.winfo_height() != height_before:
        raise AssertionError("window should shrink back to its original height after _reset_quality")


def check_quality_incremental_add(app):
    """Regression guard for a real report: dropping one more track into an
    already-analyzed Quality folder wiped every existing row first,
    instead of just adding to them - _start_quality_scan unconditionally
    cleared the table/counts/sort state on every call, whether it was a
    fresh folder scan or just one more file dropped onto the same
    folder. Fixed by only resetting when explicit_files is absent (a
    real fresh scan) or the folder actually changed."""
    tmp_dir = tempfile.mkdtemp()

    app._reset_quality()
    pump(app.window, 0.1)
    app.quality_folder_var.set(tmp_dir)
    app.quality_last_scanned_folder = tmp_dir
    app._add_quality_row({
        "file": "existing.mp3", "format": "MP3", "verdict": tagger.QUALITY_GREEN,
        "bitrate_kbps": 320, "lufs": -10,
    })
    pump(app.window, 0.3)

    original_run_scan = app._run_quality_scan
    app._run_quality_scan = lambda *a, **kw: None  # avoid a real background analysis
    try:
        app._start_quality_scan(explicit_files=[os.path.join(tmp_dir, "new.mp3")])
        pump(app.window, 0.1)
        rows = app.quality_table.get_children()
        if len(rows) != 1:
            raise AssertionError(f"expected the existing row to survive an incremental add, got {len(rows)} row(s)")
        if app.quality_table.item(rows[0], "values")[0] != "existing.mp3":
            raise AssertionError("the surviving row isn't the pre-existing one - table was rebuilt, not appended to")

        # A scan with NO explicit_files (a real fresh "Analyze" click) must
        # still reset - only the incremental-drop path should preserve rows.
        app._finalize_quality_scan(([], False, None))
        pump(app.window, 0.1)
        app._start_quality_scan()
        pump(app.window, 0.1)
        if app.quality_table.get_children():
            raise AssertionError("a fresh whole-folder scan should still clear previous rows")
    finally:
        app._run_quality_scan = original_run_scan
        app.quality_browse_button.configure(state="normal")
        app.quality_reset_button.configure(state="normal")
        app._set_tabs_locked(False)
        app._reset_quality()
        pump(app.window, 0.1)


def check_quality_drag_and_drop(app):
    """Regression guard for the Quality tab's drag-and-drop, added to
    match the Tagger tab's own (a folder or file dropped while viewing
    Quality now analyzes it there, instead of only Tagger's window-level
    registration ever reacting to a drop) - including a real report that
    an earlier version of this always analyzed the WHOLE parent folder
    even when a single file was dropped, unlike Tagger's own drop, which
    tags just the file(s) actually dropped. Calls the drop handler
    directly with a synthetic event rather than a simulated OS-level
    drop (unreliable in CI/sandboxes - see CLAUDE.md), so this checks the
    handler's own logic: folder resolution, explicit-file-list scanning,
    syncing the other tabs' folder fields, and the "ignore while a scan
    is running" guard."""
    tmp_dir = tempfile.mkdtemp()

    class FakeEvent:
        pass

    scan_calls = []  # each entry: (folder, explicit_files)
    original_start = app._start_quality_scan
    try:
        app._start_quality_scan = lambda explicit_files=None: scan_calls.append(
            (app.quality_folder_var.get(), explicit_files)
        )

        event = FakeEvent()
        event.data = "{" + tmp_dir + "}"
        app._on_quality_files_dropped(event)
        if scan_calls != [(tmp_dir, None)]:
            raise AssertionError(f"expected the dropped folder to start a whole-folder scan, got {scan_calls}")
        if app.extract_folder_var.get() != tmp_dir or app.folder_variable.get() != tmp_dir:
            raise AssertionError("dropping onto Quality should sync Tagger/Extractor's folder fields too")

        # Dropping a single FILE must analyze just that file, not the
        # whole folder it lives in.
        fake_file = os.path.join(tmp_dir, "track.mp3")
        with open(fake_file, "wb") as f:
            f.write(b"x")
        scan_calls.clear()
        event2 = FakeEvent()
        event2.data = "{" + fake_file + "}"
        app._on_quality_files_dropped(event2)
        if scan_calls != [(tmp_dir, [fake_file])]:
            raise AssertionError(f"expected a single-file scan of just that file, got {scan_calls}")

        # A drop while a scan is already running must be ignored.
        app.quality_browse_button.configure(state="disabled")
        try:
            scan_calls.clear()
            app.quality_folder_var.set("SHOULD_NOT_CHANGE")
            app._on_quality_files_dropped(event)
            if scan_calls or app.quality_folder_var.get() != "SHOULD_NOT_CHANGE":
                raise AssertionError("drop should be ignored while a quality scan is running")
        finally:
            app.quality_browse_button.configure(state="normal")
    finally:
        app._start_quality_scan = original_start


def check_extractor_drag_and_drop(app):
    """Regression guard: the Extractor tab had NO drag-and-drop of its
    own (unlike Tagger and, since the previous fix, Quality), so a drop
    while viewing it fell through to Tagger's window/notebook-level
    registration and silently started a Tagger scan instead - the exact
    same bug already found and fixed for Quality, just not yet noticed
    here. Also checks the deliberate difference from Tagger/Quality: a
    drop only fills the folder field, it does NOT auto-start the
    extraction (which moves files on disk with no review step first,
    unlike a scan/analysis)."""
    tmp_dir = tempfile.mkdtemp()

    class FakeEvent:
        pass

    extract_calls = []
    original_start = app._start_extraction
    try:
        app._start_extraction = lambda: extract_calls.append(True)

        event = FakeEvent()
        event.data = "{" + tmp_dir + "}"
        app._on_extractor_files_dropped(event)
        if app.extract_folder_var.get() != tmp_dir:
            raise AssertionError(f"expected the dropped folder to fill the field, got {app.extract_folder_var.get()!r}")
        if app.quality_folder_var.get() != tmp_dir or app.folder_variable.get() != tmp_dir:
            raise AssertionError("dropping onto Extractor should sync Tagger/Quality's folder fields too")
        if extract_calls:
            raise AssertionError("a drop must not auto-start the extraction (it moves files on disk)")

        # Dropping a FILE resolves to its containing folder (extraction is
        # inherently folder-scoped - there's no per-file equivalent).
        fake_file = os.path.join(tmp_dir, "track.mp3")
        with open(fake_file, "wb") as f:
            f.write(b"x")
        app.extract_folder_var.set("")
        event2 = FakeEvent()
        event2.data = "{" + fake_file + "}"
        app._on_extractor_files_dropped(event2)
        if app.extract_folder_var.get() != tmp_dir:
            raise AssertionError(f"expected a dropped file to resolve to its folder, got {app.extract_folder_var.get()!r}")

        # A drop while an extraction is already running must be ignored.
        app.extract_browse_button.configure(state="disabled")
        try:
            app.extract_folder_var.set("SHOULD_NOT_CHANGE")
            app._on_extractor_files_dropped(event)
            if app.extract_folder_var.get() != "SHOULD_NOT_CHANGE":
                raise AssertionError("drop should be ignored while an extraction is running")
        finally:
            app.extract_browse_button.configure(state="normal")
    finally:
        app._start_extraction = original_start


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
        check_bpm_key_display(app)
        print("OK   tagger BPM/Key second-line display")
    except Exception as error:
        failures.append(f"BPM/Key display: {error}")
        print(f"FAIL BPM/Key display: {error}")

    try:
        check_duplicate_marker_and_row_tag(app)
        print("OK   tagger duplicate marker/row tag")
    except Exception as error:
        failures.append(f"duplicate marker/row tag: {error}")
        print(f"FAIL duplicate marker/row tag: {error}")

    try:
        check_always_on_top_toggle(app)
        print("OK   always-on-top pin button")
    except Exception as error:
        failures.append(f"always-on-top pin button: {error}")
        print(f"FAIL always-on-top pin button: {error}")

    try:
        app.notebook.select(2)  # Quality tab
        pump(root, 0.3)
        check_quality_row_logic(app)
        print("OK   quality row auto-sort/unanalyzable-popup logic")
    except Exception as error:
        failures.append(f"quality row logic: {error}")
        print(f"FAIL quality row logic: {error}")

    try:
        check_quality_progress_bar_placement(app)
        print("OK   quality progress bar placement/visibility")
    except Exception as error:
        failures.append(f"quality progress bar placement: {error}")
        print(f"FAIL quality progress bar placement: {error}")

    try:
        check_quality_incremental_add(app)
        print("OK   quality tab incremental add (drop doesn't reset existing rows)")
    except Exception as error:
        failures.append(f"quality incremental add: {error}")
        print(f"FAIL quality incremental add: {error}")

    try:
        check_quality_drag_and_drop(app)
        print("OK   quality tab drag-and-drop")
    except Exception as error:
        failures.append(f"quality drag-and-drop: {error}")
        print(f"FAIL quality drag-and-drop: {error}")

    try:
        check_extractor_drag_and_drop(app)
        print("OK   extractor tab drag-and-drop")
    except Exception as error:
        failures.append(f"extractor drag-and-drop: {error}")
        print(f"FAIL extractor drag-and-drop: {error}")

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
