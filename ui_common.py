"""Shared, never-mutated constants and small OS-integration helpers used by
both interface.py and the tab_*.py mixins. Deliberately has NO import of
interface.py (or any tab_*.py) - the tab_*.py files import from here
instead of from interface.py specifically to avoid a circular import that
only bites when interface.py is run directly (python interface.py), where
it becomes module "__main__" rather than "interface" - see git history /
CLAUDE.md for the full story if this file's purpose is ever unclear."""
import os
import subprocess
import sys
import tkinter as tk

if sys.platform == "win32":
    import winsound


def open_with_default_app(path):
    """Opens a file with whatever app the OS has associated with it (used
    for playing an audio file, opening the bundled license notices...)."""
    if sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.run(["open", path])


def reveal_in_file_manager(path):
    """Opens the OS's file manager with this file pre-selected."""
    if sys.platform == "win32":
        # Deliberately NOT list-form: subprocess.list2cmdline() would quote
        # the whole "/select,<path>" argument as one unit
        # ("/select,C:\my folder\file.mp3"), but explorer's own /select,
        # parser specifically expects the comma OUTSIDE the quotes and
        # only the path itself quoted (/select,"C:\my folder\file.mp3") -
        # list form silently breaks this. Safe as a raw string on Windows
        # without shell=True (the whole string is passed straight to
        # CreateProcess as-is), and full_path can't contain '"' (not a
        # legal character in a Windows path).
        subprocess.run(f'explorer /select,"{path}"')
    else:
        subprocess.run(["open", "-R", path])


def play_short_sound(path):
    """Plays a short local sound file (the Apply-complete chime)."""
    if sys.platform == "win32":
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
    else:
        subprocess.run(["afplay", path])


def resource_path(filename):
    """
    Locates a bundled resource (icon, sound...) whether running as a normal
    script or as a PyInstaller-frozen .exe (which extracts data files to a
    temporary folder, sys._MEIPASS, different from the .exe's own location).
    """
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, filename)

# Windows renders ☑/☐ inconsistently when the Treeview has no explicit
# font: a thin outline in the native column header vs. a solid black
# emoji-style box in table cells (same character, different font fallback
# per rendering path). Fixed at the root by pinning Table.Treeview's font
# explicitly (see _build_interface / _apply_theme) rather than avoiding
# the glyph - both now render identically.
CHECKED_BOX = "☑"
EMPTY_BOX = "☐"
PROCESSED_CHECK = "✔"
# Shown in the "apply" column instead of PROCESSED_CHECK for a row the
# scan found already fully tagged (see track_tidy.py's "already_applied") -
# distinguishes "nothing to do, already done before this scan" from "this
# scan just processed it".
ALREADY_APPLIED_MARK = "-"

# How often a buffered scan result is revealed into the table - see
# _reveal_next_scan_row(). Purely cosmetic pacing so tracks don't all pop
# in at once; the actual scan underneath is unaffected.
SCAN_REVEAL_INTERVAL_MS = 1000

# Track Tidy is still in beta - large libraries haven't had enough
# real-world mileage yet, so a single scan is capped at this many tracks
# for now (see _apply_track_count_limit).
MAX_TRACKS_PER_SCAN = 100

# Above this fraction of no-cover-match tracks in a scanned folder,
# automatically send the whole batch to Discord - see _finalize_scan /
# _notify_no_cover_report.
NO_COVER_REPORT_THRESHOLD = 0.15

# "Automatic" appearance (see _resolve_theme_choice): light between these
# two local hours, dark the rest of the time (evening/night). Plain hour-
# of-day, not sunrise/sunset - simple and predictable rather than needing
# the user's location.
AUTO_THEME_LIGHT_START_HOUR = 7
AUTO_THEME_DARK_START_HOUR = 20

# How often a running app re-checks the current hour while "Automatic" is
# selected, so the theme actually flips if the app is left open across one
# of the two boundaries above instead of only updating on next launch.
AUTO_THEME_RECHECK_INTERVAL_MS = 30 * 60 * 1000

THUMBNAIL_SIZE = (44, 44)
TABLE_ROW_HEIGHT = 48
# Data columns. The cover is shown via the native "#0" column (dedicated, on the left),
# the "apply" checkbox is a separate column right after it.
# "format" combines the format AND the conversion (e.g. "MP3", "WAV ☑")
COLUMNS = ("apply", "title", "artist", "format")

# Synthetic row id for the "N track(s) with cover hidden" summary shown at
# the bottom of the table when the no-cover filter hides some rows - never
# backed by a real scanned_plan entry.
NO_COVER_SUMMARY_ROW_ID = "__no_cover_summary_row__"

# Synthetic row id for the "N track(s) found" summary shown at the bottom
# of the table while the search box has an active query - never backed by
# a real scanned_plan entry.
SEARCH_RESULT_SUMMARY_ROW_ID = "__search_result_summary_row__"

# Soft selection highlight for the main table in light mode, in place of the
# native theme's stock (harsher) Windows blue - the rest of light mode still
# intentionally leaves the native theme's own colors alone (see below).
LIGHT_TABLE_SELECT_BG = "#cfe3f5"
LIGHT_TABLE_SELECT_FG = "#1a1a1a"

# Muted/secondary text (footer credits, version label) - a plain mid-grey
# reads fine on light mode's near-white background, but is borderline-low
# contrast against dark mode's near-black one, so it's brightened for dark
# specifically (see MUTED_TEXT_COLOR's uses in _apply_theme).
MUTED_TEXT_COLOR = "#888888"
DARK_MUTED_TEXT_COLOR = "#a0a0a0"

# Dark palette. There's no equivalent LIGHT_COLORS dict - "light" instead
# means "leave the native theme's own colors alone", captured at startup
# (see App._native_bg etc.) so it matches today's look exactly.
DARK_COLORS = {
    "bg": "#18191C",  # background
    "fg": "#e0e0e0",
    "entry_bg": "#202225",  # panels (LabelFrame boxes, buttons, inputs, inactive tabs)
    "entry_fg": "#f0f0f0",
    "select_bg": "#3f6fb0",
    "select_fg": "#ffffff",
    "tree_bg": "#24262A",  # list area (table body, listboxes, log)
    "tree_fg": "#e0e0e0",
    "tree_odd_row": "#2A2C31",
    "tree_heading_bg": "#2B2E33",  # list header
    "listbox_bg": "#24262A",
    "listbox_fg": "#e0e0e0",
    "journal_bg": "#24262A",
    "journal_fg": "#d4d4d4",
    "progress_track": "#24262A",
    "progress_fill": "#4a90d9",
    "progress_text": "#f0f0f0",
    "menu_bg": "#2B2E33",
    "menu_fg": "#e0e0e0",
    "border": "#3A3D42",
}

# Checkbutton/Radiobutton indicator colors. Light and dark draw the exact
# same clam-sourced indicator shape (see the "Uniform.*.indicator" block in
# _apply_theme) - the native theme's own indicator is pure OS drawing with
# no configurable colors at all, so without this both modes would show two
# completely different checkbox/radio shapes for the same control. Only the
# colors differ here; "checked" intentionally reuses dark mode's own accent
# blue in both themes.
INDICATOR_CHECKED_BG = DARK_COLORS["select_bg"]
LIGHT_INDICATOR_COLORS = {
    "indicator_bg": "#ffffff",
    "indicator_border": "#8a8a8a",
}

# Scrollbar colors for light mode. The native theme's own scrollbar (like
# its checkbox indicator above) has zero stylable properties either, so it
# can't literally be copied pixel-for-pixel - both modes instead render the
# same clam-sourced scrollbar shape (see _apply_theme), recolored to
# approximate each theme's look. Chosen to sit close to stock Windows'
# light-grey thumb/near-white trough.
LIGHT_SCROLLBAR_COLORS = {
    "thumb": "#c1c1c1",
    "trough": "#f0f0f0",
    "arrow": "#606060",
    "active_thumb": "#8c8c8c",
}


def setup_placeholder(entry, placeholder, on_change=None):
    """
    Shows greyed-out placeholder text in an Entry when it's empty and unfocused,
    like a native HTML placeholder. on_change (if given) is called whenever the
    placeholder is shown/hidden, so callers relying on the entry's content (e.g.
    a search filter) can react. entry.placeholder_active tracks the state.
    entry.normal_color is kept mutable (not a closure constant) so a theme
    change can update what "not a placeholder" text color means.
    """
    entry.normal_color = "black"
    placeholder_color = "#999999"

    def show_placeholder():
        entry.insert(0, placeholder)
        entry.configure(foreground=placeholder_color)
        entry.placeholder_active = True

    def clear_placeholder():
        if getattr(entry, "placeholder_active", False):
            entry.delete(0, "end")
            entry.configure(foreground=entry.normal_color)
            entry.placeholder_active = False

    def on_focus_in(_event):
        clear_placeholder()

    def on_focus_out(_event):
        if not entry.get():
            show_placeholder()
            if on_change:
                on_change()

    def on_key(_event):
        clear_placeholder()

    entry.bind("<FocusIn>", on_focus_in)
    entry.bind("<FocusOut>", on_focus_out)
    entry.bind("<Key>", on_key, add="+")

    show_placeholder()


