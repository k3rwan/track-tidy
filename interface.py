"""
Graphical interface for the audio tagging module (track_tidy.py, imported
below as `tagger`).

Flow:
1. Choose the folder
2. Click "Scan" -> each file appears as soon as it's analyzed, with a suggested
   pre-checked "Apply" state
   - Unchecked: shows the CURRENT info of the file (existing artist/title/cover)
   - Checked: shows the SUGGESTED info (inferred from the filename + online cover)
3. Click the checkbox/Format cell to toggle it; double-click Title/Artist to edit
4. Click "Apply"
"""

import getpass
import io
import os
import re
import socket
import sys
import subprocess
import tempfile
import threading
import time
import traceback
import queue
import webbrowser
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
from tkinter import font as tkfont
from PIL import Image, ImageTk, ImageDraw

if sys.platform == "win32":
    import winsound

# When launched via pythonw.exe (no console), sys.stdout/stderr are None.
# Any leftover print() call would then crash with AttributeError. Redirect
# them to a no-op stream so nothing ever breaks silently because of this.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import track_tidy as tagger


# --- Cross-platform OS integration (Windows / macOS) ---

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


# Arbitrary fixed high port, unlikely to collide with anything else -
# bound for the app's entire lifetime purely as a mutex (nothing ever
# connects to it). Kept alive by _SINGLE_INSTANCE_LOCK holding a reference
# to the socket so it isn't garbage-collected (which would close it).
SINGLE_INSTANCE_PORT = 51793
_SINGLE_INSTANCE_LOCK = None


def acquire_single_instance_lock():
    """Returns True if this is the only running instance of the app, False
    if another one already holds the lock (e.g. the user double-clicked the
    shortcut/exe more than once) - binding a local TCP socket is a simple,
    dependency-free mutex that works the same way on Windows/macOS/Linux and
    is automatically released by the OS if the process ever dies without
    cleaning up."""
    global _SINGLE_INSTANCE_LOCK
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
    except OSError:
        sock.close()
        return False
    _SINGLE_INSTANCE_LOCK = sock  # keep it alive/bound for the app's lifetime
    return True


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
# Widened from the original 500px so the bigger thumbnails and wider
# Title/Artist columns actually have room to breathe. This is the size at
# REFERENCE_SCREEN_WIDTH/_HEIGHT - TaggerInterface.__init__ rescales both
# this and BASE_WINDOW_HEIGHT to the user's actual screen resolution
# before the window is ever shown, and every other place in this file
# that sizes something off WINDOW_WIDTH reads the module global at call
# time, so the rescale (a `global` reassignment) reaches all of them.
WINDOW_WIDTH = 620
BASE_WINDOW_HEIGHT = 650
REFERENCE_SCREEN_WIDTH = 1920
REFERENCE_SCREEN_HEIGHT = 1080
# Never shrink below the original hand-tuned size (a smaller window than
# that has never been tested and could clip something), and never grow
# past 1.6x it either - a 4K/5K display would otherwise get a window far
# bigger than the fixed-width table columns and 44px thumbnails actually
# need.
MIN_WINDOW_SCALE = 1.0
MAX_WINDOW_SCALE = 1.6

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


class TaggerInterface:
    def __init__(self, window):
        self.window = window
        # Hidden until the theme warm-up finishes (see the call site further
        # down) - otherwise the light/dark flicker during warm-up would be
        # visible for a moment on every launch.
        self.window.withdraw()
        self.base_title = "Track Tidy (beta)"
        self.window.title(self.base_title)
        icon_path = resource_path("assets/track-tidy_icon.ico")
        icon_png_path = resource_path("assets/track-tidy_icon.png")

        if os.path.exists(icon_path):
            self.window.iconbitmap(icon_path)
        if os.path.exists(icon_png_path):
            self._icon_image_ref = ImageTk.PhotoImage(file=icon_png_path)  # keep a reference
            self.window.iconphoto(True, self._icon_image_ref)

        # Scales the window - and the tables' own row counts below, since a
        # wider/taller window shell alone wouldn't show any more rows: both
        # ttk.Treeviews below are built with an explicit `height` in ROWS,
        # which is what actually determines the window's natural height
        # (see _adjust_window_height) - a plain geometry() bump would just
        # get overwritten by that recompute right after _build_interface().
        global WINDOW_WIDTH
        self.window_scale = min(
            self.window.winfo_screenwidth() / REFERENCE_SCREEN_WIDTH,
            self.window.winfo_screenheight() / REFERENCE_SCREEN_HEIGHT,
        )
        self.window_scale = max(MIN_WINDOW_SCALE, min(self.window_scale, MAX_WINDOW_SCALE))
        WINDOW_WIDTH = round(WINDOW_WIDTH * self.window_scale)
        self.window.geometry(f"{WINDOW_WIDTH}x{round(BASE_WINDOW_HEIGHT * self.window_scale)}")
        self.window.resizable(False, False)  # prevents fullscreen / resizing

        self.message_queue = queue.Queue()
        # Catches an unhandled exception wherever it happens - a Tk callback
        # on the main thread (report_callback_exception, Tkinter's own hook)
        # or a background scan/extraction/quality/update thread
        # (threading.excepthook, process-wide) - and reports it to Discord
        # (see _report_crash) instead of it only ever surfacing, if at all,
        # as a silent failure or a one-line stderr print nobody sees (a
        # pythonw.exe build has no console at all - see the sys.stdout/
        # stderr redirect above). Deduped per (context, exception type,
        # message) so a crash that keeps recurring (e.g. on every redraw)
        # doesn't flood the channel.
        self._reported_crash_signatures = set()
        self.window.report_callback_exception = self._handle_tk_exception
        threading.excepthook = self._handle_thread_exception
        self._is_online = True  # optimistic default until the first real check lands
        self.processing_in_progress = False
        self._processing_failures = []  # (identifier, reason) pairs - see _show_processing_failures_dialog
        self.cancel_requested = threading.Event()
        # Separate from cancel_requested above - Scan/Apply and Extract are
        # independent background actions that could in principle overlap
        # (different tabs, nothing stops both running at once), so sharing
        # one Event would let cancelling one spuriously cancel the other.
        self.extract_cancel_requested = threading.Event()
        # Same reasoning as extract_cancel_requested above - the Quality
        # tab's own scan is a third independent background action.
        self.quality_cancel_requested = threading.Event()
        # The folder a Quality scan's results are relative to, and a map of
        # each result row's item id -> its absolute path - both needed to
        # resolve a double-clicked row back to a real file for the spectrum
        # viewer (analyze_folder_quality() only returns paths relative to
        # the scanned folder).
        self.quality_last_scanned_folder = None
        self.quality_row_paths = {}
        self._quality_spectrogram_requests = {}
        self._quality_scan_counts = {tagger.QUALITY_GREEN: 0, tagger.QUALITY_ORANGE: 0, tagger.QUALITY_RED: 0}
        # Same paced-reveal pattern as _pending_scan_reveals/_pending_scan_done
        # below, for the Quality tab's own scan - see _reveal_next_quality_row().
        self._pending_quality_reveals = []
        self._pending_quality_scan_done = None
        # Cycles 0 -> 1 (worst/red on top) -> 2 (best/green on top) -> 0
        # (back to scan/arrival order) on each click of the verdict dot
        # column's heading - see _on_quality_verdict_heading_click.
        self._quality_verdict_sort_state = 0
        self._quality_default_row_order = None
        # Total file count for the current scan, used to drive the
        # progress bar off the paced reveal (see _add_quality_row) rather
        # than the background analysis, which runs far ahead of it.
        self._quality_scan_total = 0
        # Remembers the folder the user last manually located a moved
        # history file in (see restore_selected in the History window) -
        # kept at the app level, not just for one Restore call, since a
        # user restoring several moved tracks one at a time (not all
        # selected together) should still benefit from it on the 2nd, 3rd...
        # track, not just within a single multi-select batch.
        self._history_restore_located_folder = None
        self.scanned_plan = []
        self.tk_images = {}  # keeps a reference to PhotoImages (otherwise Tkinter clears them)
        self.tk_images_hover = {}  # same thumbnails, with a magnifier badge - built lazily on first hover
        self._thumbnail_pil_images = {}  # pre-PhotoImage PIL images, so the hover badge can be composited cheaply
        self._fix_dialog_rows = {}  # file -> row widgets, while the "fix no-cover tracks" dialog is open
        self._hovered_cover_row = None
        self._tooltip_window = None
        self._tooltip_key = None
        self._pending_double_click_after_id = None
        # Ctrl+Z stack, most recent last - entries are ("removal", [(original_index, info), ...])
        # per Delete/"Remove from list" action, or ("edit", {...}) per Title/Artist cell edit.
        self._undo_stack = []
        self._drag_row_id = None  # row being dragged to reorder, if any
        self._table_font = tkfont.nametofont("TkDefaultFont")
        self._reset_scan_run_state()
        self.mention_counts = {}  # raw mention text -> number of times seen

        # Must run before any saved setting below is actually read into a
        # UI var - resets settings.json to defaults on the first launch
        # after an update, so the values loaded just below are already the
        # fresh defaults rather than whatever the previous version saved.
        tagger.check_and_apply_version_reset()

        self._native_theme = ttk.Style().theme_use()  # so "light" can restore it later
        self.theme_colors = None  # None while light/native; DARK_COLORS once dark is applied
        saved_theme = tagger.load_settings().get("theme", "auto")
        if saved_theme not in ("light", "dark", "auto"):
            saved_theme = "light"  # e.g. an old "system" preference from before that option existed
        self.theme_var = tk.StringVar(value=saved_theme)

        saved_settings = tagger.load_settings()

        # Restores the last folder explicitly chosen via Browse... (see
        # _choose_folder, which is also the only place that saves it) - not
        # a folder only ever reached by drag-and-drop, which by design never
        # touches this field. Silently ignored if the folder's gone (moved,
        # deleted, a different drive not connected right now) - the field
        # just starts empty like it always used to, no error shown for it.
        saved_music_folder = saved_settings.get("music_folder", "")
        if saved_music_folder and os.path.isdir(saved_music_folder):
            tagger.MUSIC_FOLDER = saved_music_folder

        self.auto_convert_var = tk.BooleanVar(value=saved_settings.get("auto_convert_mp3", False))
        self.auto_convert_wav_aiff_var = tk.BooleanVar(value=saved_settings.get("auto_convert_wav_to_aiff", True))
        self.fix_track_file_name_var = tk.BooleanVar(value=saved_settings.get("fix_track_file_name", True))
        self.use_spotify_var = tk.BooleanVar(value=saved_settings.get("use_spotify", False))
        self.show_log_var = tk.BooleanVar(value=saved_settings.get("show_log_section", False))
        self.use_telemetry_var = tk.BooleanVar(value=saved_settings.get("send_usage_telemetry", True))
        self._tagger_resize_pending = False
        tagger.AUTO_CONVERT_MP3 = self.auto_convert_var.get()
        tagger.AUTO_CONVERT_WAV_TO_AIFF = self.auto_convert_wav_aiff_var.get()
        tagger.FIX_TRACK_FILE_NAME = self.fix_track_file_name_var.get()
        tagger.USE_SPOTIFY = self.use_spotify_var.get()
        tagger.SEND_USAGE_TELEMETRY = self.use_telemetry_var.get()

        self._build_interface()
        # Tagger's folder may already be pre-filled from a saved setting
        # (see folder_variable's construction above) - mirror it into
        # Extractor/Quality right away so they aren't left blank while
        # Tagger already has one, same as a fresh selection does.
        if self.folder_variable.get():
            self._sync_all_folder_pickers(self.folder_variable.get())
        self._setup_drag_and_drop()
        self._adjust_window_height()
        self._apply_theme(self._resolve_theme_choice(self.theme_var.get()))
        # Warm-up pass: the very first paint (before the window is ever
        # actually mapped) computes native-theme widget metrics a few
        # pixels too small - re-applying the theme once the window is
        # really up fixes it, but doing this synchronously here is too
        # early (mainloop() hasn't started yet), so it's deferred.
        self.window.after(100, self._rewarm_theme)
        self._start_message_loop()
        self._reveal_next_scan_row()
        self._reveal_next_quality_row()
        self._check_for_update_on_startup()
        self._check_internet_connection(is_startup_check=True)
        self._notify_new_install_on_startup()
        self._check_source_health_on_startup()

    # --- Theme & dialog helpers ---

    def _resolve_theme_choice(self, choice):
        """Resolves the user's saved preference ("light"/"dark"/"auto") to
        an actual "light"/"dark" for _apply_theme() - which only knows
        those two. "auto" picks light or dark from the current local hour
        (see AUTO_THEME_LIGHT_START_HOUR/AUTO_THEME_DARK_START_HOUR)."""
        if choice != "auto":
            return choice
        hour = datetime.now().hour
        return "light" if AUTO_THEME_LIGHT_START_HOUR <= hour < AUTO_THEME_DARK_START_HOUR else "dark"

    def _rewarm_theme(self, step=0):
        """See the comment at the __init__ call site: fixes the native
        theme's widget metrics being subtly wrong on the very first paint.
        Each step is a separate event-loop turn (via after()) rather than
        back-to-back calls - ttk/Windows only recomputes native-theme
        metrics correctly with a real idle turn between theme switches."""
        steps = ("light", "dark", self._resolve_theme_choice(self.theme_var.get()))
        self._apply_theme(steps[step])
        if step + 1 < len(steps):
            self.window.after(50, lambda: self._rewarm_theme(step + 1))
        else:
            self._adjust_window_height()
            self.window.deiconify()
            # DWMWA_BORDER_COLOR (unlike DWMWA_USE_IMMERSIVE_DARK_MODE) doesn't
            # reliably stick when set while the window is still withdrawn -
            # DWM seems to reset the frame's border to the OS default the
            # first time the window actually becomes visible. Re-applying it
            # now, right after deiconify(), on the now-visible window fixes it.
            self._set_titlebar_dark(self.window, bool(self.theme_colors))
            if self.theme_var.get() == "auto":
                self._schedule_auto_theme_recheck()

    def _on_theme_changed(self):
        choice = self.theme_var.get()
        self._apply_theme(self._resolve_theme_choice(choice))
        tagger.save_setting("theme", choice)
        tagger.log_action(f"Theme changed to '{choice}'")
        if choice == "auto":
            self._schedule_auto_theme_recheck()

    def _schedule_auto_theme_recheck(self):
        """While "Automatic" is selected, periodically re-resolves and
        re-applies the theme so it actually flips if the app is left open
        across a AUTO_THEME_LIGHT_START_HOUR/AUTO_THEME_DARK_START_HOUR
        boundary, instead of only updating on the next launch. Stops
        rescheduling itself as soon as the user picks a fixed theme
        instead - checked fresh on every tick rather than cancelled via
        after_cancel, since nothing else ever needs to interrupt this."""
        def _recheck():
            if self.theme_var.get() != "auto":
                return
            self._apply_theme(self._resolve_theme_choice("auto"))
            self._schedule_auto_theme_recheck()

        self.window.after(AUTO_THEME_RECHECK_INTERVAL_MS, _recheck)

    def _on_auto_convert_changed(self):
        enabled = self.auto_convert_var.get()
        if enabled and self.auto_convert_wav_aiff_var.get():
            self.auto_convert_wav_aiff_var.set(False)
            tagger.AUTO_CONVERT_WAV_TO_AIFF = False
            tagger.save_setting("auto_convert_wav_to_aiff", False)
            messagebox.showinfo(
                "Convert WAV to AIFF disabled",
                "\"Convert everything to MP3\" and \"Convert WAV to AIFF\" are two "
                "different destinies for WAV files, so only one can be on at a time - "
                "\"Convert WAV to AIFF\" has been turned off.",
                parent=self.window,
            )
        elif not enabled:
            wav_fate = (
                "converted to AIFF" if self.auto_convert_wav_aiff_var.get() else "tagged (and get a cover) in place, kept as WAV"
            )
            messagebox.showwarning(
                "Convert to MP3 disabled",
                f"WAV files will now be {wav_fate}. FLAC files will be tagged "
                "(and get a cover) in place too. Other non-MP3 formats "
                "(M4A, OGG...) will be ignored when scanning - they can't be "
                "tagged without converting to MP3 first.",
                parent=self.window,
            )
        tagger.AUTO_CONVERT_MP3 = enabled
        tagger.save_setting("auto_convert_mp3", enabled)
        tagger.log_action(f"Convert everything to MP3: {enabled}")

    def _on_auto_convert_wav_aiff_changed(self):
        enabled = self.auto_convert_wav_aiff_var.get()
        if enabled and self.auto_convert_var.get():
            self.auto_convert_var.set(False)
            tagger.AUTO_CONVERT_MP3 = False
            tagger.save_setting("auto_convert_mp3", False)
            messagebox.showinfo(
                "Convert everything to MP3 disabled",
                "\"Convert everything to MP3\" and \"Convert WAV to AIFF\" are two "
                "different destinies for WAV files, so only one can be on at a time - "
                "\"Convert everything to MP3\" has been turned off.",
                parent=self.window,
            )
        tagger.AUTO_CONVERT_WAV_TO_AIFF = enabled
        tagger.save_setting("auto_convert_wav_to_aiff", enabled)
        tagger.log_action(f"Convert WAV to AIFF: {enabled}")

    def _on_fix_track_file_name_changed(self):
        enabled = self.fix_track_file_name_var.get()
        tagger.FIX_TRACK_FILE_NAME = enabled
        tagger.save_setting("fix_track_file_name", enabled)
        tagger.log_action(f"Fix track file name: {enabled}")

    def _on_use_spotify_changed(self):
        enabled = self.use_spotify_var.get()
        tagger.USE_SPOTIFY = enabled
        tagger.save_setting("use_spotify", enabled)
        tagger.log_action(f"Use Spotify: {enabled}")

    def _reset_settings_to_default(self):
        """Restores every Settings-tab option to its out-of-the-box value.
        Deliberately bypasses the individual _on_X_changed() handlers -
        several of them (auto-convert) pop up an explanatory messagebox on
        every change, which would mean a wall of unwanted dialogs to click
        through here."""
        if not messagebox.askyesno(
            "Reset settings",
            "Reset all settings to their default values?",
            parent=self.window, default=messagebox.NO,
        ):
            return

        for key, value in tagger.DEFAULT_SETTINGS.items():
            tagger.save_setting(key, value)
        tagger.log_action("All settings reset to default")

        tagger.AUTO_CONVERT_MP3 = False
        tagger.AUTO_CONVERT_WAV_TO_AIFF = True
        tagger.FIX_TRACK_FILE_NAME = True
        tagger.USE_SPOTIFY = False
        tagger.SEND_USAGE_TELEMETRY = True

        self.auto_convert_var.set(False)
        self.auto_convert_wav_aiff_var.set(True)
        self.fix_track_file_name_var.set(True)
        self.use_spotify_var.set(False)
        self.use_telemetry_var.set(True)

        self.show_log_var.set(False)
        self._on_show_log_changed()

        self.theme_var.set("auto")
        self._apply_theme(self._resolve_theme_choice("auto"))
        self._schedule_auto_theme_recheck()

    def _on_show_log_changed(self):
        enabled = self.show_log_var.get()
        tagger.save_setting("show_log_section", enabled)
        tagger.log_action(f"Show log section: {enabled}")

        if enabled:
            if not self.journal_toggle.winfo_ismapped():
                self.journal_toggle.pack(anchor="w", padx=10, pady=(0, 5), before=self.launch_frame)
        else:
            if self.journal_section_visible:
                # Collapse without going through _toggle_journal_section() -
                # that one always resizes the window immediately, which is
                # right when the user clicks it on the Tagger tab, but not
                # here (the single resize decision below already handles it).
                self.journal_section_visible = False
                self.journal_frame.pack_forget()
                self.journal_toggle.configure(text="▸ Log")
            self.journal_toggle.pack_forget()

        # This setting only changes the Tagger tab's layout - resizing the
        # window right now would visibly affect whatever tab the user is
        # actually looking at (e.g. Settings) even though nothing there
        # changed. Only resize immediately if Tagger is the active tab;
        # otherwise the pending resize is applied once the user switches
        # back to it (see _on_tab_changed).
        if self.notebook.index("current") == 0:
            self._adjust_window_height()
        else:
            self._tagger_resize_pending = True

    def _on_tab_changed(self, event=None):
        if self._tagger_resize_pending and self.notebook.index("current") == 0:
            self._tagger_resize_pending = False
        # The window height is otherwise only ever computed from whichever
        # tab happened to be active at startup (or the last explicit
        # resize) - Settings/Extractor's own content can grow past that
        # (e.g. Settings picking up a new row of controls) without ever
        # triggering a resize, so widgets packed at the bottom (the footer
        # links/legal text) end up cramped or overlapping. Recomputing on
        # every tab switch keeps the window actually fitting whatever's
        # visible.
        self._adjust_window_height()
        if self.notebook.index("current") == 2:  # Settings - recheck right away rather than
            self._check_internet_connection()      # waiting for the next background 10s poll
            # Windows' native theme draws a visible focus rectangle around
            # whichever radiobutton last had keyboard focus - with nothing
            # having focused this group yet, that defaults to the first
            # one in tab order ("Light"), regardless of which is actually
            # SELECTED (the filled dot). Misleading on first look at
            # Settings - real report: a focus box around "Light" while
            # "Automatic" was the actual saved theme. Point it at whichever
            # is really selected instead, every time this tab is shown (a
            # theme change elsewhere - e.g. Reset settings - could have
            # moved the selection without ever touching this focus ring).
            selected = self._theme_radio_buttons.get(self.theme_var.get())
            if selected:
                selected.focus_set()

    def _style_toplevel(self, dialog):
        """Applies the current theme's background (and title bar) to a dialog
        window (its ttk children already follow the active ttk style
        automatically).

        Withdraws the window first - a freshly-created Toplevel maps itself
        immediately, at whatever position Tk defaults to and with no
        content packed into it yet, so without this a dark-themed dialog
        briefly flashes as an empty black rectangle in the wrong spot
        before its widgets and _center_dialog() catch up. The caller's
        matching _center_dialog() call deiconifies it again once it's
        actually ready to show."""
        dialog.withdraw()
        if self.theme_colors:
            dialog.configure(bg=self.theme_colors["bg"])
        dialog.update_idletasks()  # make sure the native HWND exists before DwmSetWindowAttribute
        self._set_titlebar_dark(dialog, bool(self.theme_colors))

    def _run_in_background(self, target, *args):
        """Runs target(*args) in a daemon thread - the pattern every
        long-running action (scan, process, extract, update check) uses to
        keep the UI responsive, since none of them are safe to call directly
        from the Tk main thread."""
        thread = threading.Thread(target=target, args=args, daemon=True)
        thread.start()
        return thread

    def _handle_tk_exception(self, exc_type, exc_value, exc_tb):
        """Replaces Tkinter's default report_callback_exception (which just
        prints to stderr - invisible on a console-less pythonw.exe build) -
        runs on the main thread, since that's what raised a Tk callback
        (button command, event binding...)."""
        traceback.print_exception(exc_type, exc_value, exc_tb)  # keep local visibility too
        self._report_crash(exc_type, exc_value, exc_tb, context="ui_callback")

    def _handle_thread_exception(self, args):
        """threading.excepthook target - catches an exception that killed a
        background thread (scan/extraction/quality/update-check...) before
        it ever reaches a message_queue "done" message, which would
        otherwise leave the UI stuck (Cancel/progress bar never reset) with
        no visible explanation why."""
        traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback)
        self._report_crash(args.exc_type, args.exc_value, args.exc_traceback, context="background_thread")

    def _report_crash(self, exc_type, exc_value, exc_tb, context):
        if exc_type is None:
            return
        # Same (context, type, message) signature regardless of how many
        # times it recurs in this run - a broken binding firing on every
        # mouse move would otherwise flood Discord with hundreds of
        # identical reports.
        signature = (context, exc_type.__name__, str(exc_value))
        if signature in self._reported_crash_signatures:
            return
        self._reported_crash_signatures.add(signature)

        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            reporter_name = getpass.getuser()
        except Exception:
            reporter_name = ""
        self._run_in_background(tagger.send_crash_report, reporter_name, tb_text, context)

    def _sync_mentions_to_remove(self):
        """Pushes the current "To remove" listbox contents to the tagger
        module - a Tk widget read, so it must happen on the main thread,
        before handing off to any background scan/rescan thread that reads
        tagger.MENTIONS_TO_REMOVE."""
        tagger.MENTIONS_TO_REMOVE = list(self.mentions_listbox.get(0, "end"))

    def _center_dialog(self, dialog):
        """Centers a dialog over the main window, clamped to stay fully
        on-screen - a dialog wider/taller than the main window (e.g. the
        history table) would otherwise end up centered partly off-screen.

        Also deiconifies it - the matching partner to _style_toplevel()'s
        withdraw(), so the dialog only actually becomes visible once it's
        both fully built and correctly positioned. A dialog that was never
        withdrawn (no _style_toplevel() call) is unaffected - deiconify()
        on an already-mapped window is a no-op."""
        dialog.update_idletasks()
        x = self.window.winfo_x() + (self.window.winfo_width() - dialog.winfo_width()) // 2
        y = self.window.winfo_y() + (self.window.winfo_height() - dialog.winfo_height()) // 2
        x = max(0, min(x, dialog.winfo_screenwidth() - dialog.winfo_width()))
        y = max(0, min(y, dialog.winfo_screenheight() - dialog.winfo_height()))
        dialog.geometry(f"+{x}+{y}")
        dialog.deiconify()

    def _bind_canvas_mousewheel(self, canvas):
        """Makes a scrollable Canvas respond to the mouse wheel. Tk never
        delivers wheel events to a Canvas on its own - bind_all is the
        standard workaround, but since that's global, it's only active
        while the cursor is actually over the canvas (wired/torn down on
        Enter/Leave) so it doesn't hijack scrolling in the rest of the app.
        Returns an unbind() callable - call it when the canvas's dialog
        closes, in case it's torn down while the cursor is still hovering
        (no Leave event would otherwise fire)."""
        def _on_wheel(event):
            # Windows/macOS deliver <MouseWheel> with event.delta; Windows
            # reports it in multiples of 120, macOS in raw small steps.
            if sys.platform == "darwin":
                canvas.yview_scroll(-1 * event.delta, "units")
            else:
                canvas.yview_scroll(-1 * int(event.delta / 120), "units")

        def _on_wheel_linux(event):
            canvas.yview_scroll(-1 if event.num == 4 else 1, "units")

        def _bind(_event=None):
            canvas.bind_all("<MouseWheel>", _on_wheel)
            canvas.bind_all("<Button-4>", _on_wheel_linux)
            canvas.bind_all("<Button-5>", _on_wheel_linux)

        def _unbind(_event=None):
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        canvas.bind("<Enter>", _bind)
        canvas.bind("<Leave>", _unbind)
        return _unbind

    def _make_themed_menu(self, parent):
        """A tk.Menu with the current theme's colors applied - tk.Menu is
        plain Tk, not ttk, so it doesn't pick up theme colors on its own
        the way ttk widgets do."""
        menu = tk.Menu(parent, tearoff=0)
        if self.theme_colors:
            menu.configure(
                bg=self.theme_colors["menu_bg"], fg=self.theme_colors["menu_fg"],
                activebackground=self.theme_colors["select_bg"], activeforeground=self.theme_colors["select_fg"],
            )
        return menu

    def _bind_entry_context_menu(self, entry, readonly=False, on_paste_folder=None):
        """Adds a right-click Cut/Copy/Paste/Select All menu to an Entry -
        unlike native Windows edit controls, Tk Entry widgets don't get one
        for free. A readonly entry drops Cut/Paste (there's no cursor to
        type/insert at) - UNLESS on_paste_folder is given: the three
        folder-path fields are readonly to prevent free-typed garbage
        paths, but a path copied from Explorer's own address bar is
        exactly as valid as one picked via Browse..., so "Paste" still
        appears there, wired to validate the clipboard text as an actual
        folder and hand it to on_paste_folder instead of inserting text."""
        def paste_folder():
            try:
                clipboard_text = self.window.clipboard_get().strip().strip('"')
            except tk.TclError:
                return
            if os.path.isdir(clipboard_text):
                on_paste_folder(clipboard_text)
            else:
                messagebox.showwarning(
                    "Not a folder", f"'{clipboard_text}' is not a valid folder.", parent=self.window,
                )

        def show_menu(event):
            menu = self._make_themed_menu(entry)
            if not readonly:
                menu.add_command(label="Cut", command=lambda: entry.event_generate("<<Cut>>"))
            menu.add_command(label="Copy", command=lambda: entry.event_generate("<<Copy>>"))
            if not readonly:
                menu.add_command(label="Paste", command=lambda: entry.event_generate("<<Paste>>"))
            elif on_paste_folder:
                menu.add_command(label="Paste", command=paste_folder)
            menu.add_separator()
            menu.add_command(label="Select All", command=lambda: entry.select_range(0, "end"))
            menu.tk_popup(event.x_root, event.y_root)

        entry.bind("<Button-3>", show_menu)

    def _build_folder_icon_photo(self, dark, size=16):
        """Small flat folder glyph shown to the left of the parent-folder
        path - drawn as a raster image (like the checkbox indicator)
        rather than a Unicode folder emoji, so it's a plain solid color
        instead of an uncontrollable colored glyph that wouldn't match the
        theme. A single connected polygon (tab + body as one outline), not
        two separate rectangles - at this size two adjacent-but-separate
        shapes rendered as a visible seam instead of reading as one folder.
        Recolored per theme and rebuilt in _apply_theme, like the checkbox
        indicator - it was previously built once with a fixed color and
        never updated on a light/dark switch."""
        color = "#c1c1c1" if dark else "#707070"
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        points = [
            (size * 0.06, size * 0.25), (size * 0.06, size * 0.81), (size * 0.94, size * 0.81),
            (size * 0.94, size * 0.34), (size * 0.42, size * 0.34), (size * 0.34, size * 0.25),
        ]
        draw.polygon(points, fill=color)
        return ImageTk.PhotoImage(image)

    def _build_empty_state_icon_photo(self, dark, size=110):
        """
        "Drag an audio file here" glyph for the empty Tagger table (see
        empty_state_frame): a dashed drop-zone square around an audio-file
        icon (rounded document + eighth note) with a mouse cursor
        overlapping its corner - same drag-and-drop visual language as the
        generic stock icons this was modeled after, just swapping their
        photo/image glyph for an audio one. Drawn by hand (like
        _build_folder_icon_photo) rather than a bundled image file, so it
        recolors correctly per theme with zero extra assets.
        """
        color = "#9a9a9a" if dark else "#4a4a4a"
        cursor_fill = color if dark else "#ffffff"
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        # Dashed drop-zone border - PIL has no native dashed-line primitive,
        # so each side is walked in fixed dash/gap steps by hand.
        pad = size * 0.05
        dash, gap = size * 0.06, size * 0.045
        border_width = max(2, round(size * 0.018))

        def dashed_line(x1, y1, x2, y2):
            length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            if length == 0:
                return
            dx, dy = (x2 - x1) / length, (y2 - y1) / length
            travelled = 0.0
            while travelled < length:
                seg_end = min(travelled + dash, length)
                draw.line(
                    [(x1 + dx * travelled, y1 + dy * travelled), (x1 + dx * seg_end, y1 + dy * seg_end)],
                    fill=color, width=border_width,
                )
                travelled = seg_end + gap

        dashed_line(pad, pad, size - pad, pad)
        dashed_line(size - pad, pad, size - pad, size - pad)
        dashed_line(size - pad, size - pad, pad, size - pad)
        dashed_line(pad, size - pad, pad, pad)

        # Audio-file icon (rounded document with a folded corner) - smaller
        # than the drop-zone and shifted up-left, leaving room for the
        # cursor to overlap its bottom-right corner like the reference.
        file_left, file_top = size * 0.24, size * 0.20
        file_right, file_bottom = size * 0.66, size * 0.66
        fold = size * 0.12
        file_width = max(2, round(size * 0.028))
        draw.line(
            [
                (file_left, file_top + fold * 0.4), (file_left, file_bottom),
                (file_right, file_bottom), (file_right, file_top + fold),
                (file_right - fold, file_top), (file_left + fold * 0.6, file_top),
            ],
            fill=color, width=file_width, joint="curve",
        )
        # Rounds the two corners the plain polyline above leaves sharp
        # (top-left and the folded corner's outer edge), matching the
        # rounded-document look real file-type icons use.
        draw.arc(
            [file_left, file_top, file_left + fold * 1.2, file_top + fold * 1.2],
            180, 270, fill=color, width=file_width,
        )
        draw.line(
            [(file_right - fold, file_top), (file_right, file_top + fold)],
            fill=color, width=file_width,
        )

        # Eighth note, centered in the file - a filled notehead, a stem,
        # and a small flag, same silhouette as a standard music glyph.
        note_cx, note_cy = (file_left + file_right) / 2, (file_top + file_bottom) / 2 + size * 0.03
        head_rx, head_ry = size * 0.055, size * 0.042
        stem_top = note_cy - size * 0.16
        draw.ellipse(
            [note_cx - head_rx, note_cy - head_ry, note_cx + head_rx, note_cy + head_ry], fill=color,
        )
        draw.line(
            [(note_cx + head_rx * 0.85, note_cy), (note_cx + head_rx * 0.85, stem_top)],
            fill=color, width=max(2, round(size * 0.022)),
        )
        draw.line(
            [
                (note_cx + head_rx * 0.85, stem_top),
                (note_cx + head_rx * 0.85 + size * 0.09, stem_top + size * 0.025),
                (note_cx + head_rx * 0.85 + size * 0.03, stem_top + size * 0.08),
            ],
            fill=color, width=max(2, round(size * 0.018)), joint="curve",
        )

        # Mouse cursor overlapping the file's bottom-right corner, same
        # composition as the reference drag-and-drop icons.
        cx, cy = file_right - size * 0.06, file_bottom - size * 0.08
        cursor_scale = size * 0.34
        cursor_points = [
            (0.00, 0.00), (0.00, 0.75), (0.18, 0.58), (0.30, 0.98),
            (0.42, 0.93), (0.30, 0.53), (0.55, 0.53),
        ]
        cursor_polygon = [(cx + px * cursor_scale, cy + py * cursor_scale) for px, py in cursor_points]
        draw.polygon(cursor_polygon, fill=cursor_fill, outline=color, width=max(2, round(size * 0.018)))

        return ImageTk.PhotoImage(image)

    @staticmethod
    def _load_extractor_preview_photos(dark, target_total_width=340):
        """Loads the Extractor tab's before/after screenshot crops for the
        current theme (assets/extractor-{before,after}-{dark,light}.png -
        see the preview frame's own comment for why two theme variants
        exist). Both crops come from the same source window/DPI, so a
        folder icon and a file icon are the same number of PIXELS wide in
        the raw files - scaling each crop to its OWN target width (as this
        used to do) stretched them by different factors and made the
        icons look mismatched in size. Scaling both by one shared factor
        (picked from their combined width) keeps that native ratio intact.
        Returns (None, None) if either asset is missing."""
        sources = {}
        for side in ("before", "after"):
            suffix = "dark" if dark else "light"
            path = resource_path(f"assets/extractor-{side}-{suffix}.png")
            if not os.path.exists(path):
                return None, None
            sources[side] = Image.open(path)

        combined_width = sources["before"].width + sources["after"].width
        scale = target_total_width / combined_width
        photos = {}
        for side, source in sources.items():
            size = (round(source.width * scale), round(source.height * scale))
            photos[side] = ImageTk.PhotoImage(source.resize(size, Image.LANCZOS))
        return photos["before"], photos["after"]

    def _build_gray_dot_photo(self, size=10):
        """Static neutral dot shown in the Quality table's own verdict dot
        column heading (#0) - a plain drawn circle rather than a colored
        dot/emoji, since the heading itself has no verdict, just marks
        which column the per-row colored dots belong to. Same reasoning as
        the row dots themselves for why this is a real drawn shape and not
        a Unicode/emoji glyph - stays a flat, correctly-colored gray
        regardless of theme or platform font rendering."""
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse([0, 0, size - 1, size - 1], fill="#999999")
        return ImageTk.PhotoImage(image)

    def _build_checkbox_indicator_photo(self, box_bg, box_border, checked, size=13):
        """
        Draws one checkbox indicator state (empty box, or filled box with a
        checkmark) as a small raster image - clam's own indicator element
        can't draw a custom mark, only its own hardcoded tick, so the
        indicator is fully custom-drawn here instead.
        """
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        fill = INDICATOR_CHECKED_BG if checked else box_bg
        draw.rectangle([0, 0, size - 1, size - 1], fill=fill, outline=box_border, width=1)
        if checked:
            points = [
                (size * 0.22, size * 0.52), (size * 0.42, size * 0.72), (size * 0.80, size * 0.28),
            ]
            width = max(2, size // 7)
            draw.line(points, fill="#ffffff", width=width, joint="curve")
            radius = width / 2
            for x, y in points:
                draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill="#ffffff")
        return ImageTk.PhotoImage(image)

    def _ensure_checkbox_indicator_images(self, style, dark):
        """
        Registers the "Light.Checkbutton.indicator" / "Dark.Checkbutton.indicator"
        image elements the first time either theme is applied - element_create
        can't be re-called for a name that already exists, so this only ever
        builds each theme's pair of images once, then _apply_theme just picks
        which name the TCheckbutton layout points at.
        """
        if not hasattr(self, "_checkbox_indicator_photos"):
            self._checkbox_indicator_photos = {}
        theme_key = "dark" if dark else "light"
        element_name = f"{'Dark' if dark else 'Light'}.Checkbutton.indicator"
        if element_name in style.element_names():
            return
        box_bg, box_border = (
            (DARK_COLORS["entry_bg"], DARK_COLORS["border"]) if dark
            else (LIGHT_INDICATOR_COLORS["indicator_bg"], LIGHT_INDICATOR_COLORS["indicator_border"])
        )
        unchecked_photo = self._build_checkbox_indicator_photo(box_bg, box_border, checked=False)
        checked_photo = self._build_checkbox_indicator_photo(box_bg, box_border, checked=True)
        self._checkbox_indicator_photos[theme_key] = (unchecked_photo, checked_photo)
        style.element_create(element_name, "image", unchecked_photo, ("selected", checked_photo))

    def _apply_theme(self, choice):
        dark = choice == "dark"
        colors = DARK_COLORS if dark else None
        style = ttk.Style()
        style.theme_use("clam" if dark else self._native_theme)

        # Custom style names are scoped to the CURRENTLY active ttk theme, so
        # switching theme_use() above resets them - both of these have to be
        # re-applied every time, for every theme, not just for dark mode.
        style.configure(
            "Table.Treeview", rowheight=TABLE_ROW_HEIGHT,
            font=(self._table_font.actual("family"), self._table_font.actual("size")),
        )

        # Same reasoning as Table.Treeview above, but for Radiobutton: pull
        # clam's own indicator element into whichever theme is active
        # (native "vista" included) so light and dark render the identical
        # dot shape, just recolored. Guarded by element_names() -
        # re-creating an element that already exists in the current theme
        # raises a TclError, but once created it's visible from every
        # theme, so this only actually runs once.
        if "Uniform.Radiobutton.indicator" not in style.element_names():
            style.element_create("Uniform.Radiobutton.indicator", "from", "clam", "Radiobutton.indicator")
        # Checkbutton's "checked" mark is fully custom-drawn - clam's
        # indicator element has no option to change the
        # mark shape itself, only its colors, so this uses a small raster
        # image per theme instead of clam's element. Built once per theme
        # and cached (element_create can't be re-called for a name that
        # already exists), then just swapped by name below.
        self._ensure_checkbox_indicator_images(style, dark)
        style.layout("TCheckbutton", [
            ("Checkbutton.padding", {"sticky": "nswe", "children": [
                (f"{'Dark' if dark else 'Light'}.Checkbutton.indicator", {"side": "left", "sticky": ""}),
                ("Checkbutton.focus", {"side": "left", "sticky": "", "children": [
                    ("Checkbutton.label", {"sticky": "nswe"}),
                ]}),
            ]}),
        ])
        style.layout("TRadiobutton", [
            ("Radiobutton.padding", {"sticky": "nswe", "children": [
                ("Uniform.Radiobutton.indicator", {"side": "left", "sticky": ""}),
                ("Radiobutton.focus", {"side": "left", "sticky": "", "children": [
                    ("Radiobutton.label", {"sticky": "nswe"}),
                ]}),
            ]}),
        ])
        if dark:
            indicator_bg, indicator_border = colors["entry_bg"], colors["border"]
        else:
            indicator_bg, indicator_border = LIGHT_INDICATOR_COLORS["indicator_bg"], LIGHT_INDICATOR_COLORS["indicator_border"]
        style.configure(
            "TRadiobutton",
            indicatorbackground=indicator_bg, indicatorforeground="#ffffff",
            upperbordercolor=indicator_border, lowerbordercolor=indicator_border,
        )
        style.map(
            "TRadiobutton",
            indicatorbackground=[("selected", INDICATOR_CHECKED_BG), ("!selected", indicator_bg)],
        )

        # Same cross-theme trick again, for the scrollbar: native's own
        # trough/thumb/arrow elements have zero stylable options too
        # (verified the same way as the checkbutton indicator above), so
        # "copying" light mode's scrollbar into dark really means giving
        # both modes the identical clam-drawn shape and only varying the
        # colors - there's no native chrome left to literally copy pixels
        # from either way.
        for element_name, source_element in (
            ("Uniform.Vertical.Scrollbar.trough", "Vertical.Scrollbar.trough"),
            ("Uniform.Vertical.Scrollbar.thumb", "Vertical.Scrollbar.thumb"),
            ("Uniform.Vertical.Scrollbar.uparrow", "Vertical.Scrollbar.uparrow"),
            ("Uniform.Vertical.Scrollbar.downarrow", "Vertical.Scrollbar.downarrow"),
            ("Uniform.Horizontal.Scrollbar.trough", "Horizontal.Scrollbar.trough"),
            ("Uniform.Horizontal.Scrollbar.thumb", "Horizontal.Scrollbar.thumb"),
            ("Uniform.Horizontal.Scrollbar.leftarrow", "Horizontal.Scrollbar.leftarrow"),
            ("Uniform.Horizontal.Scrollbar.rightarrow", "Horizontal.Scrollbar.rightarrow"),
        ):
            if element_name not in style.element_names():
                style.element_create(element_name, "from", "clam", source_element)
        style.layout("Vertical.TScrollbar", [
            ("Uniform.Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Uniform.Vertical.Scrollbar.uparrow", {"side": "top", "sticky": ""}),
                ("Uniform.Vertical.Scrollbar.downarrow", {"side": "bottom", "sticky": ""}),
                ("Uniform.Vertical.Scrollbar.thumb", {"sticky": "nswe"}),
            ]}),
        ])
        style.layout("Horizontal.TScrollbar", [
            ("Uniform.Horizontal.Scrollbar.trough", {"sticky": "we", "children": [
                ("Uniform.Horizontal.Scrollbar.leftarrow", {"side": "left", "sticky": ""}),
                ("Uniform.Horizontal.Scrollbar.rightarrow", {"side": "right", "sticky": ""}),
                ("Uniform.Horizontal.Scrollbar.thumb", {"sticky": "nswe"}),
            ]}),
        ])
        if dark:
            scrollbar_colors = dict(
                thumb=colors["border"], trough=colors["tree_bg"], arrow=colors["fg"], active_thumb=colors["select_bg"],
            )
        else:
            scrollbar_colors = LIGHT_SCROLLBAR_COLORS
        for scrollbar_style in ("TScrollbar", "Vertical.TScrollbar", "Horizontal.TScrollbar"):
            style.configure(
                scrollbar_style,
                background=scrollbar_colors["thumb"], troughcolor=scrollbar_colors["trough"],
                bordercolor=scrollbar_colors["trough"], arrowcolor=scrollbar_colors["arrow"],
                lightcolor=scrollbar_colors["thumb"], darkcolor=scrollbar_colors["thumb"],
                relief="flat", borderwidth=1,
            )
            style.map(scrollbar_style, background=[("active", scrollbar_colors["active_thumb"])])

        if dark:
            style.configure(".", background=colors["bg"], foreground=colors["fg"])
            style.configure("TFrame", background=colors["bg"])
            style.configure("TLabel", background=colors["bg"], foreground=colors["fg"])
            # No border at all - panels are set apart from the plain window
            # background by color contrast (entry_bg vs bg) rather than a
            # drawn line, per the "no old-Windows bevels" dark mode design.
            style.configure(
                "TLabelframe", background=colors["entry_bg"], foreground=colors["fg"], borderwidth=0,
            )
            style.configure("TLabelframe.Label", background=colors["entry_bg"], foreground=colors["fg"])
            # clam's default dotted focus ring renders as a solid block unless
            # focuscolor is pinned to the background - blend it in instead.
            # Uses entry_bg (the panel color) rather than bg since every
            # checkbutton/radiobutton in this app lives inside a LabelFrame
            # panel, not directly on the plain window background.
            style.configure(
                "TCheckbutton", background=colors["entry_bg"], foreground=colors["fg"], focuscolor=colors["entry_bg"],
            )
            style.configure(
                "TRadiobutton", background=colors["entry_bg"], foreground=colors["fg"], focuscolor=colors["entry_bg"],
            )
            # clam shows a light "active" (hover) background by default -
            # against dark text that's nearly white-on-white. Pin both the
            # background and foreground back to the normal panel colors.
            style.map(
                "TCheckbutton",
                background=[("active", colors["entry_bg"])], foreground=[("active", colors["fg"])],
            )
            style.map(
                "TRadiobutton",
                background=[("active", colors["entry_bg"])], foreground=[("active", colors["fg"])],
            )
            # borderwidth=0 alone doesn't fully suppress the client pane's
            # border - like Treeview.field, Notebook.client still exposes
            # (and clam still draws with) its own bordercolor/lightcolor/
            # darkcolor defaults (light, made for light-mode) unless those
            # are pinned too. That's the actual source of the outline
            # tracing the whole tab content area, not any single control
            # inside it.
            style.configure(
                "TNotebook", background=colors["bg"], borderwidth=0,
                bordercolor=colors["bg"], lightcolor=colors["bg"], darkcolor=colors["bg"],
            )
            style.configure(
                "TNotebook.Tab", background=colors["entry_bg"], foreground=colors["fg"],
                bordercolor=colors["border"],
            )
            # clam's default dotted focus ring on the active tab (e.g.
            # "Tagger") renders as a bright white rectangle - blend it into
            # whichever background is actually showing behind that tab.
            style.map(
                "TNotebook.Tab",
                background=[("selected", colors["bg"])],
                focuscolor=[("selected", colors["bg"]), ("!selected", colors["entry_bg"])],
            )
            # clam draws every control with a 2px raised/sunken bevel
            # (lightcolor/darkcolor highlight+shadow on two opposite edges) -
            # that's the "old Windows" look. Pinning lightcolor/darkcolor to
            # the same flat bordercolor and trimming borderwidth to 1px kills
            # the bevel everywhere without touching layout.
            style.configure(
                "TButton", background=colors["entry_bg"], foreground=colors["fg"], focuscolor=colors["entry_bg"],
                bordercolor=colors["border"], lightcolor=colors["border"], darkcolor=colors["border"],
                borderwidth=1,
                # clam's default vertical button padding is much taller than
                # the native theme's (which ignores this option entirely and
                # uses its own compact OS metrics instead) - trimmed down so
                # switching themes doesn't visibly resize the window.
                padding=(5, 0),
            )
            style.map("TButton", background=[("active", colors["select_bg"])])
            style.configure(
                "TEntry", fieldbackground=colors["entry_bg"], foreground=colors["entry_fg"],
                insertcolor=colors["fg"], padding=(1, 0),
                bordercolor=colors["border"], lightcolor=colors["border"], darkcolor=colors["border"],
                borderwidth=1,
            )
            style.configure("TSeparator", background=colors["border"])
            style.configure(
                "Table.Treeview", background=colors["tree_bg"], fieldbackground=colors["tree_bg"],
                foreground=colors["tree_fg"],
                # clam's "Treeview.field" element has a 1px border baked
                # directly into its layout ('border': '1') - unlike every
                # other bordered control here, that inset isn't gated by a
                # -borderwidth style option at all, so it can't be removed,
                # only recolored (element_options() confirms only
                # bordercolor/lightcolor exist for it, no darkcolor either).
                # Coloring it colors["border"] (a lighter shade, meant to
                # read as a visible line elsewhere) made it stand out as a
                # bright outline against this much darker panel - matching
                # it to the surrounding frame background instead blends the
                # unavoidable 1px away entirely.
                bordercolor=colors["bg"], lightcolor=colors["bg"],
            )
            style.map(
                "Table.Treeview",
                background=[("selected", colors["select_bg"])], foreground=[("selected", colors["select_fg"])],
                # clam's built-in Treeview map brightens bordercolor/lightcolor
                # for the "focus" state (i.e. as soon as the table is clicked)
                # regardless of the plain configure() default above - pin
                # that state too or the blended-away border above reappears
                # the moment the table actually gets used.
                bordercolor=[("focus", colors["bg"])], lightcolor=[("focus", colors["bg"])],
            )
            style.configure(
                "Treeview.Heading", background=colors["tree_heading_bg"], foreground=colors["fg"],
                bordercolor=colors["border"], lightcolor=colors["tree_heading_bg"], darkcolor=colors["tree_heading_bg"],
                relief="flat",
            )
            # clam shows a light "active" (hover) background on column
            # headers by default, same as the checkbutton/radiobutton issue
            # above - pin it back to the normal heading color.
            style.map(
                "Treeview.Heading",
                background=[("active", colors["tree_heading_bg"])], foreground=[("active", colors["fg"])],
            )
            style.map(
                "ReadonlyWhite.TEntry",
                fieldbackground=[("readonly", colors["entry_bg"])],
                foreground=[("readonly", colors["entry_fg"])],
                # Blends any text selection into the field's own colors -
                # these entries are readonly folder-path displays, not real
                # text inputs, so a click-drag selection highlight just
                # looks like a stray visual glitch rather than anything
                # meaningful to select.
                selectbackground=[("readonly", colors["entry_bg"])],
                selectforeground=[("readonly", colors["entry_fg"])],
            )

            self.window.configure(bg=colors["bg"])
            self.journal_text.configure(
                bg=colors["journal_bg"], fg=colors["journal_fg"], insertbackground=colors["journal_fg"],
            )
            for listbox in (self.suggested_listbox, self.mentions_listbox):
                listbox.configure(
                    bg=colors["listbox_bg"], fg=colors["listbox_fg"],
                    selectbackground=colors["select_bg"], selectforeground=colors["select_fg"],
                )
            for canvas in (self.progress_canvas, self.extract_progress_canvas, self.quality_progress_canvas):
                canvas.configure(bg=colors["progress_track"])
                canvas.itemconfig(canvas.progress_rect, fill=colors["progress_fill"])
                canvas.itemconfig(canvas.progress_text, fill=colors["progress_text"])
            self.table.tag_configure("odd_row", background=colors["tree_odd_row"], foreground=colors["tree_fg"])
            self.table.tag_configure("even_row", background=colors["tree_bg"], foreground=colors["tree_fg"])
            self.quality_table.tag_configure(
                "odd_row", background=colors["tree_odd_row"], foreground=colors["tree_fg"],
            )
            self.quality_table.tag_configure("even_row", background=colors["tree_bg"], foreground=colors["tree_fg"])
        else:
            style.map(
                "ReadonlyWhite.TEntry",
                fieldbackground=[("readonly", "white")],
                foreground=[("readonly", "black")],
                selectbackground=[("readonly", "white")],
                selectforeground=[("readonly", "black")],
            )
            style.map(
                "Table.Treeview",
                background=[("selected", LIGHT_TABLE_SELECT_BG)],
                foreground=[("selected", LIGHT_TABLE_SELECT_FG)],
            )
            self.window.configure(bg=self._native_bg)
            self.journal_text.configure(
                bg=self._native_journal_bg, fg=self._native_journal_fg, insertbackground=self._native_journal_fg,
            )
            for listbox in (self.suggested_listbox, self.mentions_listbox):
                listbox.configure(
                    bg=self._native_listbox_bg, fg=self._native_listbox_fg,
                    selectbackground="SystemHighlight", selectforeground="SystemHighlightText",
                )
            for canvas in (self.progress_canvas, self.extract_progress_canvas, self.quality_progress_canvas):
                canvas.configure(bg="#e2e2e2")
                canvas.itemconfig(canvas.progress_rect, fill="#4a90d9")
                canvas.itemconfig(canvas.progress_text, fill="#1a1a1a")
            self.table.tag_configure("odd_row", background="#e9e9e9", foreground="black")
            self.table.tag_configure("even_row", background="white", foreground="black")
            self.quality_table.tag_configure("odd_row", background="#e9e9e9", foreground="black")
            self.quality_table.tag_configure("even_row", background="white", foreground="black")

        for entry in (self.new_mention_entry, self.table_filter_entry):
            entry.normal_color = colors["entry_fg"] if dark else "black"
            if not getattr(entry, "placeholder_active", False):
                entry.configure(foreground=entry.normal_color)

        muted_fg = DARK_MUTED_TEXT_COLOR if dark else MUTED_TEXT_COLOR
        self.dev_credit_label.configure(foreground=muted_fg)
        self.kevz_credit_label.configure(foreground=muted_fg)
        self.legal_text_label.configure(foreground=muted_fg)

        # Matches the table's own background (colors["tree_bg"]/"white" -
        # same literal the "even_row" tag uses for light mode above) so the
        # hint blends into the empty table instead of looking like a
        # separate panel dropped on top of it.
        empty_state_bg = colors["tree_bg"] if dark else "white"
        style.configure("EmptyState.TFrame", background=empty_state_bg)
        style.configure("EmptyState.TLabel", background=empty_state_bg, foreground=muted_fg)

        # Separate PhotoImage per label (not one shared image) - each
        # widget needs its own reference kept alive, and building fresh
        # ones is cheap (a handful of tiny polygon draws).
        self._folder_icon_photo = self._build_folder_icon_photo(dark)
        self.folder_icon_label.configure(image=self._folder_icon_photo)
        self._extract_folder_icon_photo = self._build_folder_icon_photo(dark)
        self.extract_folder_icon_label.configure(image=self._extract_folder_icon_photo)
        self._quality_folder_icon_photo = self._build_folder_icon_photo(dark)
        self.quality_folder_icon_label.configure(image=self._quality_folder_icon_photo)
        self._empty_state_icon_photo = self._build_empty_state_icon_photo(dark)
        self.empty_state_icon_label.configure(image=self._empty_state_icon_photo)
        self._extractor_preview_before_photo, self._extractor_preview_after_photo = (
            self._load_extractor_preview_photos(dark)
        )
        self.extractor_preview_before_label.configure(image=self._extractor_preview_before_photo)
        self.extractor_preview_after_label.configure(image=self._extractor_preview_after_photo)

        self.theme_colors = colors
        self._set_titlebar_dark(self.window, dark)
        # Deliberately NOT re-locking the window height here: even the ~2px
        # difference left between light/dark after the padding tuning above
        # caused a visible jump when the window resized on every toggle.
        # Keeping the geometry frozen (set once at startup) trades a couple
        # of harmless pixels of slack for a switch with zero movement.

    def _set_titlebar_dark(self, window, dark):
        """
        Best-effort: darkens a window's native title bar to match the theme
        (Windows 10 1809+ / 11 only - a no-op on macOS, which already
        darkens its own title bar automatically based on the system
        appearance). Silently does nothing if unsupported - the app is
        still fully usable, just with a light title bar.
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
            value = ctypes.c_int(1 if dark else 0)
            for attribute in (20, 19):  # 20 = current attribute id, 19 = pre-20H1 builds
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
                ) == 0:
                    break
            # DWM doesn't always repaint the title bar immediately after the
            # attribute changes - nudge it with a plain repaint. Deliberately
            # NOT using SetWindowPos(..., SWP_FRAMECHANGED): that's a real
            # resize/reposition call even as a no-op, and Windows' window
            # animations can turn it into a visible "jump". RedrawWindow only
            # asks for a repaint, no size/position semantics at all.
            RDW_INVALIDATE, RDW_UPDATENOW, RDW_FRAME = 0x1, 0x100, 0x400
            ctypes.windll.user32.RedrawWindow(
                hwnd, None, None, RDW_INVALIDATE | RDW_UPDATENOW | RDW_FRAME
            )

            # DWMWA_USE_IMMERSIVE_DARK_MODE only darkens the title bar - the
            # thin 1px frame DWM draws around the whole window (Windows 11
            # 22H2+) stays its default light color unless set separately via
            # DWMWA_BORDER_COLOR. Match it to the dark panel border color, or
            # restore the OS default (DWMWA_COLOR_DEFAULT) in light mode.
            DWMWA_BORDER_COLOR = 34
            if dark:
                r, g, b = (int(DARK_COLORS["border"][i:i + 2], 16) for i in (1, 3, 5))
                border_color = ctypes.c_int((b << 16) | (g << 8) | r)
            else:
                border_color = ctypes.c_int(-1)  # 0xFFFFFFFF as signed 32-bit = DWMWA_COLOR_DEFAULT
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_BORDER_COLOR, ctypes.byref(border_color), ctypes.sizeof(border_color)
            )
            # Unlike the title bar (a GDI-painted element RedrawWindow above
            # already refreshes), the 1px frame stroke is drawn entirely by
            # the DWM compositor and doesn't repaint on its own just because
            # the attribute call succeeded - it needs an actual non-client
            # recalculation to pick up the new color. SWP_FRAMECHANGED with
            # every "don't actually move/resize/refocus" flag set triggers
            # that recalculation without any visible movement.
            SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = (
                0x2, 0x1, 0x4, 0x10, 0x20,
            )
            ctypes.windll.user32.SetWindowPos(
                hwnd, None, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
            )
        except Exception:
            pass

    # --- Update check ---

    def _check_for_update_on_startup(self):
        def _run_check():
            is_newer, latest_version, release_url, installer_url, expected_sha256 = tagger.check_for_update()
            if is_newer:
                self.message_queue.put(
                    ("update_available", (latest_version, release_url, installer_url, expected_sha256))
                )

        self._run_in_background(_run_check)

    def _notify_new_install_on_startup(self):
        """Pings Discord when this Windows account first uses the app, and
        again whenever it's since updated to a newer version - disclosed
        in Settings' legal notice text (see the "View license &
        third-party notices" area), not sent silently with no mention
        anywhere.

        Gated by a saved "last notified version" setting rather than a
        plain done/not-done flag, so an update is noticed too, not just
        the very first install. That setting is only saved AFTER a
        successful send (not optimistically beforehand) - a failed
        attempt (no internet yet at launch, a transient block...) is
        silently retried on the next launch instead of being permanently
        given up on, which is what let a real install slip through
        unnoticed before this fix."""
        last_notified_version = tagger.load_settings().get("last_notified_version")
        if last_notified_version == tagger.APP_VERSION:
            return

        try:
            reporter_name = getpass.getuser()
        except Exception:
            reporter_name = ""

        def _send():
            sent = tagger.send_new_install_notification(
                reporter_name=reporter_name,
                previous_version=last_notified_version,
            )
            if sent:
                tagger.save_setting("last_notified_version", tagger.APP_VERSION)

        self._run_in_background(_send)

    def _notify_scan_complete(
        self, number_new, number_removed, total, number_no_cover=0,
        number_rate_limited_sources=0, auth_error_sources=None, cancelled=False,
        number_itunes=0, number_spotify=0, number_soundcloud=0, number_acoustid_used=0,
    ):
        """Pings Discord once per finished scan (including a scan the user
        cancelled partway through - cancelled=True just relabels the
        embed), so the developer knows when the app is actually being
        used day to day (not just installed) - disclosed alongside the
        "new install" ping in Settings' legal notice text. See
        tagger.DISCORD_NOTIFICATION_EXCLUDED_USERS for any accounts
        excluded from this."""
        try:
            reporter_name = getpass.getuser()
        except Exception:
            reporter_name = ""

        def _send():
            tagger.send_scan_complete_notification(
                reporter_name=reporter_name, number_new=number_new, number_removed=number_removed,
                total=total, number_no_cover=number_no_cover,
                number_rate_limited_sources=number_rate_limited_sources,
                auth_error_sources=auth_error_sources, cancelled=cancelled,
                number_itunes=number_itunes, number_spotify=number_spotify,
                number_soundcloud=number_soundcloud, number_acoustid_used=number_acoustid_used,
            )

        self._run_in_background(_send)

    def _notify_no_cover_report(self, no_cover_infos, total):
        """Automatically sends the full list of no-cover tracks for this
        scan to Discord (as a .txt attachment) when the miss rate crosses
        NO_COVER_REPORT_THRESHOLD - see _finalize_scan."""
        try:
            reporter_name = getpass.getuser()
        except Exception:
            reporter_name = ""

        def _send():
            tagger.send_no_cover_report(no_cover_infos, total, reporter_name=reporter_name)

        self._run_in_background(_send)

    def _notify_rate_limited(self, source):
        """Pings Discord the moment a cover source gets rate-limited
        during a scan - see the *_rate_limited handlers below, each
        already gated to fire at most once per scan per source."""
        try:
            reporter_name = getpass.getuser()
        except Exception:
            reporter_name = ""

        def _send():
            tagger.send_rate_limit_report(source, reporter_name=reporter_name)

        self._run_in_background(_send)

    def _check_internet_connection(self, is_startup_check=False):
        """Checks connectivity in the background, updates the status
        indicator, and (only for the initial startup check) warns once via
        a popup if there's no connection. Re-checks itself every 10s so the
        indicator (and the Tagger tab's buttons - see
        _refresh_tagger_buttons_for_connectivity) stay accurate for the
        life of the run."""
        def _run_check():
            is_online = tagger.check_internet_connection()
            self.message_queue.put(("internet_status", (is_online, is_startup_check)))

        self._run_in_background(_run_check)
        self.window.after(10000, self._check_internet_connection)

    def _check_source_health_on_startup(self):
        """Runs two background checks at most once every 24h (not every
        single launch - these force a fresh SoundCloud/Spotify token
        request each time against a real, limited rate limit, so re-
        running this on every relaunch was pure waste on top of normal
        scanning usage; skipped entirely for Spotify while it's turned
        off, see tagger.check_source_credentials): whether the shared
        SoundCloud/Spotify/AcoustID credentials still authenticate, and
        whether iTunes/Spotify's own domains are reachable at all (a
        restrictive firewall/network filter blocking just those, while
        general internet access still works, would otherwise look
        identical to "nothing found" with no explanation)."""
        last_checked = tagger.load_settings().get("last_source_health_check", 0)
        if time.time() - last_checked < 24 * 60 * 60:
            return
        tagger.save_setting("last_source_health_check", time.time())

        def _run_check():
            broken_credentials = tagger.check_source_credentials(log=self._append_to_journal)
            is_online = tagger.check_internet_connection()
            blocked_sources = tagger.check_restrictive_firewall() if is_online else []
            self.message_queue.put(("source_health_checked", (broken_credentials, blocked_sources)))

        self._run_in_background(_run_check)

    def _refresh_tagger_buttons_for_connectivity(self):
        """Scan/Apply/Reset need internet for cover search, so they're
        disabled while offline - Browse is exempt (still useful to pick a
        folder while offline). Skipped entirely while a scan/apply run is
        active: browse_button is only ever disabled by _set_buttons_enabled
        for that, never by connectivity, so its state doubles as "is
        something already running" without a separate flag to track."""
        if str(self.browse_button.cget("state")) == "disabled":
            return
        if not self._is_online:
            self.scan_button.configure(state="disabled")
            self.reset_button.configure(state="disabled")
            self.apply_button.configure(state="disabled")
            return
        if self.folder_variable.get().strip():
            self.scan_button.configure(state="normal")
            self.reset_button.configure(state="normal")
            self.apply_button.configure(state="normal")

    def _check_for_update_manual(self):
        """Same check as on startup, but always reports back (up to date /
        failed / available) since the user explicitly asked for it here."""
        self.check_update_button.configure(state="disabled", text="Checking...")

        def _run_check():
            result = tagger.check_for_update()
            self.message_queue.put(("manual_update_check_result", result))

        self._run_in_background(_run_check)

    def _offer_update(self, latest_version, release_url, installer_url, expected_sha256=None):
        """Shared by the silent startup check and the manual button - both
        end up here once they've decided a newer version really exists."""
        if not installer_url:
            # No .exe asset on the release - fall back to the old flow.
            open_page = messagebox.askyesno(
                "Update available",
                f"A new version ({latest_version}) of Track-Tidy is available "
                f"(you have {tagger.APP_VERSION}).\n\nOpen the download page?",
                parent=self.window,
            )
            if open_page:
                webbrowser.open(release_url)
            return

        update_now = messagebox.askyesno(
            "Update available",
            f"A new version ({latest_version}) of Track-Tidy is available "
            f"(you have {tagger.APP_VERSION}).\n\n"
            "Download and install it now? Track Tidy will close to finish the update.",
            parent=self.window,
        )
        if update_now:
            self._start_in_app_update(installer_url, latest_version, expected_sha256)

    def _start_in_app_update(self, installer_url, latest_version, expected_sha256=None):
        """Downloads the installer straight into the app (with a progress
        bar) and launches it once done, instead of sending the user to a
        browser to fetch it manually."""
        dialog = tk.Toplevel(self.window)
        self._style_toplevel(dialog)
        dialog.title("Updating...")
        dialog.resizable(False, False)
        dialog.transient(self.window)
        dialog.grab_set()
        dialog.protocol("WM_DELETE_WINDOW", lambda: None)  # can't close mid-download

        ttk.Label(dialog, text=f"Downloading Track Tidy {latest_version}...", padding=(20, 15, 20, 5)).pack()
        self._update_progress_var = tk.DoubleVar(value=0)
        ttk.Progressbar(
            dialog, variable=self._update_progress_var, maximum=100, length=320
        ).pack(padx=20, pady=(0, 10))
        self._update_percent_label = ttk.Label(dialog, text="0%")
        self._update_percent_label.pack(pady=(0, 15))

        self._update_dialog = dialog
        self._center_dialog(dialog)

        def on_progress(downloaded, total):
            self.message_queue.put(("update_download_progress", (downloaded, total)))

        def _run():
            # Extension follows the actual asset (.exe on Windows, .dmg on
            # macOS - see check_for_update) rather than being assumed, so
            # this doesn't silently mislabel the downloaded file.
            extension = os.path.splitext(installer_url)[1] or (".dmg" if sys.platform == "darwin" else ".exe")
            # latest_version comes straight from the GitHub release's own
            # tag_name (see tagger.check_for_update) - sanitized the same
            # way a track's own filename is (tagger.sanitize_filename())
            # before it becomes part of a path, so a malformed/hostile tag
            # (e.g. containing "/" or "..") can't land the download outside
            # the intended temp directory.
            safe_version = tagger.sanitize_filename(latest_version)
            dest_path = os.path.join(tempfile.gettempdir(), f"Track-Tidy-Setup-{safe_version}{extension}")
            success = tagger.download_installer(
                installer_url, dest_path, on_progress=on_progress,
                expected_sha256=expected_sha256, log=self._append_to_journal,
            )
            self.message_queue.put(("update_download_done", (success, dest_path)))

        self._run_in_background(_run)

    def _finish_in_app_update(self, success, dest_path):
        dialog = getattr(self, "_update_dialog", None)
        if dialog is not None and dialog.winfo_exists():
            dialog.destroy()
        self._update_dialog = None

        if not success:
            messagebox.showerror(
                "Update failed",
                "Could not download the update - check your internet connection and try again.",
                parent=self.window,
            )
            return

        try:
            # On Windows this runs the installer directly (same as
            # double-clicking it). On macOS a .dmg isn't a silent
            # installer - "open" mounts it in Finder, the normal macOS
            # flow, and the user drags the app to Applications themselves.
            open_with_default_app(dest_path)
        except Exception as error:
            messagebox.showerror("Update failed", f"Could not launch the installer: {error}", parent=self.window)
            return

        tagger.log_action(f"Update installer launched: '{os.path.basename(dest_path)}'")
        self.window.destroy()

    # --- Startup checks ---

    # --- Drag and drop ---

    def _setup_drag_and_drop(self):
        """Lets the user drag audio files (or a folder) onto the window to set
        the Parent folder and scan automatically. Silently does nothing if
        tkinterdnd2 isn't installed."""
        try:
            from tkinterdnd2 import DND_FILES
        except ImportError:
            return

        # The Notebook (tabs) covers the entire window, so registering only
        # the root window wouldn't actually catch drops - register it AND the
        # tab content too, to cover the whole visible surface. The empty-
        # state hint's own widgets are included too - while it's showing,
        # it's placed ON TOP of self.table, so it would otherwise catch the
        # drop event itself and the table's registration would never fire.
        for widget in (self.window, self.notebook, self.tagger_tab, self.table, *self.empty_state_widgets):
            try:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_files_dropped)
            except Exception:
                pass  # not fatal - the app works fine without drag-and-drop

    def _on_files_dropped(self, event):
        raw_paths = self.window.tk.splitlist(event.data)
        if not raw_paths:
            return

        paths = [os.path.normpath(p.strip("{}")) for p in raw_paths]

        if os.path.isdir(paths[0]):
            self._start_dropped_folder_scan(paths[0])
        else:
            self._start_multi_file_scan(paths)

    def _start_dropped_folder_scan(self, folder):
        """Drop of a folder: scans it fully, WITHOUT touching the visible
        'Parent folder' field (which only ever reflects an explicit Browse...)."""
        if not os.path.isdir(folder):
            return

        tagger.MUSIC_FOLDER = folder
        all_files = tagger.list_audio_files()
        capped_files = self._apply_track_count_limit(all_files)
        truncated = len(capped_files) < len(all_files)

        self._sync_mentions_to_remove()
        self._reset_scan_run_state()

        if folder != getattr(self, "last_scanned_folder", None):
            for row in self.table.get_children():
                self.table.delete(row)
            self.tk_images.clear()
            self.tk_images_hover.clear()
            self._thumbnail_pil_images.clear()
            self.scanned_plan = []
            self.last_scanned_folder = folder
            self._update_empty_state_hint()

        self.notebook.select(0)
        self._set_buttons_enabled(False)
        self._launch_scan_after_already_scanned_check(explicit_files=capped_files if truncated else None)

    def _start_multi_file_scan(self, file_paths):
        """Drop of one or more individual audio files: tags just those
        files, without scanning everything else that happens to sit in
        the same folder. All dropped files are expected to share ONE
        parent folder (the normal case - multi-selecting tracks in one
        Explorer window and dragging them together), since tagger.
        MUSIC_FOLDER/scanned_plan's relative paths only support a single
        folder at a time - one dropped from a different folder than the
        first valid file is skipped (logged, not silently mixed in or
        left to collide with a same-named file)."""
        valid_paths = [
            path for path in file_paths
            if os.path.isfile(path) and path.lower().endswith(tagger.SUPPORTED_EXTENSIONS)
        ]
        if not valid_paths:
            return

        folder = os.path.dirname(valid_paths[0])
        relative_names = []
        for path in valid_paths:
            if os.path.dirname(path) != folder:
                self._append_to_journal(
                    f"Ignored '{os.path.basename(path)}' - dropped from a different folder than the rest."
                )
                continue
            if (
                not tagger.AUTO_CONVERT_MP3
                and not path.lower().endswith((".mp3", ".wav", ".aiff", ".aif", ".flac"))
            ):
                self._append_to_journal(
                    f"Ignored '{os.path.basename(path)}' - only MP3/WAV/AIFF/FLAC can be tagged "
                    "without converting (Settings > Convert everything to MP3)."
                )
                continue
            relative_name = os.path.basename(path)
            if any(info["file"] == relative_name for info in self.scanned_plan):
                continue  # already in the table
            relative_names.append(relative_name)

        if not relative_names:
            return

        relative_names = self._apply_track_count_limit(relative_names)

        tagger.MUSIC_FOLDER = folder
        self.last_scanned_folder = folder
        self._reset_scan_run_state()

        self.notebook.select(0)
        self._set_buttons_enabled(False)
        self._show_scan_progress_bar()
        self._run_in_background(self._run_scan, relative_names)

    # --- UI construction ---

    def _build_interface(self):
        self.notebook = ttk.Notebook(self.window)
        self.notebook.pack(fill="both", expand=True)

        version_label = ttk.Label(self.window, text=tagger.APP_VERSION, foreground="#999999")
        version_label.place(relx=1.0, rely=1.0, x=-6, y=-4, anchor="se")

        tagger_tab = ttk.Frame(self.notebook)
        extractor_tab = ttk.Frame(self.notebook)
        quality_tab = ttk.Frame(self.notebook)
        soundcloud_tab = ttk.Frame(self.notebook)
        self.tagger_tab = tagger_tab
        self.notebook.add(tagger_tab, text="Tagger")
        self.notebook.add(extractor_tab, text="Extractor")
        self.notebook.add(quality_tab, text="Quality")
        self.notebook.add(soundcloud_tab, text="Settings")
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # ============================== Tagger tab ==============================

        # Same header pattern as Extractor/Quality below: a short one-line
        # description plus a "ⓘ" (see quality_info_icon) for the more
        # cautionary detail that doesn't need to be visible at all times.
        tagger_header_frame = ttk.Frame(tagger_tab)
        tagger_header_frame.pack(fill="x", padx=10, pady=(10, 10))

        tagger_info_icon = ttk.Label(
            tagger_header_frame, text=" ⓘ", foreground="#1a73e8", cursor="hand2",
        )
        tagger_info_icon.pack(side="right", anchor="n")
        tagger_info_text = (
            "Automated matching isn't perfect - review the suggested Artist/Title "
            "in the table before clicking Apply, especially any low-confidence or "
            "\U0001f3a7 AcoustID-identified rows."
        )
        tagger_info_icon.bind("<Enter>", lambda e: self._show_tooltip(tagger_info_text, e))
        tagger_info_icon.bind("<Leave>", lambda e: self._hide_tooltip())

        tagger_intro_label = ttk.Label(
            tagger_header_frame,
            text="Matches tracks in a folder against online catalogs to fill in missing cover art, artist, and title tags.",
            justify="left",
        )
        tagger_intro_label.pack(side="left", fill="x", expand=True)
        tagger_intro_label.bind("<Configure>", lambda e: e.widget.configure(wraplength=e.width))

        # --- Folder selection ---
        folder_frame = ttk.LabelFrame(tagger_tab, text="Parent folder:")
        folder_frame.pack(fill="x", padx=10, pady=(0, 2))

        self.folder_variable = tk.StringVar(value=os.path.abspath(tagger.MUSIC_FOLDER) if tagger.MUSIC_FOLDER else "")

        folder_entry_row = ttk.Frame(folder_frame)
        folder_entry_row.pack(fill="x", padx=10, pady=(10, 5))

        # Image is set in _apply_theme (needs to know light/dark, and is
        # rebuilt every time the theme changes, same as the checkbox
        # indicator images).
        self.folder_icon_label = ttk.Label(folder_entry_row)
        self.folder_icon_label.pack(side="left", padx=(0, 6))

        # "ReadonlyWhite.TEntry"'s actual colors are (re)configured by _apply_theme,
        # since they depend on the light/dark choice and ttk styles are per-theme.
        folder_entry = ttk.Entry(
            folder_entry_row, textvariable=self.folder_variable, state="readonly", style="ReadonlyWhite.TEntry"
        )
        folder_entry.pack(side="left", fill="x", expand=True)
        self._bind_entry_context_menu(folder_entry, readonly=True, on_paste_folder=self._apply_picked_folder)

        folder_buttons_frame = ttk.Frame(folder_frame)
        folder_buttons_frame.pack(fill="x", padx=10, pady=(0, 5))
        self.browse_button = ttk.Button(folder_buttons_frame, text="Browse...", command=self._choose_folder)
        self.browse_button.pack(side="left", fill="x", expand=True, padx=(0, 5))

        self.scan_button = ttk.Button(folder_buttons_frame, text="Scan", command=self._start_scan)
        self.scan_button.configure(state="disabled")
        self.scan_button.pack(side="left", fill="x", expand=True, padx=(0, 5))

        self.reset_button = ttk.Button(folder_buttons_frame, text="Reset", command=self._reset_app)
        self.reset_button.configure(state="disabled")
        self.reset_button.pack(side="left", fill="x", expand=True)

        # (scan progress is now shown directly in the Scan button's own text)

        # --- Advanced section (collapsible): mentions to remove ---
        self.advanced_section_visible = False

        self.advanced_toggle = ttk.Label(tagger_tab, text="▸ ⚙️", cursor="hand2", foreground="#1a73e8")
        self.advanced_toggle.pack(anchor="w", padx=10, pady=(0, 2))
        self.advanced_toggle.bind("<Button-1>", lambda event: self._toggle_advanced_section())

        # Plain Frame, not LabelFrame - an empty-title LabelFrame still
        # reserves space for its (invisible) label line at the top, which
        # was most of the excess gap above "Suggested"/"To remove".
        self.advanced_frame = ttk.Frame(tagger_tab)
        # not shown by default (pack() is called/undone in _toggle_advanced_section)

        columns_frame = ttk.Frame(self.advanced_frame)
        columns_frame.pack(fill="x", padx=10, pady=(2, 5))

        suggested_column = ttk.Frame(columns_frame)
        suggested_column.pack(side="left", fill="both", expand=True, padx=(0, 5))
        ttk.Label(suggested_column, text="Suggested").pack(anchor="w")
        self.suggested_listbox = tk.Listbox(suggested_column, height=4)
        self.suggested_listbox.pack(fill="both", expand=True)
        self.suggested_listbox.bind("<Double-1>", self._promote_suggested_mention)

        to_remove_column = ttk.Frame(columns_frame)
        to_remove_column.pack(side="left", fill="both", expand=True, padx=(5, 0))
        ttk.Label(to_remove_column, text="To remove").pack(anchor="w")
        self.mentions_listbox = tk.Listbox(to_remove_column, height=4)
        self.mentions_listbox.pack(fill="both", expand=True)
        self.mentions_listbox.bind("<Double-1>", self._demote_removed_mention)
        for mention in tagger.MENTIONS_TO_REMOVE:
            self.mentions_listbox.insert("end", mention)

        self.new_mention_entry = ttk.Entry(self.advanced_frame)
        self.new_mention_entry.pack(fill="x", padx=10, pady=(5, 10))
        self.new_mention_entry.bind("<Return>", self._add_mention)
        self._bind_entry_context_menu(self.new_mention_entry)
        setup_placeholder(self.new_mention_entry, "Add a word or phrase to remove...")

        self.no_cover_filter_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self.advanced_frame, text="Only show tracks with no cover match",
            variable=self.no_cover_filter_var, command=self._apply_table_filter,
        ).pack(anchor="w", padx=10, pady=(0, 6))

        # --- Scan results table ---
        # Plain Frame, not LabelFrame - same reasoning as advanced_frame
        # above, this was most of the excess gap above the table headers.
        table_frame = ttk.Frame(tagger_tab)
        table_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        search_frame = ttk.Frame(table_frame)
        search_frame.pack(fill="x", padx=10, pady=(0, 10), side="bottom")
        self.table_filter_entry = ttk.Entry(search_frame)
        self.table_filter_entry.pack(fill="x", expand=True)
        self.table_filter_entry.bind("<KeyRelease>", self._schedule_table_filter)
        setup_placeholder(self.table_filter_entry, "Search tracks...", on_change=self._apply_table_filter)
        self._bind_entry_context_menu(self.table_filter_entry)

        scrollbars_frame = ttk.Frame(table_frame)
        scrollbars_frame.pack(fill="both", expand=True, padx=(10, 0), pady=(2, 10))

        # show="tree headings": the native "#0" column (far left) shows ONLY the cover
        self.table = ttk.Treeview(
            scrollbars_frame, columns=COLUMNS, show="tree headings", height=round(8 * self.window_scale),
        )
        self.table.heading("#0", text="Cover")
        self.table.column("#0", width=72, minwidth=72, anchor="center", stretch=False)

        self.all_checked_state = True
        self.all_convert_state = True
        self.sort_state = {"column": None, "state": 0}

        headers = {
            "apply": CHECKED_BOX,
            "title": "Title",
            "artist": "Artist",
            "format": CHECKED_BOX,
        }
        widths = {
            "apply": 34, "title": 210, "artist": 160, "format": 55,
        }

        style = ttk.Style()
        # An explicit font (even one that just matches TkDefaultFont) keeps
        # cell glyph rendering aligned with the native column header - left
        # unset, Windows silently falls back to the color emoji font for
        # certain characters (e.g. the checkbox glyphs below) in table
        # cells but not in the header, so the same glyph looks different.
        style.configure(
            "Table.Treeview", rowheight=TABLE_ROW_HEIGHT,
            font=(self._table_font.actual("family"), self._table_font.actual("size")),
        )
        self.table.configure(style="Table.Treeview")

        for col in COLUMNS:
            self.table.heading(col, text=headers[col])
            anchor = "center" if col in ("apply", "format") else "w"
            expandable = col in ("title", "artist")  # share the remaining space
            self.table.column(col, width=widths[col], anchor=anchor, stretch=expandable)

        self.table.heading("apply", command=self._toggle_all)
        self.table.heading("title", command=lambda: self._sort_by("title"))
        self.table.heading("artist", command=lambda: self._sort_by("artist"))
        self.table.heading("format", command=self._toggle_all_convert)

        # Alternating rows (every other one greyed out) for readability.
        # Foreground is set explicitly here too (not just background) -
        # otherwise ttk/clam can fall back to a default (dim/grey) text
        # color instead of inheriting the theme's real foreground.
        self.table.tag_configure("odd_row", background="#e9e9e9", foreground="black")
        self.table.tag_configure("even_row", background="white", foreground="black")

        vertical_scrollbar = ttk.Scrollbar(scrollbars_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=vertical_scrollbar.set)

        self.table.pack(side="left", fill="both", expand=True)
        vertical_scrollbar.pack(side="left", fill="y")

        # Empty-state hint: placed (not packed) so it floats centered over
        # the table area regardless of the scrollbar's own packing, and
        # created after the table/scrollbar so its default stacking order
        # already puts it on top of both. Shown/hidden by
        # _update_empty_state_hint() - see its call sites (initial setup
        # below, _add_scan_row, and every point that clears the table back
        # to zero rows).
        self.empty_state_frame = ttk.Frame(scrollbars_frame, style="EmptyState.TFrame")
        self.empty_state_widgets = [self.empty_state_frame]
        # Image is set in _apply_theme (needs to know light/dark), same as
        # folder_icon_label above.
        self.empty_state_icon_label = ttk.Label(self.empty_state_frame, style="EmptyState.TLabel")
        self.empty_state_icon_label.pack(pady=(0, 12))
        self.empty_state_widgets.append(self.empty_state_icon_label)
        empty_state_text_label = ttk.Label(
            self.empty_state_frame, style="EmptyState.TLabel", justify="center",
            text="Drag and drop an audio file here,\nor select a folder above to get started",
        )
        empty_state_text_label.pack()
        self.empty_state_widgets.append(empty_state_text_label)
        self._update_empty_state_hint()

        self.table.bind("<Button-1>", self._toggle_cell, add="+")
        self.table.bind("<Button-1>", self._on_row_drag_start, add="+")
        self.table.bind("<B1-Motion>", self._on_row_drag_motion, add="+")
        self.table.bind("<ButtonRelease-1>", self._on_row_drag_release, add="+")
        self.table.bind("<Double-1>", self._toggle_cell_double_click, add="+")
        self.table.bind("<Triple-1>", self._toggle_cell_triple_click, add="+")
        self.table.bind("<Button-3>", self._show_context_menu)
        self.table.bind("<Delete>", self._delete_selected_rows)
        self.table.bind("<Control-a>", self._select_all_rows)
        self.table.bind("<Control-z>", self._undo_last_action)
        self.table.bind("<Motion>", self._on_table_hover, add="+")
        self.table.bind("<Leave>", self._on_table_leave, add="+")

        # --- Journal section (collapsible, shown/hidden via Settings -> "Show log section") ---
        self.journal_section_visible = False

        self.journal_toggle = ttk.Label(tagger_tab, text="▸ Log", cursor="hand2", foreground="#1a73e8")
        if self.show_log_var.get():
            self.journal_toggle.pack(anchor="w", padx=10, pady=(0, 5))
        self.journal_toggle.bind("<Button-1>", lambda event: self._toggle_journal_section())

        self.journal_frame = ttk.LabelFrame(tagger_tab, text="Log")
        # not shown by default (pack() is called/undone in _toggle_journal_section)

        self.journal_text = scrolledtext.ScrolledText(self.journal_frame, state="disabled", wrap="word", height=6)
        self.journal_text.pack(fill="both", expand=True, padx=5, pady=5)

        # --- Launch + progress ---
        self.launch_frame = launch_frame = ttk.Frame(tagger_tab)
        # Extra bottom margin - the "vX.Y" version label sits fixed at the
        # window's bottom-right corner and would otherwise overlap the
        # Apply button below it.
        launch_frame.pack(fill="x", padx=10, pady=(0, 22))

        apply_row = ttk.Frame(launch_frame)
        apply_row.pack(fill="x", pady=(0, 5))

        self.apply_status_label = ttk.Label(apply_row, text="0 tracks selected")
        self.apply_status_label.pack(side="left")

        self.apply_button = ttk.Button(apply_row, text="Apply", command=self._start_processing, width=18)
        self.apply_button.configure(state="disabled")
        self.apply_button.pack(side="right")

        # not packed yet: only shown once a run has actually started (see _start_processing)
        self.progress_canvas = self._build_progress_canvas(launch_frame)

        # ============================== Extractor tab ==============================

        extractor_header_frame = ttk.Frame(extractor_tab)
        extractor_header_frame.pack(fill="x", padx=10, pady=(10, 10))

        extractor_info_icon = ttk.Label(
            extractor_header_frame, text=" ⓘ", foreground="#1a73e8", cursor="hand2",
        )
        extractor_info_icon.pack(side="right", anchor="n")
        extractor_info_text = (
            "Works on every common audio format (MP3, WAV, FLAC, AAC, M4A, OGG, "
            "WMA...). Files already directly inside the folder are left alone."
        )
        extractor_info_icon.bind("<Enter>", lambda e: self._show_tooltip(extractor_info_text, e))
        extractor_info_icon.bind("<Leave>", lambda e: self._hide_tooltip())

        extractor_intro_label = ttk.Label(
            extractor_header_frame,
            text=(
                "Flattens a messy music folder: audio files buried in nested "
                "subfolders move straight into the folder below, and any "
                "subfolders left empty are cleaned up automatically."
            ),
            justify="left",
        )
        extractor_intro_label.pack(side="left", fill="x", expand=True)
        # Wraps to the label's own actual width instead of a fixed guess, so
        # it uses the full available width up to the right edge (like the
        # left-aligned text already does), not just whatever a hardcoded
        # wraplength happened to allow.
        extractor_intro_label.bind("<Configure>", lambda e: e.widget.configure(wraplength=e.width))

        # Same "Parent folder:" LabelFrame + icon + entry-row structure as
        # the Tagger tab's own folder picker (folder_frame above) - was
        # previously a bare Label + ungrouped Entry/buttons here.
        extract_folder_frame = ttk.LabelFrame(extractor_tab, text="Folder to flatten:")
        extract_folder_frame.pack(fill="x", padx=10, pady=(0, 2))

        self.extract_folder_var = tk.StringVar(value="")

        extract_entry_row = ttk.Frame(extract_folder_frame)
        extract_entry_row.pack(fill="x", padx=10, pady=(10, 5))

        # Image is set in _apply_theme, same as folder_icon_label above.
        self.extract_folder_icon_label = ttk.Label(extract_entry_row)
        self.extract_folder_icon_label.pack(side="left", padx=(0, 6))

        extract_folder_entry = ttk.Entry(
            extract_entry_row, textvariable=self.extract_folder_var, state="readonly", style="ReadonlyWhite.TEntry"
        )
        extract_folder_entry.pack(side="left", fill="x", expand=True)
        self._bind_entry_context_menu(extract_folder_entry, readonly=True, on_paste_folder=self._apply_picked_folder)

        extract_buttons_frame = ttk.Frame(extract_folder_frame)
        extract_buttons_frame.pack(fill="x", padx=10, pady=(0, 5))
        self.extract_browse_button = ttk.Button(
            extract_buttons_frame, text="Browse...", command=self._choose_extract_folder
        )
        self.extract_browse_button.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.extract_button = ttk.Button(
            extract_buttons_frame, text="Extract", command=self._start_extraction
        )
        self.extract_button.configure(state="disabled")
        self.extract_button.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.extract_reset_button = ttk.Button(
            extract_buttons_frame, text="Reset", command=self._reset_extract,
        )
        self.extract_reset_button.configure(state="disabled")
        self.extract_reset_button.pack(side="left", fill="x", expand=True)

        # Before/after preview - shows what "Extract" actually does at a
        # glance, since the intro label's text alone wasn't landing (users
        # kept asking what this tab was for). Real screenshots, cropped
        # tight to just a few rows of the file list (folders with their own
        # subfolder vs. that same handful of tracks flattened), background
        # keyed out to transparent so it floats on the tab instead of
        # showing as a dark box in light mode - which needs light/dark
        # variants of the same crop (the light one has its near-white text
        # inverted to dark, see _build_extractor_preview_photo), swapped in
        # by _apply_theme like every other theme-aware icon in this file.
        self.extractor_preview_frame = ttk.Frame(extractor_tab)
        self.extractor_preview_frame.pack(pady=(25, 15))
        self.extractor_preview_before_label = ttk.Label(self.extractor_preview_frame)
        self.extractor_preview_before_label.pack(side="left")
        self.extractor_preview_arrow_label = ttk.Label(
            self.extractor_preview_frame, text="  →  ", font=("Segoe UI", 36, "bold")
        )
        self.extractor_preview_arrow_label.pack(side="left")
        self.extractor_preview_after_label = ttk.Label(self.extractor_preview_frame)
        self.extractor_preview_after_label.pack(side="left")
        # Images are set in _apply_theme, same as folder_icon_label above.

        # not packed yet: only shown once an extraction has actually started
        self.extract_progress_canvas = self._build_progress_canvas(extractor_tab)

        # ============================== Quality tab ==============================

        quality_header_frame = ttk.Frame(quality_tab)
        quality_header_frame.pack(fill="x", padx=10, pady=(10, 10))

        # "ⓘ" = circled "i" - matches the "▸" toggle labels' blue/
        # hand2 clickable look used elsewhere (advanced_toggle, journal_toggle)
        # instead of introducing a new affordance style just for this tab.
        quality_info_icon = ttk.Label(
            quality_header_frame, text=" ⓘ", foreground="#1a73e8", cursor="hand2",
        )
        quality_info_icon.pack(side="right", anchor="n")
        quality_info_text = (
            "Best-effort ESTIMATE, not a certainty. A real track can legitimately "
            "roll off high frequencies (mastering, genre), and some lossy sources "
            "don't show a detectable trace at all - treat orange/red as \"worth a "
            "listen\", not proof."
        )
        quality_info_icon.bind("<Enter>", lambda e: self._show_tooltip(quality_info_text, e))
        quality_info_icon.bind("<Leave>", lambda e: self._hide_tooltip())

        quality_intro_label = ttk.Label(
            quality_header_frame,
            text="Flags tracks whose real audio doesn't match their declared format/bitrate.",
            justify="left",
        )
        quality_intro_label.pack(side="left", fill="x", expand=True)
        quality_intro_label.bind("<Configure>", lambda e: e.widget.configure(wraplength=e.width))

        # Same LabelFrame + icon + entry-row structure as Tagger's own
        # folder_frame (and now Extractor's extract_folder_frame above).
        quality_folder_frame = ttk.LabelFrame(quality_tab, text="Folder to analyze:")
        quality_folder_frame.pack(fill="x", padx=10, pady=(0, 2))

        self.quality_folder_var = tk.StringVar(value="")

        quality_entry_row = ttk.Frame(quality_folder_frame)
        quality_entry_row.pack(fill="x", padx=10, pady=(10, 5))

        # Image is set in _apply_theme, same as folder_icon_label above.
        self.quality_folder_icon_label = ttk.Label(quality_entry_row)
        self.quality_folder_icon_label.pack(side="left", padx=(0, 6))

        quality_folder_entry = ttk.Entry(
            quality_entry_row, textvariable=self.quality_folder_var, state="readonly",
            style="ReadonlyWhite.TEntry",
        )
        quality_folder_entry.pack(side="left", fill="x", expand=True)
        self._bind_entry_context_menu(quality_folder_entry, readonly=True, on_paste_folder=self._apply_picked_folder)

        quality_buttons_frame = ttk.Frame(quality_folder_frame)
        quality_buttons_frame.pack(fill="x", padx=10, pady=(0, 5))
        self.quality_browse_button = ttk.Button(
            quality_buttons_frame, text="Browse...", command=self._choose_quality_folder
        )
        self.quality_browse_button.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.quality_scan_button = ttk.Button(
            quality_buttons_frame, text="Scan", command=self._start_quality_scan
        )
        self.quality_scan_button.configure(state="disabled")
        self.quality_scan_button.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.quality_reset_button = ttk.Button(
            quality_buttons_frame, text="Reset", command=self._reset_quality,
        )
        self.quality_reset_button.configure(state="disabled")
        self.quality_reset_button.pack(side="left", fill="x", expand=True)

        # not packed yet - only shown once a scan has actually started.
        # Same Canvas-based animated bar as Tagger/Extractor, not a plain
        # ttk.Progressbar - was previously the odd one out here, with no
        # dark-mode theming of its own.
        self.quality_progress_canvas = self._build_progress_canvas(quality_tab)

        # Colored at-a-glance counts, shown once a scan has produced results -
        # summarizing many rows as three numbers reads faster than scanning
        # the table itself. Same flat-UI green/red already used for the
        # Online/Offline status label, plus a matching orange for "worth
        # checking" (self.internet_status_label's foreground colors).
        self.quality_summary_frame = ttk.Frame(quality_tab)
        # not packed yet - only shown once results exist
        self.quality_summary_green_var = tk.StringVar(value="")
        self.quality_summary_orange_var = tk.StringVar(value="")
        self.quality_summary_red_var = tk.StringVar(value="")
        for var, color, padx in (
            (self.quality_summary_green_var, "#2ecc71", (10, 14)),
            (self.quality_summary_orange_var, "#e67e22", (0, 14)),
            (self.quality_summary_red_var, "#e74c3c", (0, 14)),
        ):
            ttk.Label(
                self.quality_summary_frame, textvariable=var, foreground=color,
                font=("TkDefaultFont", 9, "bold"),
            ).pack(side="left", padx=padx)

        self.quality_table_frame = ttk.Frame(quality_tab)
        self.quality_table_frame.pack(fill="both", expand=True, padx=10, pady=(5, 10))

        # "#0" (the tree column) is always the leftmost column in a ttk
        # Treeview, so it's repurposed to hold just the verdict dot. A plain
        # "●" colored via a tag's foreground, not a colored circle emoji
        # (\U0001f7e2 etc.) - Tk on Windows doesn't render multi-color emoji
        # glyphs, it falls back to a flat gray outline, which is why an
        # earlier version of this looked gray regardless of verdict.
        quality_columns = ("file", "level", "format", "bitrate")
        self.quality_table = ttk.Treeview(
            self.quality_table_frame, columns=quality_columns, show="tree headings",
            selectmode="browse", style="Table.Treeview", height=round(10 * self.window_scale),
        )
        # Clicking the dot column's heading cycles through sorting by
        # verdict severity: worst-first, then best-first, then back to
        # scan/arrival order - see _on_quality_verdict_heading_click. The
        # gray dot marks the column as such (kept alive via self. - a
        # PhotoImage with no surviving reference gets garbage-collected
        # and silently vanishes from the widget).
        self._quality_verdict_heading_photo = self._build_gray_dot_photo()
        self.quality_table.heading(
            "#0", text="", image=self._quality_verdict_heading_photo,
            command=self._on_quality_verdict_heading_click,
        )
        self.quality_table.column("#0", width=36, minwidth=36, stretch=False, anchor="center")
        self.quality_table.heading("file", text="File")
        # Stretches to soak up all leftover width, which keeps "bitrate" -
        # now the last column - pinned flush against the table's right edge
        # instead of leaving blank space after it. Base width kept modest
        # (unlike the other, fixed-width columns here, ttk never shrinks a
        # stretching column below its configured width to make room for
        # its neighbors - only grows it - so a too-wide base width here
        # would push "bitrate" off the edge of the app's fixed 620px-wide
        # window instead of actually stretching).
        self.quality_table.column("file", width=170, minwidth=100, stretch=True, anchor="w")
        # Unit isn't repeated in every cell (just "-13", not "-13 LUFS"/
        # "320 kbps") - the header already says it, and it keeps these
        # three columns tight instead of each carrying its own padding.
        self.quality_table.heading("level", text="LUFS")
        self.quality_table.column("level", width=42, minwidth=42, stretch=False, anchor="center")
        self.quality_table.heading("format", text="Format")
        self.quality_table.column("format", width=46, minwidth=46, stretch=False, anchor="center")
        # "kbps" instead of "Bitrate" - shorter header, and matches the
        # LUFS/kbps pattern of the unit living in the header, not the cell.
        self.quality_table.heading("bitrate", text="kbps")
        self.quality_table.column("bitrate", width=42, minwidth=42, stretch=False, anchor="center")
        # Colors the whole row (dot + file/format/detail text) - ttk Treeview
        # tags apply per-item, not per-cell, so there's no way to color only
        # the dot on its own; matches what was asked for anyway ("les lignes
        # aussi"). Independent of light/dark theming (unlike odd_row/
        # even_row) since flat-UI green/orange/red read fine on both.
        self.quality_table.tag_configure("verdict_green", foreground="#2ecc71")
        self.quality_table.tag_configure("verdict_orange", foreground="#e67e22")
        self.quality_table.tag_configure("verdict_red", foreground="#e74c3c")
        self.quality_table.bind("<Double-1>", self._on_quality_row_double_click)
        self.quality_table.bind("<Button-3>", self._show_quality_context_menu)

        quality_scrollbar = ttk.Scrollbar(
            self.quality_table_frame, orient="vertical", command=self.quality_table.yview,
        )
        self.quality_table.configure(yscrollcommand=quality_scrollbar.set)
        self.quality_table.pack(side="left", fill="both", expand=True)
        quality_scrollbar.pack(side="left", fill="y")

        # Empty-state hint: explains what the dot color means before a scan
        # has produced any rows to show it on directly - placed (not
        # packed) so it floats centered over the table regardless of the
        # scrollbar's own packing, same technique as the Tagger tab's own
        # empty_state_frame. Shown/hidden by _update_quality_empty_state_hint()
        # - see its call sites (initial setup below, _start_quality_scan,
        # and _add_quality_row).
        self.quality_empty_state_frame = ttk.Frame(self.quality_table_frame, style="EmptyState.TFrame")
        self.quality_empty_state_widgets = [self.quality_empty_state_frame]
        for color, text in (
            ("#2ecc71", "●  Level and encoding look fine"),
            ("#e67e22", "●  Worth a listen - quiet track, or a borderline cutoff"),
            ("#e74c3c", "●  Likely re-encoded from a lossy source, or very quiet"),
        ):
            line_label = ttk.Label(
                self.quality_empty_state_frame, style="EmptyState.TLabel",
                foreground=color, justify="center", text=text,
            )
            line_label.pack()
            self.quality_empty_state_widgets.append(line_label)
        self._update_quality_empty_state_hint()

        # ============================== Settings tab ==============================

        appearance_frame = ttk.LabelFrame(soundcloud_tab, text="Appearance")
        appearance_frame.pack(fill="x", padx=10, pady=(15, 10))
        self._theme_radio_buttons = {}
        for value, label in (("auto", "Automatic (time of day)"), ("light", "Light"), ("dark", "Dark")):
            theme_radio = ttk.Radiobutton(
                appearance_frame, text=label, value=value, variable=self.theme_var,
                command=self._on_theme_changed,
            )
            theme_radio.pack(side="left", padx=10, pady=10)
            self._theme_radio_buttons[value] = theme_radio

        # Renamed from "Behavior" - now holds only actual file-handling
        # toggles, not one-off maintenance actions (those moved to "App").
        behavior_frame = ttk.LabelFrame(soundcloud_tab, text="File handling")
        behavior_frame.pack(fill="x", padx=10, pady=(0, 10))
        self.auto_convert_checkbox = ttk.Checkbutton(
            behavior_frame, text="Convert everything to MP3 (320 kbps)", variable=self.auto_convert_var,
            command=self._on_auto_convert_changed,
        )
        self.auto_convert_checkbox.pack(anchor="w", padx=10, pady=(10, 0))
        self.auto_convert_wav_aiff_checkbox = ttk.Checkbutton(
            behavior_frame, text="Convert WAV to AIFF (needed for cover art in Rekordbox)",
            variable=self.auto_convert_wav_aiff_var, command=self._on_auto_convert_wav_aiff_changed,
        )
        self.auto_convert_wav_aiff_checkbox.pack(anchor="w", padx=10, pady=(0, 0))
        ttk.Checkbutton(
            behavior_frame, text="Fix track file name (renaming can break Rekordbox's link to the file)",
            variable=self.fix_track_file_name_var,
            command=self._on_fix_track_file_name_changed,
        ).pack(anchor="w", padx=10, pady=(0, 0))
        ttk.Checkbutton(
            behavior_frame, text="Use Spotify as a cover source", variable=self.use_spotify_var,
            command=self._on_use_spotify_changed,
        ).pack(anchor="w", padx=10, pady=(0, 0))
        ttk.Checkbutton(
            behavior_frame, text="Show log section", variable=self.show_log_var,
            command=self._on_show_log_changed,
        ).pack(anchor="w", padx=10, pady=(0, 10))

        # Maintenance actions, clearly grouped as their own section instead
        # of floating unframed below "Behavior" (where they looked like
        # they were part of it, even though they're one-off actions, not
        # settings).
        app_frame = ttk.LabelFrame(soundcloud_tab, text="App")
        app_frame.pack(fill="x", padx=10, pady=(0, 10))
        self.check_update_button = ttk.Button(
            app_frame, text="Check for updates", command=self._check_for_update_manual,
        )
        self.check_update_button.pack(fill="x", padx=10, pady=(10, 5))
        ttk.Button(
            app_frame, text="View processing history", command=self._show_history_window,
        ).pack(fill="x", padx=10, pady=(0, 5))
        ttk.Button(
            app_frame, text="Reset all settings to default", command=self._reset_settings_to_default,
        ).pack(fill="x", padx=10, pady=(0, 10))

        self.internet_status_label = ttk.Label(
            soundcloud_tab, text="● Checking connection...", foreground="#999999",
        )
        self.internet_status_label.pack(anchor="w", padx=10, pady=(0, 10))

        self.legal_text_label = legal_text_label = ttk.Label(
            soundcloud_tab,
            text=(
                "Track Tidy is an independent, personal tool and is not affiliated with, "
                "endorsed by, or sponsored by SoundCloud, Apple, or any other third-party "
                "service it connects to. All trademarks are the property of their "
                "respective owners.\n"
                "Licensed under the GNU General Public License v2 or later - includes "
                "mutagen (GPL-2.0-or-later) and FFmpeg (GPLv3).\n"
                "Track Tidy notifies the developer (via Discord) of your Windows username, "
                "OS, and basic counts - no track/file names - on install, and after each "
                "scan, extraction, or quality analysis (including a cancelled one), as well "
                "as on a crash (with an error traceback). The in-app \"Report track\" button "
                "additionally sends that one track's info when you press it - see "
                "PRIVACY.md in the repo for the full breakdown."
            ),
            justify="left",
            foreground=MUTED_TEXT_COLOR,
            font=("TkDefaultFont", 8),
        )
        legal_text_label.pack(anchor="w", fill="x", padx=10, pady=(0, 2), side="bottom")
        # See the Extractor tab's intro label above for why this is dynamic
        # rather than a fixed wraplength.
        legal_text_label.bind("<Configure>", lambda e: e.widget.configure(wraplength=e.width))

        self.legal_notices_link = ttk.Label(
            soundcloud_tab, text="View license & third-party notices",
            foreground="#1a73e8", cursor="hand2", font=("TkDefaultFont", 8),
        )
        self.legal_notices_link.pack(anchor="w", padx=10, pady=(0, 6), side="bottom")
        self.legal_notices_link.bind("<Button-1>", self._open_legal_notices)

        credit_frame = ttk.Frame(soundcloud_tab)
        credit_frame.pack(anchor="w", padx=10, pady=(0, 2), side="bottom")
        # Colored explicitly by _apply_theme (MUTED_TEXT_COLOR/DARK_COLORS'
        # "muted_fg") rather than left at this light-mode default forever -
        # #888888 is borderline-low contrast against the dark background,
        # unlike virtually every other color in the app, which IS
        # reassigned on every theme switch.
        self.dev_credit_label = ttk.Label(
            credit_frame, text="Developed by ", foreground=MUTED_TEXT_COLOR, font=("TkDefaultFont", 8, "bold"),
            padding=0,
        )
        self.dev_credit_label.pack(side="left")
        self.kevz_credit_label = ttk.Label(
            credit_frame, text="KEVZ", foreground=MUTED_TEXT_COLOR, font=("TkDefaultFont", 8, "bold"), cursor="hand2",
            padding=0,
        )
        self.kevz_credit_label.pack(side="left")
        self.kevz_credit_label.bind("<Button-1>", self._open_kevz_instagram)

        ttk.Separator(soundcloud_tab, orient="horizontal").pack(fill="x", padx=10, pady=(20, 10), side="bottom")

        # Captured now (native theme, before any dark styling is ever applied)
        # so "light" mode can restore these exact values later.
        self._native_bg = self.window.cget("bg")
        self._native_journal_bg = self.journal_text.cget("bg")
        self._native_journal_fg = self.journal_text.cget("fg")
        self._native_listbox_bg = self.suggested_listbox.cget("bg")
        self._native_listbox_fg = self.suggested_listbox.cget("fg")

    # --- Window / progress bar helpers ---

    def _adjust_window_height(self):
        """Recomputes the needed window height based on the currently visible sections."""
        self.window.update_idletasks()
        height = self.window.winfo_reqheight()
        self.window.geometry(f"{WINDOW_WIDTH}x{height}")

    def _build_progress_canvas(self, parent):
        """Builds one of the app's Canvas-based animated progress bars -
        Tagger's is the original/reference; Extractor and Quality each get
        their own independent instance of the exact same widget via this
        (not shared - the three tabs' runs are all independent and could
        in principle animate at the same time). Returns the Canvas, not
        packed yet - only shown once a run actually starts, same as
        Tagger's own self.progress_canvas."""
        canvas = tk.Canvas(parent, height=24, bg="#e2e2e2", highlightthickness=0)
        canvas.progress_rect = canvas.create_rectangle(0, 0, 0, 24, fill="#4a90d9", width=0)
        canvas.progress_text = canvas.create_text(
            0, 12, text="", fill="#1a1a1a", font=("TkDefaultFont", 9, "bold")
        )
        canvas.progress_target_fraction = 0
        canvas.progress_current_fraction = 0
        canvas.progress_animating = False
        return canvas

    def _update_progress_bar(self, canvas, fraction, text):
        """Redraws the progress bar text immediately, and glides the fill
        rectangle toward the new fraction instead of jumping straight to
        it - a per-file scan/apply update every ~1s otherwise looked like
        the bar was snapping in discrete jumps rather than filling
        smoothly. A reset to 0 (start of a new run) snaps instantly
        instead of animating backwards, which would look like the bar
        emptying itself out before the next run even starts.

        The glide state (target/current fraction, whether it's mid-
        animation) lives on the canvas itself, not on self - each of the
        app's progress bars (Tagger/Extractor/Quality) is independent."""
        canvas.update_idletasks()
        width = canvas.winfo_width() or (WINDOW_WIDTH - 40)
        height = 24
        canvas.coords(canvas.progress_text, width / 2, height / 2)
        canvas.itemconfigure(canvas.progress_text, text=text)

        canvas.progress_target_fraction = fraction
        if fraction == 0:
            canvas.progress_current_fraction = 0
            canvas.coords(canvas.progress_rect, 0, 0, 0, height)
            canvas.progress_animating = False
            return

        if not canvas.progress_animating:
            canvas.progress_animating = True
            self._glide_progress_bar(canvas)

    PROGRESS_GLIDE_STEP_MS = 16
    PROGRESS_GLIDE_EASE = 0.35  # fraction of the remaining gap closed per step

    def _glide_progress_bar(self, canvas):
        width = canvas.winfo_width() or (WINDOW_WIDTH - 40)
        height = 24
        current = canvas.progress_current_fraction
        target = canvas.progress_target_fraction
        if abs(target - current) < 0.002:
            current = target
            canvas.progress_animating = False
        else:
            current += (target - current) * self.PROGRESS_GLIDE_EASE

        canvas.progress_current_fraction = current
        canvas.coords(canvas.progress_rect, 0, 0, width * current, height)

        if canvas.progress_animating:
            self.window.after(self.PROGRESS_GLIDE_STEP_MS, lambda: self._glide_progress_bar(canvas))

    def _toggle_advanced_section(self):
        if self._is_run_active():
            return  # locked during a scan/apply run, same as the other action buttons
        self.advanced_section_visible = not self.advanced_section_visible
        if self.advanced_section_visible:
            self.advanced_frame.pack(fill="x", padx=10, pady=(0, 10), after=self.advanced_toggle)
            self.advanced_toggle.configure(text="▾ ⚙️")
        else:
            self.advanced_frame.pack_forget()
            self.advanced_toggle.configure(text="▸ ⚙️")
        self._adjust_window_height()

    def _toggle_journal_section(self):
        self.journal_section_visible = not self.journal_section_visible
        if self.journal_section_visible:
            self.journal_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10), after=self.journal_toggle)
            self.journal_toggle.configure(text="▾ Log")
        else:
            self.journal_frame.pack_forget()
            self.journal_toggle.configure(text="▸ Log")
        self._adjust_window_height()

    # --- Settings tab actions ---

    def _open_legal_notices(self, event=None):
        """Opens THIRD-PARTY-NOTICES.md, installed next to the app - only
        present from v0.10 onward (see installer.iss), so this falls back
        to a plain explanation on older installs rather than erroring."""
        path = os.path.join(tagger.app_base_dir(), "THIRD-PARTY-NOTICES.md")
        if not os.path.exists(path):
            messagebox.showinfo(
                "Not available in this version",
                "THIRD-PARTY-NOTICES.md isn't bundled with this version of Track Tidy - "
                "it's included starting with the next update.",
                parent=self.window,
            )
            return
        try:
            open_with_default_app(path)
        except Exception as error:
            self._append_to_journal(f"Could not open license notices: {error}")

    def _open_kevz_instagram(self, event=None):
        webbrowser.open("https://www.instagram.com/kevz_fr/")

    # --- Extractor tab actions ---

    def _choose_extract_folder(self):
        folder = filedialog.askdirectory(title="Choose the folder to flatten")
        if folder:
            self._sync_all_folder_pickers(folder)

    def _start_extraction(self):
        folder = self.extract_folder_var.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("Missing folder", "Please choose a valid folder first.", parent=self.window)
            return

        self.extract_browse_button.configure(state="disabled")
        self.extract_reset_button.configure(state="disabled")
        self.extract_cancel_requested.clear()
        self.extract_button.configure(text="Cancel", command=self._request_extract_cancel, state="normal")
        # Locks every other tab (Tagger/Quality/Settings) so no other
        # button in the app is reachable while an extraction is running -
        # same pattern as Tagger's own scan/apply run (_set_buttons_enabled).
        self._set_tabs_locked(True)

        if not self.extract_progress_canvas.winfo_ismapped():
            self.extract_progress_canvas.pack(fill="x", padx=10, pady=(0, 10))
        self._update_progress_bar(self.extract_progress_canvas, 0, "0 %")

        self._run_in_background(self._run_extraction, folder)

    def _request_extract_cancel(self):
        self.extract_cancel_requested.set()
        self._append_to_journal("Extraction cancellation requested — stopping after the current folder...")
        self.extract_button.configure(state="disabled")

    def _run_extraction(self, folder):
        try:
            reporter_name = getpass.getuser()
        except Exception:
            reporter_name = ""

        def on_progress(index, total):
            self.message_queue.put(("extract_progress", (index, total)))

        try:
            moved_count = tagger.extract_audio_files(
                folder, log=self._append_to_journal, on_progress=on_progress,
                should_cancel=self.extract_cancel_requested.is_set,
            )
            removed_count = tagger.remove_empty_subfolders(
                folder, log=self._append_to_journal, should_cancel=self.extract_cancel_requested.is_set,
            )
            cancelled = self.extract_cancel_requested.is_set()
            tagger.send_extraction_report(
                reporter_name=reporter_name, moved_count=moved_count, removed_count=removed_count,
                cancelled=cancelled,
            )
            self.message_queue.put(("extract_done", (folder, moved_count, removed_count, cancelled, None)))
        except Exception as error:
            tagger.send_extraction_report(reporter_name=reporter_name, error=str(error))
            self.message_queue.put(("extract_done", (folder, 0, 0, False, str(error))))

    # --- Quality tab actions ---

    # Plain "●" per verdict, colored via the matching Treeview tag - see the
    # tag_configure comment in _build_interface for why not a colored emoji.
    QUALITY_VERDICT_TAG = {
        tagger.QUALITY_GREEN: "verdict_green",
        tagger.QUALITY_ORANGE: "verdict_orange",
        tagger.QUALITY_RED: "verdict_red",
    }

    def _choose_quality_folder(self):
        folder = filedialog.askdirectory(title="Choose the folder to analyze")
        if folder:
            self._sync_all_folder_pickers(folder)

    def _start_quality_scan(self):
        folder = self.quality_folder_var.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("Missing folder", "Please choose a valid folder first.", parent=self.window)
            return

        for row in self.quality_table.get_children():
            self.quality_table.delete(row)
        self._update_quality_empty_state_hint()
        self.quality_summary_frame.pack_forget()
        self.quality_last_scanned_folder = folder
        self.quality_row_paths = {}
        self._quality_scan_counts = {tagger.QUALITY_GREEN: 0, tagger.QUALITY_ORANGE: 0, tagger.QUALITY_RED: 0}
        self._pending_quality_reveals = []  # results queued for _reveal_next_quality_row
        self._pending_quality_scan_done = None  # held until reveals catch up
        self._quality_verdict_sort_state = 0  # a fresh scan invalidates any prior sort
        self._quality_default_row_order = None
        self._quality_scan_total = 0  # set once the first "quality_scan_progress" message arrives

        self.quality_browse_button.configure(state="disabled")
        self.quality_reset_button.configure(state="disabled")
        self.quality_cancel_requested.clear()
        self.quality_scan_button.configure(text="Cancel", command=self._request_quality_cancel, state="normal")
        # Locks every other tab (Tagger/Extractor/Settings) so no other
        # button in the app is reachable while a quality scan is running -
        # same pattern as Tagger's own scan/apply run (_set_buttons_enabled).
        self._set_tabs_locked(True)

        if not self.quality_progress_canvas.winfo_ismapped():
            self.quality_progress_canvas.pack(fill="x", padx=10, pady=(0, 5), before=self.quality_table_frame)
        self._update_progress_bar(self.quality_progress_canvas, 0, "0 %")

        self._run_in_background(self._run_quality_scan, folder)

    def _request_quality_cancel(self):
        self.quality_cancel_requested.set()
        self.quality_scan_button.configure(state="disabled")

    def _run_quality_scan(self, folder):
        try:
            reporter_name = getpass.getuser()
        except Exception:
            reporter_name = ""

        def on_progress(index, total):
            self.message_queue.put(("quality_scan_progress", (index, total)))

        def on_result(result):
            # Streamed one at a time as each file finishes analyzing
            # (rather than only at the very end), so the table fills in
            # live the same way the Tagger tab's own scan does.
            self.message_queue.put(("quality_scan_row", result))

        try:
            results = tagger.analyze_folder_quality(
                folder, log=self._append_to_journal, on_progress=on_progress, on_result=on_result,
                should_cancel=self.quality_cancel_requested.is_set,
            )
            cancelled = self.quality_cancel_requested.is_set()
            tagger.send_quality_scan_report(
                reporter_name=reporter_name, total=len(results),
                number_green=sum(1 for r in results if r.get("verdict") == tagger.QUALITY_GREEN),
                number_orange=sum(1 for r in results if r.get("verdict") == tagger.QUALITY_ORANGE),
                number_red=sum(1 for r in results if r.get("verdict") == tagger.QUALITY_RED),
                cancelled=cancelled,
            )
            self.message_queue.put(("quality_scan_done", (results, cancelled, None)))
        except Exception as error:
            tagger.send_quality_scan_report(reporter_name=reporter_name, error=str(error))
            self.message_queue.put(("quality_scan_done", ([], False, str(error))))

    def _add_quality_row(self, result):
        """Inserts and flashes a single scan result as soon as it arrives -
        counterpart to _run_quality_scan's on_result callback. Running
        counts/the summary strip update live here too, rather than being
        computed once at the end."""
        verdict = result.get("verdict")
        verdict_tag = self.QUALITY_VERDICT_TAG.get(verdict)
        if verdict in self._quality_scan_counts:
            self._quality_scan_counts[verdict] += 1
        # Row is inserted at index 0 (above the previous ones), so the stripe
        # tag can't be based on the current child count the way a plain
        # end-appended table would - it's assigned by _restripe_rows() below
        # instead, same as the Tagger table does for the same reason.
        final_tags = ("even_row", verdict_tag) if verdict_tag else ("even_row",)
        bitrate_kbps = result.get("bitrate_kbps")
        lufs = result.get("lufs")
        item_id = self.quality_table.insert(
            "", 0, text="●" if verdict_tag else "❓",
            values=(
                result.get("file", ""),
                f"{lufs:.1f}" if lufs is not None else "—",
                result.get("format", ""),
                f"{bitrate_kbps:.0f}" if bitrate_kbps is not None else "—",
            ),
            tags=final_tags,
        )
        relative_file = result.get("file", "")
        if self.quality_last_scanned_folder and relative_file:
            self.quality_row_paths[item_id] = os.path.join(self.quality_last_scanned_folder, relative_file)
        self._restripe_rows(tree=self.quality_table)
        self._flash_new_row(item_id, tree=self.quality_table, final_tags=final_tags)
        self._update_quality_empty_state_hint()

        # Progress bar tracks what's actually on screen, not how far the
        # background analysis has gotten - see the "quality_scan_progress"
        # message handler for why.
        if self._quality_scan_total:
            fraction = len(self.quality_table.get_children()) / self._quality_scan_total
            self._update_progress_bar(self.quality_progress_canvas, fraction, f"{round(fraction * 100)} %")

    # Verdict rank per sort state: state 1 puts red on top (worst-first),
    # state 2 puts green on top (best-first); a row with no recognized
    # verdict tag (the "❓" rows) always sorts last in either direction.
    _QUALITY_SORT_RANKS = {
        1: {"verdict_red": 0, "verdict_orange": 1, "verdict_green": 2},
        2: {"verdict_red": 2, "verdict_orange": 1, "verdict_green": 0},
    }

    def _on_quality_verdict_heading_click(self):
        """Cycles the dot column through: 1st click = worst (red) on top,
        2nd click = best (green) on top, 3rd click = back to the original
        scan/arrival order - state tracked in _quality_verdict_sort_state,
        reset on every new scan since it no longer means anything once the
        rows themselves are gone."""
        children = self.quality_table.get_children("")
        if not children:
            return
        if self._quality_default_row_order is None:
            self._quality_default_row_order = list(children)

        self._quality_verdict_sort_state = (self._quality_verdict_sort_state + 1) % 3

        if self._quality_verdict_sort_state == 0:
            ordered = [iid for iid in self._quality_default_row_order if self.quality_table.exists(iid)]
        else:
            rank_map = self._QUALITY_SORT_RANKS[self._quality_verdict_sort_state]

            def sort_key(iid):
                tags = self.quality_table.item(iid, "tags")
                for tag in tags:
                    if tag in rank_map:
                        return rank_map[tag]
                return 3  # no recognized verdict tag - always last

            ordered = sorted(children, key=sort_key)

        for index, iid in enumerate(ordered):
            self.quality_table.move(iid, "", index)
        self._restripe_rows(tree=self.quality_table)

        self.quality_summary_green_var.set(f"● {self._quality_scan_counts[tagger.QUALITY_GREEN]}")
        self.quality_summary_orange_var.set(f"● {self._quality_scan_counts[tagger.QUALITY_ORANGE]}")
        self.quality_summary_red_var.set(f"● {self._quality_scan_counts[tagger.QUALITY_RED]}")
        if not self.quality_summary_frame.winfo_ismapped():
            self.quality_summary_frame.pack(fill="x", padx=10, pady=(0, 5), before=self.quality_table_frame)

    def _reveal_next_quality_row(self):
        """Ticks every SCAN_REVEAL_INTERVAL_MS, for the app's entire
        lifetime - same pacing mechanism as the Tagger tab's own
        _reveal_next_scan_row(), reused here rather than reinvented: pops
        at most one buffered quality result (see the "quality_scan_row"
        handler in _start_message_loop) so tracks visibly appear no faster
        than 1/second, no matter how fast the background analysis itself
        produces them. Only finalizes the scan (see _finalize_quality_scan)
        once every buffered result has actually been revealed.

        Cancellation bypasses the pacing entirely, same as the Tagger
        version - once Cancel is clicked, whatever's left in the buffer is
        flushed in one go instead of continuing to trickle out."""
        if self.quality_cancel_requested.is_set():
            while self._pending_quality_reveals:
                self._add_quality_row(self._pending_quality_reveals.pop(0))
        elif self._pending_quality_reveals:
            self._add_quality_row(self._pending_quality_reveals.pop(0))

        if not self._pending_quality_reveals and self._pending_quality_scan_done is not None:
            content, self._pending_quality_scan_done = self._pending_quality_scan_done, None
            self._finalize_quality_scan(content)

        self.window.after(SCAN_REVEAL_INTERVAL_MS, self._reveal_next_quality_row)

    def _finalize_quality_scan(self, content):
        results, cancelled, error = content
        self.quality_browse_button.configure(state="normal")
        self.quality_scan_button.configure(
            text="Scan", command=self._start_quality_scan, state="normal",
        )
        self.quality_reset_button.configure(state="normal")
        self.quality_progress_canvas.pack_forget()
        self._set_tabs_locked(False)

        if error:
            messagebox.showerror("Analysis error", error, parent=self.window)
        elif cancelled:
            self._append_to_journal(f"Quality analysis cancelled - {len(results)} track(s) analyzed so far.")

    def _on_quality_row_double_click(self, event):
        item_id = self.quality_table.identify_row(event.y)
        if not item_id:
            return
        file_path = self.quality_row_paths.get(item_id)
        if not file_path or not os.path.isfile(file_path):
            messagebox.showinfo(
                "Spectrogram unavailable",
                "This result's file isn't available anymore - run a new scan first.",
                parent=self.window,
            )
            return
        self._show_quality_spectrogram_dialog(file_path)

    def _show_quality_context_menu(self, event):
        """Right-click on a Quality row - mirrors the Tagger table's own
        context menu (_show_context_menu), just with the one action that
        actually applies here."""
        item_id = self.quality_table.identify_row(event.y)
        if not item_id:
            return
        self.quality_table.selection_set(item_id)

        file_path = self.quality_row_paths.get(item_id)
        menu = self._make_themed_menu(self.window)
        menu.add_command(
            label="Open file location",
            command=lambda: self._open_quality_file_location(file_path),
            state="normal" if file_path and os.path.isfile(file_path) else "disabled",
        )
        menu.tk_popup(event.x_root, event.y_root)

    def _open_quality_file_location(self, file_path):
        if not file_path or not os.path.isfile(file_path):
            self._append_to_journal(f"Can't open location, file not found: '{file_path}'")
            return
        try:
            reveal_in_file_manager(file_path)
        except Exception as error:
            self._append_to_journal(f"Error opening file location: {error}")

    # Canvas geometry for the spectrogram dialog - a bit larger than the
    # single-curve chart it replaced, to fit a real plot plus a dB color
    # legend bar (like a dedicated spectrogram viewer, e.g. Spek).
    QUALITY_SPECTROGRAM_CANVAS_W = 780
    QUALITY_SPECTROGRAM_CANVAS_H = 420
    QUALITY_SPECTROGRAM_MARGIN = (55, 95, 10, 30)  # left, right, top, bottom
    QUALITY_SPECTROGRAM_LEGEND_W = 18
    QUALITY_SPECTROGRAM_LEGEND_GAP = 20

    def _show_quality_spectrogram_dialog(self, file_path):
        dialog = tk.Toplevel(self.window)
        self._style_toplevel(dialog)
        dialog.title(f"Spectrogram - {os.path.basename(file_path)}")
        dialog.resizable(False, False)
        dialog.transient(self.window)

        muted_color = DARK_MUTED_TEXT_COLOR if self.theme_colors else MUTED_TEXT_COLOR
        ttk.Label(dialog, text=file_path, font=("TkDefaultFont", 8)).pack(
            anchor="w", padx=15, pady=(12, 0),
        )
        ttk.Label(
            dialog, text=tagger.describe_audio_stream(file_path), foreground=muted_color,
            font=("TkDefaultFont", 8),
        ).pack(anchor="w", padx=15, pady=(0, 8))

        canvas_bg = self.theme_colors["tree_bg"] if self.theme_colors else "white"
        canvas = tk.Canvas(
            dialog, width=self.QUALITY_SPECTROGRAM_CANVAS_W, height=self.QUALITY_SPECTROGRAM_CANVAS_H,
            highlightthickness=0, bg=canvas_bg,
        )
        canvas.pack(padx=15, pady=(0, 5))
        status_label = ttk.Label(dialog, text="Analyzing spectrogram...")
        status_label.pack(pady=(0, 5))
        ttk.Button(dialog, text="Close", command=dialog.destroy).pack(pady=(0, 15))

        self._center_dialog(dialog)

        request_id = str(id(dialog))
        self._quality_spectrogram_requests[request_id] = (dialog, canvas, status_label)
        self._run_in_background(self._compute_quality_spectrogram, file_path, request_id)

    def _compute_quality_spectrogram(self, file_path, request_id):
        try:
            data = tagger.compute_track_spectrogram(file_path, log=self._append_to_journal)
            self.message_queue.put(("quality_spectrogram_ready", (request_id, data, None)))
        except Exception as error:
            self.message_queue.put(("quality_spectrogram_ready", (request_id, None, str(error))))

    @staticmethod
    def _format_mmss(seconds):
        seconds = max(0, int(round(seconds)))
        minutes, secs = divmod(seconds, 60)
        return f"{minutes}:{secs:02d}"

    def _draw_quality_spectrogram(self, canvas, data):
        """Renders tagger.compute_track_spectrogram()'s colormapped image
        (time across, frequency up, color = dB) scaled up to fill the plot
        area, with a dB color legend, real axis ticks/labels (frequency in
        kHz, time in mm:ss), and a dashed marker at the detected cutoff (if
        any) - the same cutoff analyze_track_quality() bases its verdict
        on, made visible instead of just described in the Detail text."""
        canvas.delete("all")
        margin_left, margin_right, margin_top, margin_bottom = self.QUALITY_SPECTROGRAM_MARGIN
        plot_w = self.QUALITY_SPECTROGRAM_CANVAS_W - margin_left - margin_right
        plot_h = self.QUALITY_SPECTROGRAM_CANVAS_H - margin_top - margin_bottom

        image = data.get("image")
        duration = data.get("duration_seconds") or 0
        max_freq = data.get("max_freq_hz") or 1
        min_db = data.get("min_db", -120.0)
        max_db = data.get("max_db", 0.0)
        cutoff = data.get("cutoff_hz")
        if image is None or duration <= 0:
            return

        resized = image.resize((plot_w, plot_h), Image.LANCZOS)
        photo = ImageTk.PhotoImage(resized)
        legend_image = ImageTk.PhotoImage(
            tagger.spectrogram_legend_image(self.QUALITY_SPECTROGRAM_LEGEND_W, plot_h)
        )
        # Tk drops a PhotoImage as soon as nothing in Python still
        # references it, even though the Canvas item keeps pointing at it -
        # stash both on the canvas itself so they survive as long as the
        # dialog does.
        canvas.spectrogram_photo_refs = (photo, legend_image)
        canvas.create_image(margin_left, margin_top, anchor="nw", image=photo)

        is_dark = self.theme_colors is not None
        axis_color = self.theme_colors["border"] if is_dark else "#999999"
        text_color = self.theme_colors["tree_fg"] if is_dark else "#333333"
        cutoff_color = "#ff5555"

        canvas.create_rectangle(
            margin_left, margin_top, margin_left + plot_w, margin_top + plot_h, outline=axis_color,
        )

        # Frequency (Y) ticks - every 5kHz, top of the plot = max_freq (the
        # file's own Nyquist frequency), bottom = 0Hz.
        freq_tick = 5000
        freq = 0
        while freq <= max_freq:
            y = margin_top + plot_h * (1 - freq / max_freq)
            canvas.create_line(margin_left - 4, y, margin_left, y, fill=axis_color)
            canvas.create_text(
                margin_left - 7, y, text=f"{freq // 1000}k", fill=text_color,
                font=("TkDefaultFont", 8), anchor="e",
            )
            freq += freq_tick

        # Time (X) ticks, mm:ss - pick a step that keeps the tick count
        # readable regardless of how long the track turns out to be.
        time_tick = 600
        for step in (5, 10, 15, 30, 60, 120, 300, 600):
            if duration / step <= 8:
                time_tick = step
                break
        t = 0
        while t <= duration:
            x = margin_left + plot_w * (t / duration)
            canvas.create_line(x, margin_top + plot_h, x, margin_top + plot_h + 4, fill=axis_color)
            canvas.create_text(
                x, margin_top + plot_h + 14, text=self._format_mmss(t), fill=text_color,
                font=("TkDefaultFont", 8),
            )
            t += time_tick

        if cutoff is not None and cutoff <= max_freq:
            y = margin_top + plot_h * (1 - cutoff / max_freq)
            canvas.create_line(margin_left, y, margin_left + plot_w, y, fill=cutoff_color, dash=(4, 2))
            canvas.create_text(
                margin_left + plot_w - 4, y - 3, text=f"{cutoff / 1000:.1f} kHz cutoff", fill=cutoff_color,
                font=("TkDefaultFont", 8, "bold"), anchor="se",
            )

        # dB color legend - a vertical gradient bar plus tick labels, same
        # fixed scale and colormap the plot itself uses.
        legend_x = margin_left + plot_w + self.QUALITY_SPECTROGRAM_LEGEND_GAP
        canvas.create_image(legend_x, margin_top, anchor="nw", image=legend_image)
        canvas.create_rectangle(
            legend_x, margin_top, legend_x + self.QUALITY_SPECTROGRAM_LEGEND_W, margin_top + plot_h,
            outline=axis_color,
        )
        db_tick = 20
        db = max_db
        while db >= min_db:
            y = margin_top + plot_h * ((max_db - db) / (max_db - min_db))
            canvas.create_line(
                legend_x + self.QUALITY_SPECTROGRAM_LEGEND_W, y,
                legend_x + self.QUALITY_SPECTROGRAM_LEGEND_W + 4, y, fill=axis_color,
            )
            canvas.create_text(
                legend_x + self.QUALITY_SPECTROGRAM_LEGEND_W + 7, y, text=f"{db:.0f} dB", fill=text_color,
                font=("TkDefaultFont", 8), anchor="w",
            )
            db -= db_tick

    # --- Folder / mention actions ---

    def _choose_folder(self):
        """Windows' native folder-browser dialog (which askdirectory uses)
        never shows files, only folders - a Tk/Windows limitation, not
        something this app can turn on. As the next best thing, log how
        many audio files are actually in the chosen folder right away,
        instead of only finding out once Scan is clicked."""
        folder = filedialog.askdirectory(title="Choose the audio files folder")
        if folder:
            self._apply_picked_folder(folder)

    def _apply_picked_folder(self, folder):
        """Shared by every way of picking a folder - Browse... on the
        Tagger tab, and pasting a path into any of the three tabs' folder
        fields (see _bind_entry_context_menu's on_paste_folder) - so
        pasting gets the same "here's how many audio files are in there"
        feedback as browsing does."""
        self._sync_all_folder_pickers(folder)
        file_count = len(tagger.list_audio_files())
        unit = "audio file" if file_count == 1 else "audio files"
        self._append_to_journal(f"Selected folder contains {file_count} {unit}.")

    def _sync_all_folder_pickers(self, folder):
        """Tagger/Extractor/Quality's folder pickers are all linked -
        picking a folder in any one of them updates the other two and
        persists it as the app's remembered music folder, since in
        practice all three are always meant to point at the same folder."""
        self.folder_variable.set(folder)
        self.extract_folder_var.set(folder)
        self.quality_folder_var.set(folder)
        self._refresh_tagger_buttons_for_connectivity()
        self.extract_button.configure(state="normal")
        self.extract_reset_button.configure(state="normal")
        self.quality_scan_button.configure(state="normal")
        self.quality_reset_button.configure(state="normal")

        tagger.MUSIC_FOLDER = folder
        tagger.save_setting("music_folder", folder)

    def _add_mention(self, event=None):
        """Manual entries go straight into the 'To remove' list (press Enter to confirm)."""
        if getattr(self.new_mention_entry, "placeholder_active", False):
            return
        text = self.new_mention_entry.get().strip()
        if text:
            self.mentions_listbox.insert("end", text)
            self.new_mention_entry.delete(0, "end")
            self._refresh_all_detected_titles()

    @staticmethod
    def _strip_count_suffix(display_text):
        """Removes the ' - N' occurrence counter suffix, to get the raw mention text back."""
        return re.sub(r"\s-\s\d+$", "", display_text)

    def _refresh_suggested_entry(self, mention):
        """Inserts or updates a Suggested entry to show 'mention - count', avoiding duplicates."""
        count = self.mention_counts.get(mention, 0)
        display_text = f"{mention} - {count}"

        for index in range(self.suggested_listbox.size()):
            existing = self.suggested_listbox.get(index)
            if self._strip_count_suffix(existing) == mention:
                self.suggested_listbox.delete(index)
                self.suggested_listbox.insert(index, display_text)
                return

        self.suggested_listbox.insert("end", display_text)

    def _promote_suggested_mention(self, event):
        """Double-click on a Suggested entry: moves it to 'To remove' (makes it active)."""
        selection = self.suggested_listbox.curselection()
        if not selection:
            return
        text = self._strip_count_suffix(self.suggested_listbox.get(selection[0]))
        self.suggested_listbox.delete(selection[0])

        existing = self.mentions_listbox.get(0, "end")
        if text not in existing:
            self.mentions_listbox.insert("end", text)

        self._refresh_all_detected_titles()

    def _demote_removed_mention(self, event):
        """Double-click on a 'To remove' entry: sends it back to Suggested (deactivates it)."""
        selection = self.mentions_listbox.curselection()
        if not selection:
            return
        text = self.mentions_listbox.get(selection[0])
        self.mentions_listbox.delete(selection[0])

        self._refresh_suggested_entry(text)
        self._refresh_all_detected_titles()

    def _refresh_all_detected_titles(self):
        """
        Re-applies the current 'To remove' list to every row.
        - Not-yet-processed rows: the displayed suggestion updates immediately.
        - Already-processed rows: if the new suggestion differs from what was
          actually tagged, and the field wasn't manually edited, queue it as a
          pending fix (applied on the next click on 'Apply').
        A failure on one file must never stop the others from updating.
        """
        self._sync_mentions_to_remove()

        for info in self.scanned_plan:
            # A confident AcoustID identification isn't filename/tag-derived
            # at all - resolve_artist_title() only ever looks at those, so
            # re-running it here would silently throw the identification
            # away and fall back to the original unusable filename/tags.
            if info.get("acoustid_identified"):
                continue
            try:
                old_artist = info.get("detected_artist")
                old_title = info.get("detected_title")

                new_artist, new_title, _tags_already_present = tagger.resolve_artist_title(
                    info["file"], info.get("current_artist"), info.get("current_title")
                )

                if info.get("processed"):
                    # Only auto-suggest the new value if the user hasn't manually
                    # edited that field themselves (an auto-applied suggestion from
                    # a previous mention change is fine to recompute again).
                    if not info.get("title_override_is_manual") and new_title != old_title:
                        info["title_override"] = new_title
                        info["fix_pending"] = True
                    if not info.get("artist_override_is_manual") and new_artist != old_artist:
                        info["artist_override"] = new_artist
                        info["fix_pending"] = True
                else:
                    # If the override was just a "confirmed without changing
                    # anything" copy of the previous suggestion, drop it so it
                    # follows the new one.
                    if info.get("title_override") == old_title:
                        info["title_override"] = None
                    if info.get("artist_override") == old_artist:
                        info["artist_override"] = None

                info["detected_artist"] = new_artist
                info["detected_title"] = new_title
                if self.table.exists(info["file"]):
                    self.table.item(info["file"], values=self._build_row_values(info))
            except Exception as error:
                self._append_to_journal(f"Error refreshing '{info['file']}': {error}")

    # --- Scan ---

    def _reset_app(self):
        """Clears the current scan/table state, so a different parent folder can
        be picked and scanned fresh, without restarting the whole app."""
        if self.processing_in_progress:
            messagebox.showwarning("Processing in progress", "Wait for the current run to finish first.", parent=self.window)
            return

        for row in self.table.get_children():
            self.table.delete(row)
        self.tk_images.clear()
        self.tk_images_hover.clear()
        self._thumbnail_pil_images.clear()
        self.scanned_plan = []
        self.last_scanned_folder = None
        self._update_empty_state_hint()
        self.scan_button.configure(text="Scan")
        self._update_apply_button_label()

        self.sort_state = {"column": None, "state": 0}
        self.table.heading("title", text="Title")
        self.table.heading("artist", text="Artist")

        self.progress_canvas.pack_forget()
        self._update_progress_bar(self.progress_canvas, 0, "")

        self.journal_text.configure(state="normal")
        self.journal_text.delete("1.0", "end")
        self.journal_text.configure(state="disabled")

        self._adjust_window_height()

    def _reset_extract(self):
        """Extractor has no persistent results table like Tagger/Quality -
        it's fire-and-forget, with the outcome shown in a one-time popup -
        so there's nothing left to clear except a still-visible progress
        bar/button state if a previous run somehow left one behind. Mainly
        here so Extractor isn't the only tab without a Reset, matching
        what's asked for. Doesn't touch the chosen folder - same as
        _reset_app not touching folder_variable. No processing_in_progress-
        style guard needed: that flag only tracks Tagger's own Apply run,
        and extract_reset_button is already disabled for the whole
        duration of an extraction (see _start_extraction/"extract_done")."""
        self.extract_progress_canvas.pack_forget()
        self._update_progress_bar(self.extract_progress_canvas, 0, "")
        self.extract_button.configure(text="Extract", command=self._start_extraction)

    def _reset_quality(self):
        """Same idea as _reset_app, for the Quality tab's own results table
        and summary strip - doesn't touch the chosen folder, same as
        _reset_app leaving folder_variable alone. No processing_in_progress-
        style guard needed: that flag only tracks Tagger's own Apply run,
        and quality_reset_button is already disabled for the whole
        duration of a quality scan (see _start_quality_scan/
        _finalize_quality_scan)."""
        for row in self.quality_table.get_children():
            self.quality_table.delete(row)
        self.quality_summary_frame.pack_forget()
        self.quality_last_scanned_folder = None
        self.quality_row_paths = {}
        self._quality_scan_counts = {tagger.QUALITY_GREEN: 0, tagger.QUALITY_ORANGE: 0, tagger.QUALITY_RED: 0}
        self._quality_verdict_sort_state = 0
        self._quality_default_row_order = None
        self._update_quality_empty_state_hint()
        self.quality_scan_button.configure(text="Scan")

        self.quality_progress_canvas.pack_forget()
        self._update_progress_bar(self.quality_progress_canvas, 0, "")

        self._adjust_window_height()

    def _reset_scan_run_state(self):
        """Resets everything that must start fresh for a new scan run
        (Scan button, drag-a-folder, drag-a-single-file) - each "warned"
        flag gates its rate-limit popup to once per scan (see the
        matching message-loop handlers), and the pending-reveal state
        feeds _reveal_next_scan_row(). Factored out since all 3 call
        sites (plus __init__, for the very first scan) need the exact
        same reset and had drifted into 4 hand-copied blocks."""
        self.soundcloud_rate_limit_warned = False
        self.itunes_rate_limit_warned = False
        self.spotify_rate_limit_warned = False
        self.acoustid_rate_limit_warned = False
        self._rate_limited_messages_this_scan = []  # shown as one combined dialog in _finalize_scan
        self.source_auth_error_warned = {}  # "SoundCloud" -> already warned this scan
        self._pending_scan_reveals = []  # (info, scanned_count, total) queued for _reveal_next_scan_row
        self._pending_scan_done = None  # (removed_files, number_before), held until reveals catch up

    def _update_apply_button_label(self):
        """Shows how many of the scanned tracks are currently checked to be applied."""
        checked = sum(1 for info in self.scanned_plan if info.get("apply_changes"))
        unit = "track" if checked == 1 else "tracks"
        self.apply_status_label.configure(text=f"{checked} {unit} selected")

    def _set_buttons_enabled(self, enabled):
        """Enables/disables every action button, to avoid interference during a run."""
        state = "normal" if enabled else "disabled"
        self.browse_button.configure(state=state)
        self.scan_button.configure(state=state)
        self.reset_button.configure(state=state)
        # advanced_toggle is a plain Label (click-bound, not a real ttk
        # Button - see _toggle_advanced_section, which also has its own
        # _is_run_active() guard), so "disabling" it means faking the look
        # instead of an actual state="disabled".
        self.advanced_toggle.configure(
            foreground="#1a73e8" if enabled else "#999999", cursor="hand2" if enabled else "arrow",
        )
        if enabled:
            self.scan_button.configure(text="Scan")
            self._update_apply_button_label()
            self.apply_button.configure(text="Apply", command=self._start_processing, state="normal")
        else:
            self.cancel_requested.clear()
            self.apply_button.configure(text="Cancel", command=self._request_cancel, state="normal")
        self._set_tabs_locked(not enabled)

    def _is_run_active(self):
        """Whether a scan or apply run is currently in progress - see
        _refresh_tagger_buttons_for_connectivity for why browse_button's
        state doubles as this flag."""
        return str(self.browse_button.cget("state")) == "disabled"

    def _set_tabs_locked(self, locked):
        """Prevents switching tabs while a scan or a processing run is in progress."""
        current_index = self.notebook.index("current")
        for index in range(len(self.notebook.tabs())):
            if locked and index != current_index:
                self.notebook.tab(index, state="disabled")
            else:
                self.notebook.tab(index, state="normal")

    def _apply_track_count_limit(self, files):
        """Caps a scan at MAX_TRACKS_PER_SCAN tracks - rather than blocking
        the whole scan outright, only the first MAX_TRACKS_PER_SCAN files
        (alphabetically, same order list_audio_files() already returns them
        in) are loaded, with a one-time heads-up. Returns the files to
        actually scan (unchanged if under the limit)."""
        if len(files) <= MAX_TRACKS_PER_SCAN:
            return files

        messagebox.showinfo(
            "Beta limit reached",
            f"Track Tidy is still in beta, so a single scan is capped at "
            f"{MAX_TRACKS_PER_SCAN} tracks for now - this folder has {len(files)}.\n\n"
            f"Only the first {MAX_TRACKS_PER_SCAN} will be loaded.",
            parent=self.window,
        )
        return files[:MAX_TRACKS_PER_SCAN]

    def _start_scan(self):
        folder = self.folder_variable.get().strip()
        if not folder:
            messagebox.showwarning("Missing folder", "Please choose a folder before scanning.", parent=self.window)
            return

        tagger.MUSIC_FOLDER = folder
        all_files = tagger.list_audio_files()
        capped_files = self._apply_track_count_limit(all_files)
        truncated = len(capped_files) < len(all_files)

        self._sync_mentions_to_remove()
        self._reset_scan_run_state()

        if folder != getattr(self, "last_scanned_folder", None):
            for row in self.table.get_children():
                self.table.delete(row)
            self.tk_images.clear()
            self.tk_images_hover.clear()
            self._thumbnail_pil_images.clear()
            self.scanned_plan = []
            self.last_scanned_folder = folder
            self._update_empty_state_hint()

        self._set_buttons_enabled(False)

        self._launch_scan_after_already_scanned_check(explicit_files=capped_files if truncated else None)

    def _launch_scan_after_already_scanned_check(self, explicit_files=None):
        """Runs right before every folder-level scan actually starts
        (Scan button, drag-a-folder) - NOT the single-dropped-file case,
        which has no "folder" to ask about. A cheap local-only precheck
        (same cost class as _choose_folder's own synchronous
        list_audio_files() call, so blocking the main thread briefly here
        is consistent with existing behavior) via
        tagger.find_already_scanned_files() finds any file this scan is
        about to touch that has already been scanned before (see
        track_tidy.py's SCAN_HISTORY_FILE - this is broader than "already
        applied": it also catches a file the user reviewed and left
        unchecked last time, not just one Apply actually wrote). If
        there's at least one, offers a choice - scan only the new ones
        (default) or rescan everything - before the real (network-
        calling) scan is handed off to a background thread as usual."""
        known_files = {info["file"] for info in self.scanned_plan}
        if explicit_files is not None:
            candidate_files = sorted(set(explicit_files) - known_files)
        else:
            candidate_files = sorted(set(tagger.list_audio_files()) - known_files)

        already_scanned_files = set(tagger.find_already_scanned_files(candidate_files))
        new_files = [f for f in candidate_files if f not in already_scanned_files]

        # Stays whatever the caller passed in (None for every current
        # caller) unless the user actually chooses to filter something
        # out below - passing None through to _run_scan lets IT recompute
        # the file list itself, which is what also gets it to detect
        # removed files (files gone from disk since the last scan of this
        # folder - see _run_scan's own explicit_files handling). Passing
        # an explicit list here, even one that's unfiltered, would
        # silently lose that removed-file detection for no reason.
        files_to_scan = explicit_files

        if already_scanned_files:
            choice = self._ask_scan_mode(len(already_scanned_files), len(new_files))
            if choice is None:
                self._set_buttons_enabled(True)
                return
            if choice == "new_only":
                files_to_scan = new_files
                if not files_to_scan:
                    self._set_buttons_enabled(True)
                    messagebox.showinfo(
                        "Nothing new to scan",
                        "Every track in this folder has already been scanned before.",
                        parent=self.window,
                    )
                    return

        self._show_scan_progress_bar()
        self._run_in_background(self._run_scan, files_to_scan)

    def _ask_scan_mode(self, already_scanned_count, new_count):
        """Small choice dialog shown when some of the files about to be
        scanned have already been scanned before - "Scan only new tracks"
        is always the pre-selected default (even with zero new tracks;
        clicking Scan as-is then just shows "Nothing new to scan" - see the
        caller). Returns "new_only" or "all", or None if cancelled."""
        result = {"choice": None}

        dialog = tk.Toplevel(self.window)
        self._style_toplevel(dialog)
        dialog.title("Some tracks already scanned")
        dialog.resizable(False, False)
        dialog.transient(self.window)

        unit = "track" if already_scanned_count == 1 else "tracks"
        verb = "has" if already_scanned_count == 1 else "have"
        ttk.Label(
            dialog,
            text=f"{already_scanned_count} {unit} in this folder {verb} already been "
                 "scanned before.",
            justify="left", wraplength=380, padding=(20, 20, 20, 10),
        ).pack()

        # Always pre-selected, even when there happen to be zero new tracks -
        # clicking "Scan" as-is then just shows the "Nothing new to scan"
        # info dialog (see the caller), which is a clearer outcome than
        # silently switching the default to "Rescan everything" underneath
        # the user without them choosing that.
        choice_var = tk.StringVar(value="new_only")
        options_frame = ttk.Frame(dialog)
        options_frame.pack(fill="x", padx=20, pady=(0, 5))
        ttk.Radiobutton(
            options_frame, text=f"Scan only new tracks ({new_count})",
            variable=choice_var, value="new_only",
        ).pack(anchor="w", pady=2)
        ttk.Radiobutton(
            options_frame, text="Rescan everything", variable=choice_var, value="all",
        ).pack(anchor="w", pady=2)

        def confirm():
            result["choice"] = choice_var.get()
            dialog.destroy()

        def cancel():
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", cancel)

        button_row = ttk.Frame(dialog)
        button_row.pack(pady=(5, 15))
        scan_button = ttk.Button(button_row, text="Scan", command=confirm, width=12)
        scan_button.pack(side="left", padx=5)
        ttk.Button(button_row, text="Cancel", command=cancel, width=12).pack(side="left", padx=5)

        self._center_dialog(dialog)
        # grab_set()/focus deliberately deferred until AFTER the dialog is
        # actually mapped (_center_dialog's deiconify()) rather than right
        # after creation like most of this app's other dialogs - those
        # don't block on wait_window() for their result, so a grab that
        # doesn't fully "stick" on a not-yet-viewable window goes unnoticed
        # (the OK/single button still works via normal focus). This dialog's
        # whole return value depends on a click actually registering, so it
        # gets the extra explicit focus_force()/focus_set() below. (NOT
        # wait_visibility() first - tried that, it can block for a long time
        # waiting for a Visibility event that isn't always delivered
        # promptly, which is worse than the problem it was meant to fix.)
        dialog.grab_set()
        dialog.focus_force()
        scan_button.focus_set()
        dialog.wait_window()
        return result["choice"]

    def _show_scan_progress_bar(self):
        """Shows the same progress bar Apply uses (progress_canvas, right
        below the Apply button) - reused as-is rather than a second bar
        near the Scan button, since Scan and Apply never run at once."""
        if not self.progress_canvas.winfo_ismapped():
            self.progress_canvas.pack(fill="x")
            self._adjust_window_height()
        self._update_progress_bar(self.progress_canvas, 0, "0 %")

    def _update_scan_progress_bar(self, scanned_count, total):
        fraction = scanned_count / total if total else 0
        self._update_progress_bar(self.progress_canvas, fraction, f"{round(fraction * 100)} %")

    def _run_scan(self, explicit_files=None):
        number_before = len(self.scanned_plan)
        try:
            known_files = {info["file"] for info in self.scanned_plan}

            if explicit_files is not None:
                new_files = sorted(set(explicit_files) - known_files)
                removed_files = set()
            else:
                current_files = set(tagger.list_audio_files())
                new_files = sorted(current_files - known_files)
                removed_files = known_files - current_files

            if new_files:
                self._append_to_journal(
                    f"Scan: {len(new_files)} new file(s) to analyze "
                    f"(searching for cover online)..."
                )

            total = len(new_files)
            scanned_count = {"value": 0}

            def _on_file_scanned(info):
                scanned_count["value"] += 1
                tagger.mark_file_scanned(info["file"])
                # Not displayed the instant it's ready - see
                # _reveal_next_scan_row(): queued here so the TABLE reveals
                # tracks no faster than SCAN_REVEAL_INTERVAL_MS apart, while
                # the actual scan (this callback) keeps running at full
                # speed underneath, unaffected.
                self.message_queue.put(("file_scanned", (info, scanned_count["value"], total)))

            tagger.scan_files(
                new_files,
                on_file_scanned=_on_file_scanned,
                log=self._append_to_journal,
                on_new_mention=lambda mention: self.message_queue.put(("mention_added", mention)),
                on_rate_limited=lambda: self.message_queue.put(("soundcloud_rate_limited", None)),
                should_cancel=self.cancel_requested.is_set,
                on_auth_error=self._on_source_auth_error,
                on_itunes_rate_limited=lambda: self.message_queue.put(("itunes_rate_limited", None)),
                on_spotify_rate_limited=lambda: self.message_queue.put(("spotify_rate_limited", None)),
                on_acoustid_rate_limited=lambda: self.message_queue.put(("acoustid_rate_limited", None)),
            )

        except Exception as error:
            self._append_to_journal(f"Error during scan: {error}")
            removed_files = set()

        self.message_queue.put(("scan_done", (removed_files, number_before)))

    def _reveal_next_scan_row(self):
        """Ticks every SCAN_REVEAL_INTERVAL_MS, for the app's entire
        lifetime: pops at most one buffered scan result into the table (see
        the "file_scanned" handler in _start_message_loop) so tracks
        visibly appear no faster than that, no matter how fast the actual
        scan (running unaffected in the background) produces them. Only
        finalizes the
        scan (see _finalize_scan) once every buffered result has actually
        been revealed - otherwise the summary/buttons would jump ahead of
        rows still trickling into view.

        Cancellation bypasses the pacing entirely: once Cancel is clicked,
        every remaining buffered result is flushed in one go instead of
        trickling out over however many seconds are left, so the buttons/
        UI actually respond right away like they did before this pacing
        was added."""
        if self.cancel_requested.is_set():
            while self._pending_scan_reveals:
                info, scanned_count, total = self._pending_scan_reveals.pop(0)
                self._add_scan_row(info)
                self.scan_button.configure(text=f"Scan - {scanned_count}/{total}")
                self._update_scan_progress_bar(scanned_count, total)
        elif self._pending_scan_reveals:
            info, scanned_count, total = self._pending_scan_reveals.pop(0)
            self._add_scan_row(info)
            self.scan_button.configure(text=f"Scan - {scanned_count}/{total}")
            self._update_scan_progress_bar(scanned_count, total)

        if not self._pending_scan_reveals and self._pending_scan_done is not None:
            content, self._pending_scan_done = self._pending_scan_done, None
            self._finalize_scan(content)

        self.window.after(SCAN_REVEAL_INTERVAL_MS, self._reveal_next_scan_row)

    def _update_empty_state_hint(self):
        """Shows the drag-and-drop/select-a-folder hint centered over the
        table only while it has no rows at all - called after every table
        mutation that could take it to (or from) zero rows."""
        if self.table.get_children():
            self.empty_state_frame.place_forget()
        else:
            self.empty_state_frame.place(relx=0.5, rely=0.5, anchor="center")

    def _update_quality_empty_state_hint(self):
        """Same idea as _update_empty_state_hint, for the Quality tab's own
        table: explains what the green/orange/red dot means while there's
        nothing scanned yet to show it on directly."""
        if self.quality_table.get_children():
            self.quality_empty_state_frame.place_forget()
        else:
            self.quality_empty_state_frame.place(relx=0.5, rely=0.5, anchor="center")

    def _add_scan_row(self, info):
        """Immediately adds a row to the table, ABOVE the previous ones, as soon as a file has just been scanned."""
        # Re-sync with the CURRENT "To remove" list (main thread, authoritative)
        # before displaying - the background scan thread may have computed this
        # row's title with a slightly stale mentions list (e.g. "By FuviClan"
        # just got auto-activated by an earlier file in the very same scan).
        self._sync_mentions_to_remove()
        # Skip for a confident AcoustID identification - resolve_artist_title()
        # only ever looks at the filename/existing tags, so re-running it
        # here would silently throw away the audio-content identification
        # and fall right back to the original unusable filename/tags.
        if not info.get("processed") and not info.get("acoustid_identified"):
            new_artist, new_title, _tags_already_present = tagger.resolve_artist_title(
                info["file"], info.get("current_artist"), info.get("current_title")
            )
            info["detected_artist"] = new_artist
            info["detected_title"] = new_title

        self.scanned_plan.insert(0, info)
        info["original_order"] = len(self.scanned_plan) - 1  # to be able to go back to scan order
        image_tk = self._create_thumbnail(info)
        self.tk_images[info["file"]] = image_tk
        self.table.insert(
            "", 0, iid=info["file"],
            image=image_tk if image_tk else "",
            values=self._build_row_values(info),
        )
        self._restripe_rows()
        self._flash_new_row(info["file"])
        self._update_empty_state_hint()

    # Row-appear flash: a real slide/fade-in isn't possible on a native
    # Treeview row (Tkinter has no per-row opacity/position animation), so
    # this fakes "smooth" by animating the row's background color from an
    # accent tint down to its normal stripe color over a few quick steps.
    ROW_FLASH_STEPS = 8
    ROW_FLASH_STEP_MS = 25

    # Verdict-colored tags a settled Quality-tab row can carry - if a flash's
    # final tags include one, that foreground stays visible through the
    # flash instead of the plain row color (see _flash_new_row).
    _VERDICT_TAG_NAMES = ("verdict_green", "verdict_orange", "verdict_red")

    def _flash_new_row(self, file_iid, tree=None, final_tags=None):
        """Reusable across both the Tagger table and the Quality table
        (tree=self.quality_table) - same accent-tint-to-normal-color flash,
        just parameterized over which Treeview and which tags the row
        should settle back into (defaults match the Tagger table's own
        original always-plain-striped behavior)."""
        tree = tree if tree is not None else self.table
        is_dark = self.theme_colors is not None
        start_color = self.theme_colors["select_bg"] if is_dark else "#cfe0f5"
        is_even = tree.index(file_iid) % 2 == 0
        if is_dark:
            end_color = self.theme_colors["tree_bg"] if is_even else self.theme_colors["tree_odd_row"]
            fg_color = self.theme_colors["tree_fg"]
        else:
            end_color = "#ffffff" if is_even else "#e9e9e9"
            fg_color = "black"

        if final_tags is None:
            final_tags = ("even_row" if is_even else "odd_row",)
        for tag in final_tags:
            if tag in self._VERDICT_TAG_NAMES:
                verdict_fg = tree.tag_configure(tag).get("foreground")
                if verdict_fg:
                    fg_color = verdict_fg

        flash_tag = f"flash_{file_iid}"

        def _step(n):
            # The row may have been removed (filtered out, deleted) or
            # restriped since scheduling - bail out rather than resurrect
            # a stale tag/color on a row that's no longer this one.
            if not tree.exists(file_iid) or flash_tag not in tree.item(file_iid, "tags"):
                return
            if n > self.ROW_FLASH_STEPS:
                tree.item(file_iid, tags=final_tags)
                return
            t = n / self.ROW_FLASH_STEPS
            tree.tag_configure(
                flash_tag,
                background=self._interpolate_color(start_color, end_color, t),
                foreground=fg_color,
            )
            self.window.after(self.ROW_FLASH_STEP_MS, _step, n + 1)

        tree.item(file_iid, tags=(flash_tag,))
        _step(0)

    @staticmethod
    def _interpolate_color(start_hex, end_hex, t):
        """Linear-interpolates between two "#rrggbb" colors at fraction t (0-1)."""
        start_rgb = [int(start_hex[i : i + 2], 16) for i in (1, 3, 5)]
        end_rgb = [int(end_hex[i : i + 2], 16) for i in (1, 3, 5)]
        mixed = [round(s + (e - s) * t) for s, e in zip(start_rgb, end_rgb)]
        return "#{:02x}{:02x}{:02x}".format(*mixed)

    def _restripe_rows(self, tree=None):
        """Re-applies alternating row colors based on each row's current
        position. Reused for the Quality table (tree=self.quality_table)
        too - there, rows also carry a verdict color tag alongside the
        stripe tag, so that tag is preserved instead of being dropped."""
        tree = tree if tree is not None else self.table
        for index, item_id in enumerate(tree.get_children()):
            stripe_tag = "even_row" if index % 2 == 0 else "odd_row"
            other_tags = [t for t in tree.item(item_id, "tags") if t not in ("even_row", "odd_row")]
            tree.item(item_id, tags=tuple([stripe_tag] + other_tags))

    def _fade_out_and_delete_rows(self, item_ids, on_complete=None):
        """Mirror of _flash_new_row for removal: fades each row toward the
        window background before actually deleting it, instead of having
        it vanish instantly. on_complete runs once every row involved has
        either finished fading or turned out to not exist - restripe/label
        updates that assume the rows are already gone must go there, not
        run immediately, or _restripe_rows() would prematurely overwrite
        the fade tag mid-animation (rows still exist in the tree while
        fading).

        Tracks in-flight fades in self._fading_row_tags so a caller that's
        about to re-insert one of these same row iids (e.g. Ctrl+Z undo)
        can force it to finish immediately first - Treeview raises on
        inserting an iid that's still technically present mid-fade."""
        if not hasattr(self, "_fading_row_tags"):
            self._fading_row_tags = {}

        is_dark = self.theme_colors is not None
        end_color = self.theme_colors["bg"] if is_dark else "#f0f0f0"
        fg_color = self.theme_colors["tree_fg"] if is_dark else "black"

        pending = {}
        for item_id in item_ids:
            if not self.table.exists(item_id):
                continue
            is_even = self.table.index(item_id) % 2 == 0
            if is_dark:
                start_color = self.theme_colors["tree_bg"] if is_even else self.theme_colors["tree_odd_row"]
            else:
                start_color = "#ffffff" if is_even else "#e9e9e9"
            tag = f"fadeout_{item_id}"
            pending[item_id] = (tag, start_color)
            self._fading_row_tags[item_id] = tag
            self.table.item(item_id, tags=(tag,))

        def _step(n):
            alive = [item_id for item_id in pending if self.table.exists(item_id)]
            if n > self.ROW_FLASH_STEPS or not alive:
                for item_id in alive:
                    # Only delete if still carrying OUR fade tag - a forced
                    # finish (see force_finish_row_fade) or a stale/replaced
                    # row already handled it.
                    if self.table.exists(item_id) and self.table.item(item_id, "tags") == (pending[item_id][0],):
                        self.table.delete(item_id)
                    self._fading_row_tags.pop(item_id, None)
                if on_complete:
                    on_complete()
                return
            t = n / self.ROW_FLASH_STEPS
            for item_id in alive:
                tag, start_color = pending[item_id]
                self.table.tag_configure(
                    tag, background=self._interpolate_color(start_color, end_color, t), foreground=fg_color,
                )
            self.window.after(self.ROW_FLASH_STEP_MS, _step, n + 1)

        _step(0)

    def _force_finish_row_fade(self, item_id):
        """Immediately completes an in-progress fade-out for item_id (see
        _fade_out_and_delete_rows), deleting it right now instead of
        waiting - for a caller about to re-insert the same iid (undo)."""
        if hasattr(self, "_fading_row_tags") and item_id in self._fading_row_tags:
            if self.table.exists(item_id):
                self.table.delete(item_id)
            del self._fading_row_tags[item_id]

    def _schedule_table_filter(self, event=None):
        """Debounces the search filter: waits 300ms after the last keystroke before applying it."""
        if getattr(self, "_table_filter_after_id", None):
            self.window.after_cancel(self._table_filter_after_id)
        self._table_filter_after_id = self.window.after(300, self._apply_table_filter)

    def _apply_table_filter(self):
        """Shows only rows whose title or artist match the search box
        (case-insensitive), further narrowed to rows with no cover match if
        the "Only show tracks with no cover match" checkbox is on."""
        if getattr(self.table_filter_entry, "placeholder_active", False):
            query = ""
        else:
            query = self.table_filter_entry.get().strip().lower()

        no_cover_only = self.no_cover_filter_var.get()

        for summary_row_id in (NO_COVER_SUMMARY_ROW_ID, SEARCH_RESULT_SUMMARY_ROW_ID):
            if self.table.exists(summary_row_id):
                self.table.delete(summary_row_id)

        for info in self.scanned_plan:
            if self.table.exists(info["file"]):
                self.table.detach(info["file"])

        hidden_with_cover = 0
        visible_count = 0
        for info in self.scanned_plan:
            title = info.get("title_override") or info.get("detected_title") or info.get("current_title") or ""
            artist = info.get("artist_override") or info.get("detected_artist") or info.get("current_artist") or ""
            searchable = f"{title} {artist}".lower()

            if query and query not in searchable:
                continue
            # Deliberately NOT has_usable_cover() here - this filter means
            # "did an online search actually find something", not "will the
            # final file end up with some cover or other". A track that
            # kept its existing (perfectly fine, non-banned) cover only
            # because the online search found nothing still belongs in this
            # list - that's a real matching failure worth reviewing, just
            # one that happens to have a fallback cover to hide behind.
            # Real report: exactly this case (kept its own cover, no online
            # match) was invisible in the filter. already_applied rows are
            # still excluded below though (see _run_scan's no_cover_infos,
            # kept in sync with this filter on purpose) - no search was
            # even ATTEMPTED for those, so they're not "a search that found
            # nothing" at all.
            if no_cover_only and (info.get("found_cover_image") or info.get("already_applied")):
                hidden_with_cover += 1
                continue

            visible_count += 1
            if self.table.exists(info["file"]):
                self.table.move(info["file"], "", "end")

        if hidden_with_cover:
            self.table.insert(
                "", "end", iid=NO_COVER_SUMMARY_ROW_ID,
                values=("", f"- - - {hidden_with_cover} track(s) with cover - - -", "", ""),
            )

        # Shown alongside the results themselves (not instead of them) so a
        # search's match count is clear at a glance, the same way the
        # no-cover filter already summarizes what it hides.
        if query:
            unit = "track" if visible_count == 1 else "tracks"
            self.table.insert(
                "", "end", iid=SEARCH_RESULT_SUMMARY_ROW_ID,
                values=("", f"- - - {visible_count} {unit} found - - -", "", ""),
            )

        self._restripe_rows()

    def _show_no_files_dialog(self):
        """Custom error dialog that plays a fart sound instead of the default OS error beep."""
        sound_path = resource_path("assets/fart.wav")
        try:
            play_short_sound(sound_path)
        except Exception:
            pass  # if the sound file is missing, just show the dialog silently

        dialog = tk.Toplevel(self.window)
        self._style_toplevel(dialog)
        dialog.title("No file found")
        dialog.resizable(False, False)
        dialog.transient(self.window)
        dialog.grab_set()

        ttk.Label(
            dialog,
            text="💨 No audio file (.mp3, .wav, .aiff, etc.) was found in this\nfolder (or its subfolders).",
            justify="center",
            padding=20,
        ).pack()
        ttk.Button(dialog, text="OK", command=dialog.destroy).pack(pady=(0, 15))

        self._center_dialog(dialog)

    def _show_processing_failures_dialog(self):
        """Shown right before the "Processing complete" dialog whenever
        Apply hit one or more files it couldn't actually process (most
        commonly: corrupted audio data mutagen can't read/write at all,
        e.g. "can't sync to MPEG frame") - process_files() keeps going for
        the rest of the batch, but without this the failure was only ever
        visible buried in the log, easy to miss."""
        failures = self._processing_failures
        count = len(failures)
        unit = "file" if count == 1 else "files"

        dialog = tk.Toplevel(self.window)
        self._style_toplevel(dialog)
        dialog.title("Some files could not be processed")
        dialog.resizable(False, False)
        dialog.transient(self.window)
        dialog.grab_set()

        ttk.Label(
            dialog,
            text=f"⚠ {count} {unit} could not be processed - likely corrupted "
                 f"audio data. Everything else in this run was unaffected.",
            justify="left", wraplength=420,
            padding=(20, 20, 20, 10),
        ).pack()

        list_frame = ttk.Frame(dialog)
        list_frame.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        text_height = min(count, 8)
        failures_text = scrolledtext.ScrolledText(
            list_frame, height=text_height, width=50, wrap="word", state="normal",
        )
        for identifier, reason in failures:
            failures_text.insert("end", f"{identifier}\n    {reason}\n")
        failures_text.configure(state="disabled")
        if self.theme_colors:
            failures_text.configure(
                bg=self.theme_colors["journal_bg"], fg=self.theme_colors["journal_fg"],
                insertbackground=self.theme_colors["journal_fg"],
            )
        failures_text.pack(fill="both", expand=True)

        ttk.Button(dialog, text="OK", command=dialog.destroy).pack(pady=(0, 15))
        dialog.bind("<Escape>", lambda _event: dialog.destroy())

        self._center_dialog(dialog)

    def _play_success_sound(self):
        """Distinct chime on a successful Apply run - the progress bar
        already shows "Done ✓" once process_files() finishes, so this is
        just an audible confirmation, not a modal to click through."""
        sound_path = resource_path("assets/success.wav")
        try:
            play_short_sound(sound_path)
        except Exception:
            pass  # if the sound file is missing, just stay silent

    def _finalize_scan(self, result):
        removed_files, number_before = result
        self._set_buttons_enabled(True)

        # Reset (not "Done ✓"/left showing, unlike Apply) - a scan's own
        # progress is done being useful the moment it ends, and leaving it
        # sitting there at 100% would be stale/misleading by the time the
        # user gets around to clicking Apply, which starts its own run of
        # this same bar from 0% anyway.
        if self.progress_canvas.winfo_ismapped():
            self.progress_canvas.pack_forget()
            self._update_progress_bar(self.progress_canvas, 0, "")
            self._adjust_window_height()

        # One combined warning for every source that hit its rate limit
        # during this scan, shown here instead of interrupting mid-scan
        # per source (see the message-loop handlers, which just record
        # into _rate_limited_messages_this_scan) - matches how
        # _check_source_health_on_startup's own multi-source warning is
        # already combined into one dialog, and lets the scan run to
        # completion uninterrupted instead of stacking up to 4 separate
        # modal popups if several sources happen to saturate in the same
        # run.
        if self._rate_limited_messages_this_scan:
            messagebox.showwarning(
                "Cover source rate limit reached",
                "\n\n".join(self._rate_limited_messages_this_scan)
                + "\n\nThe scan kept going with the other cover sources in the meantime.",
                parent=self.window,
            )

        # Removes files that no longer exist on disk
        for file_name in removed_files:
            self.tk_images.pop(file_name, None)
            self.tk_images_hover.pop(file_name, None)
            self._thumbnail_pil_images.pop(file_name, None)
        self._fade_out_and_delete_rows(removed_files, on_complete=self._restripe_rows)
        self.scanned_plan = [info for info in self.scanned_plan if info["file"] not in removed_files]

        number_new = len(self.scanned_plan) - number_before + len(removed_files)

        if not self.scanned_plan:
            self._append_to_journal("No audio file (.mp3, .wav, .aiff, etc.) found in this folder.")
            self._show_no_files_dialog()
        else:
            if number_new == 0 and not removed_files:
                self._append_to_journal("Scan complete: no new file detected.")
            else:
                self._append_to_journal(
                    f"Scan complete: {number_new} new, "
                    f"{len(removed_files)} removed, {len(self.scanned_plan)} total."
                )

            # Same "did an online search actually find something" criterion
            # as the "Only show tracks with no cover match" filter (see
            # _apply_table_filter) - not has_usable_cover(), which also
            # counts as "usable" a checked row that kept its own existing
            # (non-banned) cover with no online match at all. Keeping both
            # in sync avoids a confusing mismatch between what the filter
            # shows and what this count (and the Discord report below) say.
            # already_applied rows are excluded too - unlike a genuine
            # search-came-up-empty row, no search was even ATTEMPTED for
            # these (skipped entirely, see scan_files), so they don't
            # belong in a "no cover match" count at all. Real report:
            # rescanning a folder of already-fully-tagged tracks (search
            # skipped for all of them) still logged "N track(s) currently
            # have no cover match" for every one of them, which read as
            # "none of these have a cover" even though they all did.
            # Tracks a search was actually attempted for this scan (as
            # opposed to a previously-tagged file scan_files skipped
            # outright) - the basis for both the no-cover count below and
            # the per-source match breakdown sent to Discord.
            searched_infos = [
                info for info in self.scanned_plan
                if not info.get("processed") and not info.get("already_applied")
            ]
            no_cover_infos = [info for info in searched_infos if not info.get("found_cover_image")]
            if no_cover_infos:
                self._append_to_journal(f"{len(no_cover_infos)} track(s) currently have no cover match.")

                # Unusually high miss rate for this folder - automatically
                # send the whole batch to Discord (same info as a manual
                # "Report track...") instead of just leaving it buried in
                # the log, since that's what actually improves future
                # matching, without relying on the user reporting each one.
                if len(no_cover_infos) / len(self.scanned_plan) > NO_COVER_REPORT_THRESHOLD:
                    self._notify_no_cover_report(no_cover_infos, len(self.scanned_plan))

            # Skip the Discord ping for a no-op rescan (nothing new,
            # nothing removed) - otherwise repeatedly clicking Scan on an
            # already-scanned folder spams the channel with empty
            # "0 new, 0 removed" notifications. Sent even if the user
            # cancelled partway through (cancelled=True relabels the
            # embed) - Kevin wants visibility into a cancelled scan too,
            # not just ones that ran to completion.
            if number_new > 0 or removed_files:
                self._notify_scan_complete(
                    number_new, len(removed_files), len(self.scanned_plan),
                    number_no_cover=len(no_cover_infos),
                    number_rate_limited_sources=len(self._rate_limited_messages_this_scan),
                    auth_error_sources=sorted(self.source_auth_error_warned),
                    cancelled=self.cancel_requested.is_set(),
                    number_itunes=sum(1 for i in searched_infos if i.get("cover_source") == "iTunes"),
                    number_spotify=sum(1 for i in searched_infos if i.get("cover_source") == "Spotify"),
                    number_soundcloud=sum(1 for i in searched_infos if i.get("cover_source") == "SoundCloud"),
                    number_acoustid_used=sum(1 for i in searched_infos if i.get("acoustid_identified")),
                )

        self._check_for_duplicates()

    def _check_for_duplicates(self):
        """After a scan, looks for '._'-prefixed duplicates and offers to merge them."""
        all_files = [info["file"] for info in self.scanned_plan]
        duplicate_pairs = tagger.find_dot_underscore_duplicates(all_files)

        if not duplicate_pairs:
            return

        message = (
            f"{len(duplicate_pairs)} duplicate file(s) detected (same name and duration, "
            f"just prefixed with '._').\n\nMerge them now? This will delete the '._' copies."
        )
        if not messagebox.askyesno("Duplicate tracks found", message, parent=self.window, default=messagebox.NO):
            return

        for dot_file, normal_file in duplicate_pairs:
            full_path = os.path.join(tagger.MUSIC_FOLDER, dot_file)
            try:
                os.remove(full_path)
                self._append_to_journal(f"Removed duplicate: '{dot_file}' (kept '{normal_file}')")
            except Exception as error:
                self._append_to_journal(f"Could not remove '{dot_file}': {error}")
                continue

            if self.table.exists(dot_file):
                self.table.delete(dot_file)
            self.tk_images.pop(dot_file, None)
            self.tk_images_hover.pop(dot_file, None)
            self._thumbnail_pil_images.pop(dot_file, None)
            self.scanned_plan = [info for info in self.scanned_plan if info["file"] != dot_file]

    # --- Fix Artist/Title and search again (tracks with no cover match) ---

    def _quick_rescan(self, infos):
        """Right-click "Rescan" - re-runs the cover search directly with
        whatever Artist/Title the table is already showing for each row
        (its override if the user corrected it via double-click, otherwise
        the detected one) - no re-entry dialog, since that value is
        already right there. Runs sequentially in one background thread,
        reusing a single SoundCloud token across the batch, same as a real
        scan (see scan_files) - firing one independent thread per row
        would multiply token fetches and hit iTunes/SoundCloud with an
        unthrottled burst instead. Reuses
        _apply_fix_row_search_result to update the table, which already
        tolerates no "Fix no cover" dialog being open."""
        if self._is_run_active():
            messagebox.showinfo(
                "Scan in progress",
                "Wait for the current scan/apply to finish before rescanning tracks manually.",
                parent=self.window,
            )
            return

        to_search = []
        skipped = 0
        for info in infos:
            artist = info.get("artist_override") or info.get("detected_artist")
            title = info.get("title_override") or info.get("detected_title")
            if not artist or not title:
                skipped += 1
                continue
            to_search.append((info, artist, title))

        if skipped:
            messagebox.showwarning(
                "Missing info",
                f"{skipped} track(s) skipped - no Artist/Title to search with yet. "
                "Use \"Fix no cover\" or edit the cell first.",
                parent=self.window,
            )
        if not to_search:
            return

        def _run():
            soundcloud_token = None
            if tagger.USE_SOUNDCLOUD and tagger.SOUNDCLOUD_CLIENT_ID and tagger.SOUNDCLOUD_CLIENT_SECRET:
                soundcloud_token = tagger.get_soundcloud_token(
                    log=self._append_to_journal, on_auth_error=self._on_source_auth_error,
                )
            spotify_token = None
            if tagger.USE_SPOTIFY and tagger.SPOTIFY_CLIENT_ID and tagger.SPOTIFY_CLIENT_SECRET:
                spotify_token = tagger.get_spotify_token(
                    log=self._append_to_journal, on_auth_error=self._on_source_auth_error,
                )
            for info, artist, title in to_search:
                self._append_to_journal(f"Rescanning '{artist} - {title}'...")
                found_cover_image, cover_source, returned_artist, returned_title = tagger.search_cover_manual(
                    artist, title, soundcloud_token, log=self._append_to_journal, spotify_token=spotify_token,
                )
                self.message_queue.put((
                    "fix_row_search_result",
                    (info["file"], artist, title, found_cover_image, cover_source, returned_artist, returned_title),
                ))

        self._run_in_background(_run)

    def _show_fix_no_cover_dialog(self, infos):
        """Lets the user correct Artist/Title for each track and retry the
        cover search right there - each row is independent, so some can be
        fixed while others are left for later. Used for tracks a scan
        couldn't find a cover for at all (often nothing usable to search
        with in the first place, unlike _quick_rescan's case)."""
        dialog = tk.Toplevel(self.window)
        self._style_toplevel(dialog)
        dialog.title("Correct Artist/Title and search again")
        dialog.geometry("640x420")
        dialog.transient(self.window)

        ttk.Label(
            dialog, text="Correct the Artist/Title below, then click Search to try again.",
            padding=(10, 10, 10, 5),
        ).pack(anchor="w")

        canvas_frame = ttk.Frame(dialog)
        canvas_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        canvas = tk.Canvas(canvas_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="left", fill="y")

        rows_frame = ttk.Frame(canvas)
        canvas_window = canvas.create_window((0, 0), window=rows_frame, anchor="nw")
        rows_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))

        # Mouse wheel events don't bubble to a Canvas by default - bind_all
        # is the standard Tkinter workaround, but it's global, so it's only
        # wired up while the cursor is actually over the canvas (via
        # Enter/Leave) to avoid hijacking scrolling anywhere else in the app.
        unbind_mousewheel = self._bind_canvas_mousewheel(canvas)

        if self.theme_colors:
            canvas.configure(bg=self.theme_colors["bg"])

        self._fix_dialog_rows = {}

        for info in infos:
            file_name = info["file"]
            row = ttk.Frame(rows_frame)
            row.pack(fill="x", padx=(0, 10), pady=(0, 10))

            ttk.Label(row, text=file_name, font=("TkDefaultFont", 8, "bold")).pack(anchor="w")

            fields_row = ttk.Frame(row)
            fields_row.pack(fill="x", pady=(2, 0))

            # The two entries are always pre-filled (never empty), so a
            # placeholder wouldn't show - a plain label is the only way to
            # actually tell them apart at a glance.
            ttk.Label(fields_row, text="Artist:").pack(side="left", padx=(0, 3))
            artist_entry = ttk.Entry(fields_row, width=20)
            artist_entry.insert(0, info.get("artist_override") or info.get("detected_artist") or "")
            artist_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
            self._bind_entry_context_menu(artist_entry)

            ttk.Label(fields_row, text="Title:").pack(side="left", padx=(0, 3))
            title_entry = ttk.Entry(fields_row, width=20)
            title_entry.insert(0, info.get("title_override") or info.get("detected_title") or "")
            title_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
            self._bind_entry_context_menu(title_entry)

            search_button = ttk.Button(fields_row, text="Search")
            search_button.pack(side="left", padx=(0, 5))

            status_label = ttk.Label(fields_row, text="", width=12)
            status_label.pack(side="left")

            search_button.configure(
                command=lambda i=info, ae=artist_entry, te=title_entry, sb=search_button, sl=status_label:
                    self._search_fix_row(i, ae, te, sb, sl)
            )

            self._fix_dialog_rows[file_name] = {
                "artist_entry": artist_entry, "title_entry": title_entry,
                "search_button": search_button, "status_label": status_label,
            }

        ttk.Button(dialog, text="Close", command=dialog.destroy).pack(pady=(0, 10))

        def on_close():
            unbind_mousewheel()
            self._fix_dialog_rows = {}
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", on_close)

        # <Control-z> is only bound on the main window's table, so it never
        # fires while this Toplevel has keyboard focus - bind it here too so
        # undo still works while fixing rows.
        dialog.bind("<Control-z>", self._undo_last_action)

        self._center_dialog(dialog)

    def _search_fix_row(self, info, artist_entry, title_entry, search_button, status_label):
        if self._is_run_active():
            messagebox.showinfo(
                "Scan in progress",
                "Wait for the current scan/apply to finish before searching manually.",
                parent=search_button.winfo_toplevel(),
            )
            return

        artist = artist_entry.get().strip()
        title = title_entry.get().strip()
        if not artist or not title:
            messagebox.showwarning(
                "Missing info", "Both Artist and Title are required.", parent=search_button.winfo_toplevel(),
            )
            return

        search_button.configure(state="disabled")
        status_label.configure(text="Searching...")

        def _run():
            found_cover_image, cover_source, returned_artist, returned_title = tagger.search_cover_manual_with_tokens(
                artist, title, log=self._append_to_journal, on_auth_error=self._on_source_auth_error,
            )
            self.message_queue.put((
                "fix_row_search_result",
                (info["file"], artist, title, found_cover_image, cover_source, returned_artist, returned_title),
            ))

        self._run_in_background(_run)

    def _apply_fix_row_search_result(self, content):
        file_name, artist, title, found_cover_image, cover_source, returned_artist, returned_title = content

        info = next((i for i in self.scanned_plan if i["file"] == file_name), None)
        if info is not None:
            # Same override mechanism as double-clicking the Title/Artist
            # cell - keeps the correction visible in the main table even if
            # this particular search still doesn't find a cover.
            info["artist_override"] = artist
            info["title_override"] = title
            if found_cover_image:
                info["found_cover_image"] = found_cover_image
                info["cover_source"] = cover_source
                if not info.get("processed"):
                    info["apply_changes"] = True
            if self.table.exists(file_name):
                self._refresh_row(info)

        row_widgets = self._fix_dialog_rows.get(file_name)
        if row_widgets and row_widgets["status_label"].winfo_exists():
            if found_cover_image:
                row_widgets["status_label"].configure(text=f"{PROCESSED_CHECK} Found ({cover_source})")
            else:
                row_widgets["status_label"].configure(text="Not found")
                row_widgets["search_button"].configure(state="normal")

    # --- Table row rendering ---

    def _create_thumbnail(self, info):
        """Builds the cover thumbnail (image only, no checkbox)."""
        image_bytes = tagger.effective_cover_bytes(info)
        # Stale either way - regenerated lazily next time this row is hovered.
        self.tk_images_hover.pop(info["file"], None)

        if not image_bytes:
            self._thumbnail_pil_images.pop(info["file"], None)
            return None

        try:
            image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
            image.thumbnail(THUMBNAIL_SIZE)
            self._thumbnail_pil_images[info["file"]] = image
            return ImageTk.PhotoImage(image)
        except Exception:
            self._thumbnail_pil_images.pop(info["file"], None)
            return None

    def _refresh_row(self, info):
        """Updates both the image and the text values of a row after a change."""
        image_tk = self._create_thumbnail(info)
        self.tk_images[info["file"]] = image_tk

        self.table.item(
            info["file"],
            image=image_tk if image_tk else "",
            values=self._build_row_values(info),
        )

    def _build_row_values(self, info):
        """Builds the tuple of displayed values for a row (image handled separately)."""
        # AIFF is treated like MP3 here - already taggable and lossless, it
        # never defaults to converting (see _finish_scan) and isn't offered
        # a convert checkbox at all, same plain "AIFF" display as "MP3".
        # FLAC gets the same treatment, but only while "Convert everything
        # to MP3" is off - it's tagged in place then too (see
        # open_audio_file/write_tags), same as AIFF. Once that setting is
        # on, FLAC converts to MP3 like every other non-MP3/AIFF/WAV
        # format (see _resolve_conversion_target), so its checkbox comes
        # back - forced on, same as the rest, not a per-row choice.
        needs_conversion = info["format"] not in ("MP3", "AIFF") and not (
            info["format"] == "FLAC" and not tagger.AUTO_CONVERT_MP3
        )
        # What "convert" actually resolves to for THIS file - MP3 for most
        # formats, but WAV can go to AIFF instead (see AUTO_CONVERT_WAV_TO_AIFF /
        # _resolve_conversion_target) purely for cover-art compatibility
        # with software that doesn't read artwork from WAV (e.g. Rekordbox).
        target_format = (tagger._resolve_conversion_target(info["file"]) or "mp3").upper()

        # AcoustID identifies a track from the audio itself, not the
        # filename/tags - a real, if uncommon, way for it to be confidently
        # (high score) wrong: two different tracks/remixes in similar
        # genres (e.g. house/techno) can fingerprint close enough to
        # collide. Flagged here so the user knows to double check it
        # before trusting Apply - cleared once they've actually reviewed
        # it (title_override no longer None, whether they kept it or
        # corrected it).
        acoustid_marker = " 🎧" if info.get("acoustid_identified") and info["title_override"] is None else ""
        # Same "no cover match" criterion as the post-scan count/filter (see
        # _run_scan's no_cover_infos / _apply_table_filter) - a genuine
        # search miss, not a file whose search was skipped because it's
        # already fully tagged. Cleared once reviewed (title_override set),
        # same as the AcoustID marker just above.
        no_cover_marker = (
            " ⚠️"
            if not info.get("found_cover_image") and not info.get("already_applied")
            and info["title_override"] is None
            else ""
        )

        if info.get("processed"):
            displayed_title = info["title_override"] or info["detected_title"] or "?"
            displayed_title += acoustid_marker + no_cover_marker
            displayed_artist = info["artist_override"]
            if displayed_artist is None:
                displayed_artist = info["detected_artist"] if info["detected_artist"] else "(empty)"
            displayed_format = f"{target_format} {PROCESSED_CHECK}" if (needs_conversion and info["convert"]) else info["format"]
            # Reflects what actually happened to THIS row, not "processed
            # means checked" - a row that was unchecked (kept as-is) still
            # ends up "processed" once the run reaches it, and showing it
            # checked made every row look selected after Apply even when
            # most weren't. An edit made AFTER Apply (fix_pending) reverts
            # this back to a plain checked box instead - the file's own
            # tags no longer match what the table shows, so "done" (✔)
            # would be actively misleading until the next Apply catches up.
            if info.get("fix_pending"):
                apply_box = CHECKED_BOX
            elif info.get("already_applied") and not info.get("apply_changes"):
                # Already had a cover + complete tags before this scan even
                # ran (search skipped - see track_tidy.py's
                # "already_applied") and still unchecked - distinct from
                # PROCESSED_CHECK/EMPTY_BOX so it's clear nothing needed to
                # happen here, rather than "this run processed it" or "the
                # user unchecked it". A user who explicitly (re)checks the
                # row still sees the normal PROCESSED_CHECK/EMPTY_BOX pair
                # once Apply runs - only the untouched default gets the
                # special mark, so the checkbox/select-all toggle keeps
                # actually doing something visible for these rows too.
                apply_box = ALREADY_APPLIED_MARK
            else:
                apply_box = PROCESSED_CHECK if info.get("apply_changes") else EMPTY_BOX
            return (apply_box, displayed_title, displayed_artist, displayed_format)

        apply = info["apply_changes"]

        if info["title_override"] is not None:
            displayed_title = info["title_override"]
        elif apply:
            displayed_title = (info["detected_title"] or "?") + acoustid_marker + no_cover_marker
        else:
            displayed_title = info["current_title"] or "(empty)"

        if info["artist_override"] is not None:
            displayed_artist = info["artist_override"]
        elif apply:
            displayed_artist = info["detected_artist"] if info["detected_artist"] else "(empty)"
        else:
            displayed_artist = info["current_artist"] or "(empty)"

        if info.get("already_applied") and not apply:
            apply_box = ALREADY_APPLIED_MARK
        else:
            apply_box = CHECKED_BOX if apply else EMPTY_BOX

        if needs_conversion:
            convert_box = CHECKED_BOX if info["convert"] else EMPTY_BOX
            format_text = target_format if info["convert"] else info["format"]
            displayed_format = f"{format_text} {convert_box}"
        else:
            displayed_format = info["format"]

        return (apply_box, displayed_title, displayed_artist, displayed_format)

    # --- Table interactions (sort, toggle, reorder) ---

    def _set_all_checked_state(self, checked):
        """Checks or unchecks 'apply_changes' for all *visible* rows not yet
        processed - rows currently hidden by a filter (e.g. "Only show
        tracks with no cover match") are left untouched, since the user
        never saw them to make that choice."""
        self.all_checked_state = checked
        visible_files = set(self.table.get_children())

        for info in self.scanned_plan:
            if not info.get("processed") and info["file"] in visible_files:
                info["apply_changes"] = checked
                self._refresh_row(info)

        self.table.heading("apply", text=CHECKED_BOX if checked else EMPTY_BOX)
        self._update_apply_button_label()

        # See _toggle_cell's identical call for why - checked state affects
        # has_usable_cover() (track_tidy.py), so a row can newly qualify
        # for (or drop out of) the "no cover match" filter here too.
        if self.no_cover_filter_var.get():
            self._apply_table_filter()

    def _toggle_all(self):
        """Also clears the "format" column - a track that isn't going to be
        touched at all shouldn't still have a pending conversion queued.
        Not symmetric: toggling "format" (_toggle_all_convert) only affects
        that column, since a conversion choice has no bearing on whether a
        track should be selected at all."""
        new_state = not self.all_checked_state
        self._set_all_checked_state(new_state)
        self._set_all_convert_state(new_state)

    def _sort_by(self, field):
        """
        3-state sort cycle on Title/Artist:
        1st click -> A to Z, 2nd click -> Z to A, 3rd click -> original (scan) order, then repeats.
        """
        if self.sort_state["column"] != field:
            new_state = 1  # new column: always restart at "A to Z"
        else:
            new_state = (self.sort_state["state"] + 1) % 3  # 1 -> 2 -> 0 -> 1 ...

        self.sort_state = {"column": field, "state": new_state}

        if new_state == 0:
            self.scanned_plan.sort(key=lambda info: info["original_order"])
        else:
            field_index = COLUMNS.index(field)

            def sort_key(info):
                return self._build_row_values(info)[field_index].lower()

            self.scanned_plan.sort(key=sort_key, reverse=(new_state == 2))

        self._reorder_table_rows()

        arrows = {0: "", 1: " ▲", 2: " ▼"}
        title_arrow = arrows[new_state] if field == "title" else ""
        artist_arrow = arrows[new_state] if field == "artist" else ""
        self.table.heading("title", text="Title" + title_arrow)
        self.table.heading("artist", text="Artist" + artist_arrow)

    def _set_all_convert_state(self, checked):
        """Sets 'convert' for all WAV files not yet processed - AIFF is
        already taggable/lossless and isn't offered a choice here (see
        _build_row_values), and every other non-MP3 format has no choice
        either (it MUST convert to be taggable at all, see open_audio_file),
        so those are left forced on regardless, same restriction as the
        per-row toggle in _handle_table_click."""
        self.all_convert_state = checked

        for info in self.scanned_plan:
            if not info.get("processed") and info["format"] == "WAV":
                info["convert"] = checked
                self.table.item(info["file"], values=self._build_row_values(info))

        self.table.heading("format", text=CHECKED_BOX if checked else EMPTY_BOX)

    def _toggle_all_convert(self):
        """Only affects the Format column - unlike _toggle_all (the "apply"
        header), this does NOT also touch which tracks are selected."""
        new_state = not self.all_convert_state
        self._set_all_convert_state(new_state)

    def _reorder_table_rows(self):
        """Reorders the table rows to match the current order of self.scanned_plan."""
        for new_index, info in enumerate(self.scanned_plan):
            self.table.move(info["file"], "", new_index)
            tag = "even_row" if new_index % 2 == 0 else "odd_row"
            self.table.item(info["file"], tags=(tag,))

    def _toggle_cell(self, event):
        """Single click on '✓' or 'Format' only: toggles the value. Cover
        zoom ("#0") is exempt from the "locked once processed" rule below -
        it writes straight to the file on disk and isn't part of the
        apply/checked state, so there's no reason it should stop working
        just because the row's already been processed."""
        item_id = self.table.identify_row(event.y)
        column_id = self.table.identify_column(event.x)  # "#0", "#1", "#2"...

        if not item_id:
            return

        info = next((i for i in self.scanned_plan if i["file"] == item_id), None)
        if not info:
            return

        if column_id == "#0":
            self._show_cover_zoom(info)
            return

        if info.get("processed"):
            return  # checkbox and format are locked once the file has been processed

        if column_id == f"#{COLUMNS.index('apply') + 1}":
            info["apply_changes"] = not info["apply_changes"]
            # Keep the Format checkbox in sync: unchecking a row also
            # unchecks its conversion (a track the user doesn't want
            # touched at all this run shouldn't still get converted), and
            # unchecking Format also unchecks the row below.
            if not info["apply_changes"] and info["format"] == "WAV":
                info["convert"] = False
            self._refresh_row(info)  # the image also changes based on current/suggested
            self._update_apply_button_label()
            # Checked state affects has_usable_cover() (see track_tidy.py) -
            # unchecking a row can put it back into (or checking it can take
            # it out of) the "no cover match" filter, so re-apply it right
            # away instead of leaving the row wherever it happened to be.
            if self.no_cover_filter_var.get():
                self._apply_table_filter()
        elif column_id == f"#{COLUMNS.index('format') + 1}":
            if info["format"] != "WAV":
                return  # AIFF has no checkbox (see _build_row_values); every other non-MP3 format has no choice - it MUST convert to be taggable at all
            info["convert"] = not info["convert"]
            if not info["convert"] and info["apply_changes"]:
                info["apply_changes"] = False
                self._refresh_row(info)
                self._update_apply_button_label()
                if self.no_cover_filter_var.get():
                    self._apply_table_filter()
            else:
                self.table.item(item_id, values=self._build_row_values(info))

    # --- Cover zoom ---

    def _resolve_full_path(self, info):
        """Absolute on-disk path for a scanned/processed row's file."""
        relative_path = info.get("final_path") or info["file"]
        base_folder = tagger.MUSIC_FOLDER
        if not os.path.isabs(base_folder):
            base_folder = os.path.join(tagger.app_base_dir(), base_folder)
        return os.path.abspath(os.path.join(base_folder, relative_path))

    def _apply_new_cover(self, info, new_bytes, parent=None):
        """Writes a new cover (or None to remove it) straight to the file
        on disk, immediately - independent of the Apply workflow. Updates
        the row's info dict, table, and journal on success. Shared by the
        cover zoom popup (_show_cover_zoom) and the right-click "Remove
        cover" action (_remove_cover_with_confirmation). Returns True on
        success, False on failure (an error dialog is already shown)."""
        full_path = self._resolve_full_path(info)
        if not os.path.exists(full_path):
            messagebox.showerror(
                "File not found", "Could not locate this file on disk anymore.", parent=parent or self.window,
            )
            return False
        try:
            tagger.write_tags(
                full_path, artist="", title="", cover_image=new_bytes,
                force_remove_if_missing=True, update_title=False, update_artist=False, update_cover=True,
            )
        except Exception as error:
            messagebox.showerror("Error", f"Could not update the cover: {error}", parent=parent or self.window)
            return False

        info["current_cover_bytes"] = new_bytes
        info["found_cover_image"] = new_bytes
        info["has_cover"] = new_bytes is not None
        info["cover_source"] = "Manual" if new_bytes else None
        self._refresh_row(info)
        self._append_to_journal(f"Cover {'updated' if new_bytes else 'removed'} for '{info['file']}'")
        return True

    def _confirm_remove_cover(self, parent):
        """Shared "Remove cover" confirmation - used by both the right-
        click menu (_remove_cover_with_confirmation) and the zoom popup's
        own button (_show_cover_zoom), which used to each hand-copy the
        exact same title/message."""
        return messagebox.askyesno(
            "Remove cover", "Remove the cover from this file?", parent=parent, default=messagebox.NO,
        )

    def _remove_cover_with_confirmation(self, info):
        """Right-click a cover thumbnail -> "Remove cover" - same action
        as the "Remove cover" button inside the zoom popup (_show_cover_
        zoom), just without opening it first."""
        if not self._confirm_remove_cover(self.window):
            return
        self._apply_new_cover(info, None)

    def _show_cover_zoom(self, info):
        """Click on the cover thumbnail: shows it full-size in a popup, with
        buttons to import a replacement or remove it - both write straight to
        the file on disk immediately, independent of the Apply workflow."""
        dialog = tk.Toplevel(self.window)
        self._style_toplevel(dialog)
        dialog.title(tagger.build_display_name(info.get("detected_artist"), info.get("detected_title")))
        dialog.resizable(False, False)
        dialog.transient(self.window)

        # Covers are typically already small (iTunes/SoundCloud artwork
        # rarely exceeds ~600px) - cap the popup size without upscaling
        # anything past its native resolution.
        max_size = (500, 500)

        image_label = ttk.Label(dialog)
        image_label.pack(padx=12, pady=(12, 6))

        button_row = ttk.Frame(dialog)
        button_row.pack(padx=12, pady=(0, 12), fill="x")
        import_button = ttk.Button(button_row, text="Import cover...", command=lambda: import_cover())
        remove_button = ttk.Button(button_row, text="Remove cover", command=lambda: remove_cover())
        import_button.pack(side="left", expand=True, fill="x", padx=(0, 5))
        remove_button.pack(side="left", expand=True, fill="x", padx=(5, 0))

        def current_cover_bytes():
            return tagger.effective_cover_bytes(info)

        def render():
            image_bytes = current_cover_bytes()
            if image_bytes:
                try:
                    display_image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
                    display_image.thumbnail(max_size, Image.LANCZOS)
                    image_tk = ImageTk.PhotoImage(display_image)
                    dialog.zoomed_image_ref = image_tk  # keep a reference, otherwise Tkinter clears it
                    image_label.configure(image=image_tk, text="")
                except Exception:
                    image_label.configure(image="", text="Couldn't read this cover image.")
            else:
                image_label.configure(image="", text="No cover.")
            remove_button.configure(state="normal" if image_bytes else "disabled")
            dialog.update_idletasks()
            self._center_dialog(dialog)

        def apply_new_cover(new_bytes):
            if self._apply_new_cover(info, new_bytes, parent=dialog):
                render()

        def import_cover():
            file_path = filedialog.askopenfilename(
                title="Choose a cover image",
                filetypes=[("Image files", "*.jpg *.jpeg *.png *.webp *.bmp"), ("All files", "*.*")],
            )
            if not file_path:
                return
            try:
                with open(file_path, "rb") as f:
                    raw_bytes = f.read()
                # Re-encode through PIL as JPEG - write_tags always embeds
                # image/jpeg, and this also rejects anything that isn't
                # actually a readable image before it touches the file.
                pil_image = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
                buffer = io.BytesIO()
                pil_image.save(buffer, format="JPEG", quality=90)
                jpeg_bytes = buffer.getvalue()
            except Exception as error:
                messagebox.showerror("Import failed", f"Could not read that image: {error}", parent=dialog)
                return
            apply_new_cover(jpeg_bytes)

        def remove_cover():
            if self._confirm_remove_cover(dialog):
                apply_new_cover(None)

        render()
        label_click_target = image_label
        label_click_target.bind("<Button-1>", lambda _event: dialog.destroy())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())

        self._center_dialog(dialog)
        dialog.focus_set()

    # --- Processing history window ---

    def _show_history_window(self):
        """Settings -> 'View processing history': every file ever actually
        processed (tagger.HISTORY_FILE), most recent first. Each entry shows
        its old file/artist/title on one (selectable) line, with the applied
        (new) file/artist/title indented right below it as a child row -
        children exist to show what changed, but only the old-info parent row
        can be selected, since Restore/Delete always act on the OLD info."""
        dialog = tk.Toplevel(self.window)
        self._style_toplevel(dialog)
        dialog.title("Processing history")
        dialog.geometry("840x660")
        dialog.transient(self.window)

        columns = ("file", "artist", "title", "cover", "converted")
        headings = {
            "file": "File", "artist": "Artist", "title": "Title",
            "cover": "Cover", "converted": "Converted",
        }
        widths = {"file": 200, "artist": 140, "title": 160, "cover": 55, "converted": 70}

        table_frame = ttk.Frame(dialog)
        table_frame.pack(fill="both", expand=True, padx=10, pady=(10, 5))

        tree = ttk.Treeview(
            table_frame, columns=columns, show="tree headings", height=10, selectmode="extended",
            style="Table.Treeview",
        )
        tree.heading("#0", text="When")
        tree.column("#0", width=140, anchor="w")
        for col in columns:
            tree.heading(col, text=headings[col])
            tree.column(col, width=widths[col], anchor="w")

        if self.theme_colors:
            tree.tag_configure(
                "odd_row", background=self.theme_colors["tree_odd_row"], foreground=self.theme_colors["tree_fg"],
            )
            tree.tag_configure(
                "even_row", background=self.theme_colors["tree_bg"], foreground=self.theme_colors["tree_fg"],
            )
        else:
            tree.tag_configure("odd_row", background="#e9e9e9", foreground="black")
            tree.tag_configure("even_row", background="white", foreground="black")

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="left", fill="y")

        search_frame = ttk.Frame(dialog)
        search_frame.pack(fill="x", padx=10, pady=(0, 5))
        history_filter_entry = ttk.Entry(search_frame)
        history_filter_entry.pack(fill="x", expand=True)
        self._bind_entry_context_menu(history_filter_entry)

        EMPTY_ROW_ID = "__history_empty_row__"
        entries_by_parent = {}
        group_children = {}  # group row id -> list of its track-level (old-info) row ids

        def matches_query(entry, query):
            haystack = " ".join(str(entry.get(key) or "") for key in (
                "old_file", "old_artist", "old_title", "new_file", "new_artist", "new_title",
            )).lower()
            return query in haystack

        def populate():
            for row in tree.get_children():
                tree.delete(row)
            entries_by_parent.clear()
            group_children.clear()

            if getattr(history_filter_entry, "placeholder_active", False):
                query = ""
            else:
                query = history_filter_entry.get().strip().lower()

            all_entries = list(reversed(tagger.load_history_entries()))
            entries = [e for e in all_entries if matches_query(e, query)] if query else all_entries

            if not entries:
                # The message goes in the "When" column (#0) - normally only
                # 140px wide, nowhere near enough to fit it without being
                # clipped at the column border. Widened here to span the
                # table's full width instead (the other columns are blank
                # anyway for this row), restored to normal once real
                # entries exist again.
                tree.column("#0", width=140 + sum(widths.values()))
                tree.insert(
                    "", "end", iid=EMPTY_ROW_ID,
                    text="No matching entries." if query else "No files have been processed yet.",
                    values=("", "", "", "", ""),
                )
                return

            tree.column("#0", width=140)

            # Entries from the same Apply run share a "run_id" (see
            # process_files/log_history_entry) and are already contiguous
            # here (load_history_entries is reverse-chronological) - group
            # them under one "Scan" row instead of listing each track as an
            # unrelated one-off entry. A run of just one track (or an entry
            # logged before run_id existed, which has none) stays at the top
            # level exactly as before - a group wrapper around a single
            # track would just be extra clicking for nothing.
            groups = []
            current_key = object()
            for entry in entries:
                key = entry.get("run_id") or entry.get("id")
                if key != current_key:
                    groups.append([])
                    current_key = key
                groups[-1].append(entry)

            row_index = 0
            for group_entries in groups:
                if len(group_entries) > 1:
                    first_timestamp = group_entries[0].get("timestamp", "")
                    try:
                        group_date_display = datetime.fromisoformat(first_timestamp).astimezone().strftime(
                            "%Y-%m-%d %H:%M"
                        )
                    except ValueError:
                        group_date_display = first_timestamp
                    group_tag = "even_row" if row_index % 2 == 0 else "odd_row"
                    container = tree.insert(
                        "", "end", text=f"Scan - {group_date_display} ({len(group_entries)} tracks)",
                        values=("", "", "", "", ""), tags=(group_tag,), open=False,
                    )
                    group_children[container] = []
                    row_index += 1
                else:
                    container = ""

                for entry in group_entries:
                    timestamp = entry.get("timestamp", "")
                    try:
                        date_display = datetime.fromisoformat(timestamp).astimezone().strftime("%Y-%m-%d %H:%M")
                    except ValueError:
                        date_display = timestamp

                    tag = "even_row" if row_index % 2 == 0 else "odd_row"
                    parent_id = tree.insert(
                        container, "end", text=date_display,
                        values=(
                            entry.get("old_file", ""), entry.get("old_artist") or "-", entry.get("old_title") or "-",
                            "", "",
                        ),
                        tags=(tag,),
                    )
                    tree.insert(
                        parent_id, "end", text="↳ Restored" if entry.get("restored") else "↳ Applied",
                        values=(
                            entry.get("new_file", ""), entry.get("new_artist") or "-", entry.get("new_title") or "-",
                            "Yes" if entry.get("cover_updated") else "No",
                            "Yes" if entry.get("converted") else "No",
                        ),
                        tags=(tag,),
                    )
                    tree.item(parent_id, open=False)
                    entries_by_parent[parent_id] = entry
                    if container:
                        group_children[container].append(parent_id)
                    row_index += 1

        def schedule_history_filter(event=None):
            """Debounces the search filter the same way the main table's does."""
            if getattr(dialog, "_filter_after_id", None):
                dialog.after_cancel(dialog._filter_after_id)
            dialog._filter_after_id = dialog.after(300, populate)

        history_filter_entry.bind("<KeyRelease>", schedule_history_filter)
        setup_placeholder(history_filter_entry, "Search history...", on_change=populate)

        def enforce_parent_only_selection(event=None):
            """Clicking (or ctrl/shift-clicking) a child 'Applied' row selects
            its parent instead - Restore/Delete always act on the OLD info,
            so only old-info rows are meant to be selectable. Clicking a
            'Scan - ...' group row selects every track inside it, so
            Restore/Delete can act on the whole scan at once."""
            current = tree.selection()
            corrected, seen = [], set()
            for item_id in current:
                if item_id in group_children:
                    for child_id in group_children[item_id]:
                        if child_id not in seen:
                            seen.add(child_id)
                            corrected.append(child_id)
                    continue
                target = item_id if item_id in entries_by_parent else tree.parent(item_id)
                if target and target not in seen:
                    seen.add(target)
                    corrected.append(target)
            if tuple(corrected) != current:
                tree.selection_set(corrected)

        def select_all(event=None):
            tree.selection_set(list(entries_by_parent.keys()))
            return "break"

        tree.bind("<<TreeviewSelect>>", enforce_parent_only_selection)
        tree.bind("<Control-a>", select_all)

        def restore_selected():
            selection = tree.selection()  # already parent-only, enforced above
            entries = [entries_by_parent[item_id] for item_id in selection if item_id in entries_by_parent]
            if not entries:
                messagebox.showinfo("Restore", "Select one or more entries first.", parent=dialog)
                return

            if len(entries) == 1:
                old_tags = tagger.build_display_name(
                    entries[0].get("old_artist") or "", entries[0].get("old_title") or ""
                ) or "(no tags)"
                prompt = f"Restore this file's tags and cover to:\n\n{old_tags}\n\nThis changes the file on disk right now."
            else:
                prompt = f"Restore {len(entries)} files to their previous tags and cover?\n\nThis changes the files on disk right now."

            if not messagebox.askyesno("Restore previous version(s)", prompt, parent=dialog, default=messagebox.NO):
                return

            def _log_restore(entry):
                tagger.log_action(
                    f"Restored: '{entry.get('new_file')}' -> '{entry.get('old_file')}' | "
                    f"Artist: '{entry.get('new_artist') or ''}' -> '{entry.get('old_artist') or ''}' | "
                    f"Title: '{entry.get('new_title') or ''}' -> '{entry.get('old_title') or ''}'"
                )

            successes, failures, restored_entries = 0, [], []
            # restore_history_entry already tried a bounded search of the
            # original folder tree - collect every entry it still couldn't
            # find WITHOUT prompting per file (a "Locate it manually?"
            # popup for each one, back to back, was the actual complaint -
            # one grouped question below instead, then only the unavoidable
            # per-file file-pickers if the user says yes).
            not_found = []
            for entry in entries:
                display_name = entry.get("new_file") or entry.get("old_file") or "the file"
                try:
                    tagger.restore_history_entry(entry, log=self._append_to_journal)
                    successes += 1
                    restored_entries.append(entry)
                    _log_restore(entry)
                except FileNotFoundError:
                    not_found.append((entry, display_name))
                except Exception as error:
                    failures.append((display_name, str(error)))

            if not_found:
                count = len(not_found)
                unit = "file" if count == 1 else "files"
                names_preview = "\n".join(f"- {name}" for _, name in not_found[:5])
                if count > 5:
                    names_preview += f"\n...and {count - 5} more"
                locate = messagebox.askyesno(
                    "Files not found",
                    f"{count} {unit} weren't found where {'it was' if count == 1 else 'they were'} "
                    f"originally processed - {'it' if count == 1 else 'they'} may have moved or been "
                    f"renamed:\n\n{names_preview}\n\n"
                    f"Locate {'it' if count == 1 else 'them, one at a time,'} manually?",
                    parent=dialog,
                )
                # Once the user locates ONE moved file, its folder is a
                # strong hint for the rest - a library reorganized into a
                # new folder usually moved everything together, not just
                # this one track. Auto-checks that folder (recursively,
                # same bounded search restore_history_entry itself already
                # does for the originally-logged folder) for each remaining
                # not-found entry's expected filename before bothering the
                # user with another picker for it. Kept on self (see
                # __init__), not a local, so this still helps even when the
                # user restores moved tracks one at a time across SEPARATE
                # Restore clicks, not just within one multi-select batch.
                for entry, display_name in not_found:
                    chosen = None
                    expected_name = os.path.basename(entry.get("new_file") or entry.get("old_file") or "")

                    if locate and self._history_restore_located_folder and expected_name:
                        auto_found = tagger._find_file_by_name(self._history_restore_located_folder, expected_name)
                        if auto_found:
                            chosen = auto_found
                            self._append_to_journal(
                                f"  Found '{display_name}' automatically in the same folder."
                            )
                    if chosen is None and locate:
                        # Asks for the FOLDER the library moved to, not the
                        # exact file - matches how this actually gets used
                        # (searched recursively for the expected filename,
                        # same as the auto-locate above and
                        # restore_history_entry's own bounded search), and
                        # is what a user reorganizing a whole library
                        # naturally has in mind ("it's over there now"),
                        # not one specific file's exact path.
                        picked_folder = filedialog.askdirectory(
                            title=f"Locate the folder containing '{display_name}'", parent=dialog,
                        )
                        if picked_folder:
                            self._history_restore_located_folder = picked_folder
                            chosen = tagger._find_file_by_name(picked_folder, expected_name) if expected_name else None
                            if chosen is None:
                                failures.append((display_name, f"Not found in '{picked_folder}'"))
                                continue
                    if chosen:
                        try:
                            tagger.restore_history_entry(entry, log=self._append_to_journal, override_path=chosen)
                            successes += 1
                            restored_entries.append(entry)
                            _log_restore(entry)
                            continue
                        except Exception as error:
                            failures.append((display_name, str(error)))
                            continue
                    failures.append((display_name, "File not found"))

            tagger.log_action(
                f"Restore from history: {successes}/{len(entries)} file(s) restored"
                + (f", {len(failures)} failed" if failures else "")
            )

            if restored_entries:
                tagger.mark_history_entries_restored(restored_entries)
                populate()

            if failures:
                detail = "\n".join(f"- {name}: {error}" for name, error in failures[:5])
                if len(failures) > 5:
                    detail += f"\n...and {len(failures) - 5} more"
                messagebox.showwarning(
                    "Restore finished with errors", f"{successes} restored, {len(failures)} failed:\n\n{detail}",
                    parent=dialog,
                )
            else:
                messagebox.showinfo(
                    "Restored",
                    f"{successes} file(s) restored." if successes > 1 else "The file's previous tags and cover have been restored.",
                    parent=dialog,
                )

        def delete_selected():
            selection = tree.selection()  # already parent-only, enforced above
            entries = [entries_by_parent[item_id] for item_id in selection if item_id in entries_by_parent]
            if not entries:
                messagebox.showinfo("Delete", "Select one or more entries first.", parent=dialog)
                return

            unit = "entry" if len(entries) == 1 else "entries"
            if not messagebox.askyesno(
                f"Delete {len(entries)} {unit}?",
                f"Delete {len(entries)} {unit} from the processing history?\n\n"
                "This only removes the log entry - it doesn't touch the audio file itself. "
                "This cannot be undone.",
                parent=dialog, default=messagebox.NO,
            ):
                return

            tagger.delete_history_entries(entries)
            populate()

        def show_context_menu(event):
            row_id = tree.identify_row(event.y)
            if not row_id:
                return
            if row_id in group_children:
                children = group_children[row_id]
                if not set(children).issubset(tree.selection()):
                    tree.selection_set(children)
            else:
                target = row_id if row_id in entries_by_parent else tree.parent(row_id)
                if target and target not in tree.selection():
                    tree.selection_set(target)

            menu = self._make_themed_menu(dialog)
            menu.add_command(label="Delete", command=delete_selected)
            menu.tk_popup(event.x_root, event.y_root)

        tree.bind("<Button-3>", show_context_menu)

        populate()

        button_frame = ttk.Frame(dialog)
        button_frame.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(button_frame, text="Restore selected", command=restore_selected).pack(side="left")

        self._center_dialog(dialog)

    # --- Cover hover badge ---

    def _get_hover_thumbnail(self, file_name):
        """Lazily builds (and caches) the cover thumbnail with a small magnifier
        badge, hinting that clicking it opens the full-size zoom popup."""
        if file_name in self.tk_images_hover:
            return self.tk_images_hover[file_name]

        base_image = self._thumbnail_pil_images.get(file_name)
        if base_image is None:
            return None

        badged = base_image.copy()
        draw = ImageDraw.Draw(badged, "RGBA")
        w, h = badged.size
        badge_d = max(14, min(w, h) // 2)  # badge diameter
        cx, cy = w - badge_d // 2 - 1, h - badge_d // 2 - 1

        # Dark circle behind the glass, for contrast against any cover color.
        draw.ellipse(
            [cx - badge_d / 2, cy - badge_d / 2, cx + badge_d / 2, cy + badge_d / 2],
            fill=(0, 0, 0, 170),
        )
        # Magnifying glass: lens (circle outline) + handle (short diagonal line).
        lens_r = badge_d * 0.26
        lens_cx, lens_cy = cx - badge_d * 0.10, cy - badge_d * 0.10
        draw.ellipse(
            [lens_cx - lens_r, lens_cy - lens_r, lens_cx + lens_r, lens_cy + lens_r],
            outline=(255, 255, 255, 235), width=2,
        )
        handle_dx, handle_dy = lens_r * 0.75, lens_r * 0.75
        draw.line(
            [
                (lens_cx + handle_dx, lens_cy + handle_dy),
                (cx + badge_d * 0.32, cy + badge_d * 0.32),
            ],
            fill=(255, 255, 255, 235), width=2,
        )

        image_tk = ImageTk.PhotoImage(badged)
        self.tk_images_hover[file_name] = image_tk
        return image_tk

    def _on_table_hover(self, event):
        """Shows a magnifier badge on the cover thumbnail under the cursor
        (only when it actually has a cover to zoom into), and restores the
        previous row's plain thumbnail once the cursor moves off it. Also
        shows a tooltip with the full Title/Artist text when it's too long
        to fit in its column."""
        row_id = self.table.identify_row(event.y)
        column_id = self.table.identify_column(event.x)

        hovering_cover = bool(
            row_id and column_id == "#0" and self._thumbnail_pil_images.get(row_id) is not None
        )
        target = row_id if hovering_cover else None

        if target != self._hovered_cover_row:
            if self._hovered_cover_row and self.table.exists(self._hovered_cover_row):
                self.table.item(self._hovered_cover_row, image=self.tk_images.get(self._hovered_cover_row) or "")

            self._hovered_cover_row = target

            if target:
                badged_image = self._get_hover_thumbnail(target)
                if badged_image:
                    self.table.item(target, image=badged_image)
                self.table.configure(cursor="hand2")
            else:
                self.table.configure(cursor="")

        self._update_cell_tooltip(row_id, column_id, event)

    def _on_table_leave(self, event):
        if self._hovered_cover_row and self.table.exists(self._hovered_cover_row):
            self.table.item(self._hovered_cover_row, image=self.tk_images.get(self._hovered_cover_row) or "")
        self._hovered_cover_row = None
        self.table.configure(cursor="")
        self._hide_tooltip()

    # --- Truncated-text tooltip ---

    def _update_cell_tooltip(self, row_id, column_id, event):
        """Shows a tooltip with the full text of a Title/Artist cell when
        it's too long to fit in its column, or - for the "apply" column,
        whose single-character marks (☑/☐/✔/-) are never too long to fit
        but aren't self-explanatory either - a plain-language explanation
        of what that row's specific mark currently means."""
        col_name = {"#1": "apply", "#2": "title", "#3": "artist"}.get(column_id) if row_id else None
        text = ""

        if col_name == "apply" and self.table.exists(row_id):
            info = next((i for i in self.scanned_plan if i["file"] == row_id), None)
            text = self._apply_mark_tooltip_text(info) if info else ""
        elif col_name and self.table.exists(row_id):
            values = self.table.item(row_id, "values")
            col_index = COLUMNS.index(col_name)
            text = values[col_index] if col_index < len(values) else ""
            column_width = self.table.column(col_name, "width")
            # A few px of slack for the cell's own internal padding.
            if not text or self._table_font.measure(text) <= column_width - 10:
                text = ""

        key = (row_id, col_name) if text else None
        if key == self._tooltip_key:
            self._position_tooltip(event)
            return

        self._hide_tooltip()
        self._tooltip_key = key
        if key:
            self._show_tooltip(text, event)

    def _apply_mark_tooltip_text(self, info):
        """Plain-language meaning of this row's current "apply" column
        mark - mirrors _build_row_values()'s own logic for which mark
        shows, so the two can never drift out of sync."""
        if info.get("processed"):
            if info.get("fix_pending"):
                return f"{CHECKED_BOX} Edited since Apply - will be re-applied next time."
            if info.get("already_applied") and not info.get("apply_changes"):
                return f"{ALREADY_APPLIED_MARK} Already had a cover and tags before this scan - nothing was applied."
            return (
                f"{PROCESSED_CHECK} Applied." if info.get("apply_changes")
                else f"{EMPTY_BOX} Not selected - kept as-is."
            )
        if info.get("already_applied") and not info.get("apply_changes"):
            return f"{ALREADY_APPLIED_MARK} Already has a cover and tags - nothing to apply. Click to select it anyway."
        return (
            f"{CHECKED_BOX} Selected for Apply - click to unselect." if info.get("apply_changes")
            else f"{EMPTY_BOX} Not selected - click to include it in Apply."
        )

    def _show_tooltip(self, text, event):
        self._tooltip_window = tk.Toplevel(self.window)
        self._tooltip_window.overrideredirect(True)
        self._tooltip_window.attributes("-topmost", True)
        # Pale "sticky note" yellow reads fine in light mode, but stands out
        # as a bright, jarring box against the dark UI - reuse the same
        # menu colors already used for right-click menus in dark mode
        # instead of leaving this the one un-themed popup in the app.
        if self.theme_colors:
            bg, fg = self.theme_colors["menu_bg"], self.theme_colors["menu_fg"]
        else:
            bg, fg = "#ffffe0", "#1a1a1a"
        ttk.Label(
            self._tooltip_window, text=text, background=bg, foreground=fg,
            relief="solid", borderwidth=1, padding=(6, 3),
        ).pack()
        self._position_tooltip(event)

    def _position_tooltip(self, event):
        if self._tooltip_window is not None:
            self._tooltip_window.geometry(f"+{event.x_root + 12}+{event.y_root + 18}")

    def _hide_tooltip(self):
        if self._tooltip_window is not None:
            self._tooltip_window.destroy()
            self._tooltip_window = None
        self._tooltip_key = None

    # --- Table editing & context menu ---

    def _toggle_cell_double_click(self, event):
        """Double-click on Title/Artist: opens editing (still editable even
        after processing). Deliberately delayed rather than fired right
        away - Tk fires <Double-1> on the 2nd click of every <Triple-1>
        sequence too, so editing immediately here would also flash open the
        rename box on every triple-click-to-play (see
        _toggle_cell_triple_click, which cancels this)."""
        item_id = self.table.identify_row(event.y)
        column_id = self.table.identify_column(event.x)

        if not item_id:
            return

        info = next((i for i in self.scanned_plan if i["file"] == item_id), None)
        if not info:
            return

        if column_id not in (
            f"#{COLUMNS.index('title') + 1}", f"#{COLUMNS.index('artist') + 1}",
        ):
            return

        if self._pending_double_click_after_id:
            self.window.after_cancel(self._pending_double_click_after_id)
        self._pending_double_click_after_id = self.window.after(
            300, lambda: self._edit_cell(item_id, info, "title" if column_id == f"#{COLUMNS.index('title') + 1}" else "artist", column_id),
        )

    def _toggle_cell_triple_click(self, event):
        """Triple-click on Title/Artist: plays the audio file in the
        default player - cancels the pending rename from the 2nd click of
        this same sequence first."""
        if self._pending_double_click_after_id:
            self.window.after_cancel(self._pending_double_click_after_id)
            self._pending_double_click_after_id = None

        item_id = self.table.identify_row(event.y)
        column_id = self.table.identify_column(event.x)

        if not item_id:
            return

        info = next((i for i in self.scanned_plan if i["file"] == item_id), None)
        if not info:
            return

        if column_id not in (
            f"#{COLUMNS.index('title') + 1}", f"#{COLUMNS.index('artist') + 1}",
        ):
            return

        self._play_audio_file(info)

    def _play_audio_file(self, info):
        """Launches this row's file in the default audio player."""
        full_path = self._resolve_full_path(info)
        if not os.path.exists(full_path):
            self._append_to_journal(f"Can't play, file not found: '{full_path}'")
            return
        try:
            open_with_default_app(full_path)
        except Exception as error:
            self._append_to_journal(f"Error opening file: {error}")

    def _select_all_rows(self, event=None):
        """Ctrl+A: selects every currently visible track row (skips the
        synthetic summary rows, if shown)."""
        summary_row_ids = (NO_COVER_SUMMARY_ROW_ID, SEARCH_RESULT_SUMMARY_ROW_ID)
        self.table.selection_set([
            item_id for item_id in self.table.get_children() if item_id not in summary_row_ids
        ])
        return "break"

    def _delete_selected_rows(self, event=None):
        """Removes the selected row(s) from the list only - never touches
        the actual file on disk. Ctrl+Z (_undo_last_action) brings them
        back at their original position."""
        selected_items = self.table.selection()
        if not selected_items:
            return

        for item_id in selected_items:
            self.tk_images.pop(item_id, None)
            self.tk_images_hover.pop(item_id, None)
            self._thumbnail_pil_images.pop(item_id, None)

        selected_set = set(selected_items)
        removed = [
            (index, info) for index, info in enumerate(self.scanned_plan) if info["file"] in selected_set
        ]
        self._undo_stack.append(("removal", removed))
        self.scanned_plan = [info for info in self.scanned_plan if info["file"] not in selected_set]

        def _after_fade():
            self._restripe_rows()
            self._update_apply_button_label()

        self._fade_out_and_delete_rows(selected_items, on_complete=_after_fade)

    def _undo_last_action(self, event=None):
        """Ctrl+Z: undoes the most recent removal (Delete/"Remove from
        list") or Title/Artist cell edit, whichever happened last."""
        if not self._undo_stack:
            return
        action_type, payload = self._undo_stack.pop()
        if action_type == "removal":
            self._undo_removal(payload)
        else:
            self._undo_edit(payload)

    def _undo_removal(self, removed):
        """Brings back removed row(s), each at its original position in the list."""
        for index, info in sorted(removed, key=lambda pair: pair[0]):
            self.scanned_plan.insert(min(index, len(self.scanned_plan)), info)
            # A fade-out from the just-undone delete may still be running
            # for this same row iid - finish it now so the insert below
            # never collides with an item that's technically still there.
            self._force_finish_row_fade(info["file"])
            image_tk = self._create_thumbnail(info)
            self.tk_images[info["file"]] = image_tk
            self.table.insert(
                "", "end", iid=info["file"],
                image=image_tk if image_tk else "",
                values=self._build_row_values(info),
            )

        visible_files = set(self.table.get_children())
        for info in self.scanned_plan:
            if info["file"] in visible_files:
                self.table.move(info["file"], "", "end")

        self._restripe_rows()
        self._update_apply_button_label()
        unit = "row" if len(removed) == 1 else "rows"
        self._append_to_journal(f"Restored {len(removed)} removed {unit}.")

    def _undo_edit(self, edit_record):
        """Reverts a committed Title/Artist cell edit back to whatever the
        field held right before that edit (its own override/manual-flag/
        fix_pending state at the time - not necessarily the original
        detected value, if edits were made back to back)."""
        info = edit_record["info"]
        if info not in self.scanned_plan:
            return  # the row was removed since (its own removal is a separate undo step)

        field = edit_record["field"]
        info[f"{field}_override"] = edit_record["old_override"]
        info[f"{field}_override_is_manual"] = edit_record["old_override_is_manual"]
        info["fix_pending"] = edit_record["old_fix_pending"]

        if self.table.exists(info["file"]):
            self.table.item(info["file"], values=self._build_row_values(info))
        self._append_to_journal(f"Undid the {field} edit on '{info['file']}'.")

    def _move_row(self, info, direction):
        """Moves a row up (direction=-1) or down (direction=+1) in scanned_plan,
        then re-syncs the table's display order to match - only reordering
        rows currently visible, so an active search filter isn't disturbed."""
        if info not in self.scanned_plan:
            return

        current_index = self.scanned_plan.index(info)
        new_index = current_index + direction
        if new_index < 0 or new_index >= len(self.scanned_plan):
            return

        visible_files = set(self.table.get_children())
        self.scanned_plan[current_index], self.scanned_plan[new_index] = (
            self.scanned_plan[new_index], self.scanned_plan[current_index],
        )

        for other_info in self.scanned_plan:
            if other_info["file"] in visible_files:
                self.table.move(other_info["file"], "", "end")

        self._restripe_rows()

    def _on_row_drag_start(self, event):
        """Button-1 press: remembers which row a drag (if any) would start
        from. A plain click that never moves to a different row just
        behaves as before (_toggle_cell etc.) - this only ever does
        something once _on_row_drag_motion sees real movement."""
        row_id = self.table.identify_row(event.y)
        info = next((i for i in self.scanned_plan if i["file"] == row_id), None) if row_id else None
        self._drag_row_id = row_id if info else None

    def _on_row_drag_motion(self, event):
        """Dragging a row onto another one reorders it there immediately -
        scanned_plan is the source of truth for order, so it's updated
        first and the table's visible rows are just re-synced to match
        (same approach as _move_row), skipping detached/filtered-out rows."""
        drag_row_id = getattr(self, "_drag_row_id", None)
        if not drag_row_id or not self.table.exists(drag_row_id):
            return

        target_row_id = self.table.identify_row(event.y)
        if not target_row_id or target_row_id == drag_row_id:
            return
        if target_row_id in (NO_COVER_SUMMARY_ROW_ID, SEARCH_RESULT_SUMMARY_ROW_ID):
            return

        drag_info = next((i for i in self.scanned_plan if i["file"] == drag_row_id), None)
        target_info = next((i for i in self.scanned_plan if i["file"] == target_row_id), None)
        if not drag_info or not target_info:
            return

        self.scanned_plan.remove(drag_info)
        self.scanned_plan.insert(self.scanned_plan.index(target_info), drag_info)

        visible_files = set(self.table.get_children())
        for info in self.scanned_plan:
            if info["file"] in visible_files:
                self.table.move(info["file"], "", "end")

        self._restripe_rows()

    def _on_row_drag_release(self, event):
        self._drag_row_id = None

    def _show_context_menu(self, event):
        """Right-click on a row: shows a small context menu (e.g. open file location)."""
        item_id = self.table.identify_row(event.y)
        if not item_id:
            return

        info = next((i for i in self.scanned_plan if i["file"] == item_id), None)
        if not info:
            return

        # Right-clicking a row that's already part of the current multi-
        # selection keeps that whole selection (so bulk actions apply to
        # all of it); right-clicking outside it collapses to just this row.
        if item_id not in self.table.selection():
            self.table.selection_set(item_id)

        selected_ids = self.table.selection()
        selected_infos = [i for i in self.scanned_plan if i["file"] in selected_ids] or [info]

        menu = self._make_themed_menu(self.window)
        rescan_label = "Rescan this track" if len(selected_infos) <= 1 else f"Rescan selected ({len(selected_infos)})"
        menu.add_command(label="Info", command=lambda: self._show_track_info(info))
        if self.table.identify_column(event.x) == "#0":
            cover_state = "normal" if tagger.effective_cover_bytes(info) else "disabled"
            menu.add_command(
                label="Remove cover", command=lambda: self._remove_cover_with_confirmation(info), state=cover_state,
            )
        menu.add_command(label=rescan_label, command=lambda: self._quick_rescan(selected_infos))
        fix_label = "Fix Artist/Title..." if len(selected_infos) <= 1 else f"Fix Artist/Title ({len(selected_infos)})..."
        menu.add_command(label=fix_label, command=lambda: self._show_fix_no_cover_dialog(selected_infos))
        menu.add_command(label="Open file location", command=lambda: self._open_file_location(info))
        menu.add_separator()
        menu.add_command(label="Move up", command=lambda: self._move_row(info, -1))
        menu.add_command(label="Move down", command=lambda: self._move_row(info, 1))
        menu.add_separator()
        menu.add_command(label="Report track...", command=lambda: self._report_track_menu_action(selected_infos, info))
        menu.add_separator()
        menu.add_command(label="Remove from list", command=self._delete_selected_rows)
        menu.tk_popup(event.x_root, event.y_root)

    def _open_file_location(self, info):
        """Opens Windows Explorer with the corresponding file selected."""
        relative_path = info.get("final_path") or info["file"]
        base_folder = tagger.MUSIC_FOLDER
        if not os.path.isabs(base_folder):
            base_folder = os.path.join(tagger.app_base_dir(), base_folder)

        full_path = os.path.abspath(os.path.join(base_folder, relative_path))

        if not os.path.exists(full_path):
            self._append_to_journal(f"Can't open location, file not found: '{full_path}'")
            return

        try:
            reveal_in_file_manager(full_path)
        except Exception as error:
            self._append_to_journal(f"Error opening file location: {error}")

    def _show_track_info(self, info):
        """Shows a read-only summary of everything known about this row -
        current vs. suggested tags (as one "Artist - Title" line each,
        matching how they're actually displayed everywhere else), cover
        status, format/conversion target, and any flags detected on the
        file (mention, unreleased, already applied)."""
        dialog = tk.Toplevel(self.window)
        self._style_toplevel(dialog)
        dialog.title("Track info")
        dialog.resizable(False, False)
        dialog.transient(self.window)
        dialog.grab_set()

        if info.get("processed"):
            cover_summary = "File already processed"
        elif info.get("found_cover_image"):
            size_kb = len(info["found_cover_image"]) // 1024
            cover_summary = f"Found via {info.get('cover_source') or '?'} ({size_kb} KB)"
        elif info.get("has_cover"):
            cover_summary = "No online match - existing cover kept"
        else:
            cover_summary = "No cover found"

        current_display = (
            tagger.build_display_name(info.get("current_artist"), info["current_title"])
            if info.get("current_title") else "(empty)"
        )

        suggested_artist = info["artist_override"] if info.get("artist_override") is not None else info.get("detected_artist")
        suggested_title = info["title_override"] if info.get("title_override") is not None else info.get("detected_title")
        if suggested_title:
            acoustid_marker = " 🎧" if info.get("acoustid_identified") and info["title_override"] is None else ""
            suggested_display = tagger.build_display_name(suggested_artist, suggested_title) + acoustid_marker
        else:
            suggested_display = "(none)"

        needs_conversion = info.get("format") not in ("MP3", "AIFF")
        if needs_conversion and info.get("convert"):
            target_format = (tagger._resolve_conversion_target(info["file"]) or "mp3").upper()
            format_display = f"{info.get('format') or '?'} → {target_format}"
        else:
            format_display = info.get("format") or "?"

        flags = []
        if info.get("mention_detected"):
            flags.append("Mention detected")
        if tagger.contains_unreleased_marker(info.get("file") or ""):
            flags.append("Unreleased")
        if info.get("already_applied"):
            flags.append("Already applied")

        rows = [
            ("File", info.get("file") or "?"),
            ("Format", format_display),
            ("Current tags", current_display),
            ("Suggested tags", suggested_display),
            ("Cover match", cover_summary),
            ("Apply changes", "Yes" if info.get("apply_changes") else "No"),
            ("Flags", ", ".join(flags) if flags else "(none)"),
        ]

        grid = ttk.Frame(dialog)
        grid.pack(padx=20, pady=15)
        for row_index, (label, value) in enumerate(rows):
            ttk.Label(grid, text=f"{label}:", font=("", 9, "bold")).grid(
                row=row_index, column=0, sticky="ne", padx=(0, 10), pady=2
            )
            ttk.Label(grid, text=str(value), wraplength=280, justify="left").grid(
                row=row_index, column=1, sticky="nw", pady=2
            )

        ttk.Button(dialog, text="Close", command=dialog.destroy).pack(pady=(0, 15))
        dialog.bind("<Escape>", lambda _event: dialog.destroy())

        self._center_dialog(dialog)

    def _report_track_menu_action(self, selected_infos, info):
        """Gate in front of _report_track() for the context menu: unlike
        "Rescan selected", reporting is deliberately NOT a bulk action -
        multiple simultaneous reports to the same Discord channel from one
        click looks like spam on the receiving end. If more than one row
        is selected, explains that instead of silently reporting only the
        right-clicked row (or all of them)."""
        if len(selected_infos) > 1:
            messagebox.showinfo(
                "Report one track at a time",
                "Reporting multiple tracks at once isn't supported, to avoid spamming the report "
                "channel - select just one track and report it, then repeat for the others.",
                parent=self.window,
            )
            return
        self._report_track(info)

    def _report_track(self, info):
        """Sends this row's info (file name, current/suggested tags, cover
        status...) to the developer via Discord, so problem tracks (e.g. no
        cover found) can be collected and fixed later. Tagged with the
        Windows username, so reports from different people using the app
        aren't all anonymous."""
        try:
            reporter_name = getpass.getuser()
        except Exception:
            reporter_name = ""

        def _send():
            success, reason = tagger.send_track_report(info, reporter_name=reporter_name)
            if success:
                tagger.log_action(f"Reported track: '{os.path.basename(info.get('file', ''))}'")
            self.message_queue.put(("report_sent", (success, reason)))

        self._run_in_background(_send)

    def _edit_cell(self, item_id, info, field, column_id):
        """Opens an input field directly on the cell to edit title/artist."""
        bbox = self.table.bbox(item_id, column_id)
        if not bbox:
            return
        x, y, width, height = bbox

        current_value = self._build_row_values(info)[COLUMNS.index(field)]

        edit_entry = ttk.Entry(self.table)
        edit_entry.insert(0, current_value)
        edit_entry.select_range(0, "end")
        edit_entry.place(x=x, y=y, width=width, height=height)
        edit_entry.focus()

        def confirm(_event=None):
            # Right-clicking to open the Cut/Copy/Paste menu below also fires
            # FocusOut (the popup steals focus) - guard against confirming twice.
            if not edit_entry.winfo_exists():
                return

            new_value = edit_entry.get().strip()
            new_override = new_value if new_value else None
            if new_override != info.get(f"{field}_override"):
                self._undo_stack.append(("edit", {
                    "info": info, "field": field,
                    "old_override": info.get(f"{field}_override"),
                    "old_override_is_manual": info.get(f"{field}_override_is_manual"),
                    "old_fix_pending": info.get("fix_pending"),
                }))
            info[f"{field}_override"] = new_override
            info[f"{field}_override_is_manual"] = bool(new_value)
            edit_entry.destroy()

            if info.get("processed"):
                info["fix_pending"] = True
                self._append_to_journal(
                    "Change pending — will be applied on the next click on 'Apply'."
                )
                # The progress bar still shows the PREVIOUS run's "Done" -
                # misleading now that there's a new pending change waiting
                # on the next Apply.
                self.progress_canvas.pack_forget()
                self._update_progress_bar(self.progress_canvas, 0, "")

            self.table.item(item_id, values=self._build_row_values(info))

        def undo_typing(_event=None):
            # ttk.Entry has no built-in undo (unlike tk.Text) - restores the
            # value it had when editing started, rather than the main
            # table's Ctrl+Z (self.table's own binding never fires while
            # this entry has focus - keyboard shortcuts don't propagate
            # from a child widget up to a parent's binding in Tk).
            edit_entry.delete(0, "end")
            edit_entry.insert(0, current_value)
            return "break"

        edit_entry.bind("<Return>", confirm)
        edit_entry.bind("<FocusOut>", confirm)
        edit_entry.bind("<Escape>", lambda e: edit_entry.destroy())
        edit_entry.bind("<Control-z>", undo_typing)
        self._bind_entry_context_menu(edit_entry)

    # --- Log / progress (thread-safe) ---

    def _append_to_journal(self, text):
        self.message_queue.put(("log", text))

    def _update_progress(self, index, total):
        self.message_queue.put(("progress", (index, total)))

    def _file_processed(self, identifier, success, reason=None):
        self.message_queue.put(("file_processed", (identifier, success, reason)))

    def _on_source_auth_error(self, source, message):
        self.message_queue.put(("auth_error", (source, message)))

    def _start_message_loop(self):
        try:
            while True:
                message_type, content = self.message_queue.get_nowait()

                if message_type == "log":
                    self.journal_text.configure(state="normal")
                    self.journal_text.insert("end", content + "\n")
                    self.journal_text.see("end")
                    self.journal_text.configure(state="disabled")

                elif message_type == "progress":
                    index, total = content
                    percentage = round((index / total) * 100) if total else 0
                    self._update_progress_bar(self.progress_canvas, index / total if total else 0, f"{percentage} %")

                elif message_type == "done":
                    cancelled = content
                    self.processing_in_progress = False
                    self._set_buttons_enabled(True)
                    self._update_progress_bar(
                        self.progress_canvas, 1.0 if not cancelled else 0, "Cancelled" if cancelled else "Done ✓",
                    )
                    if not cancelled:
                        if self._processing_failures:
                            self._show_processing_failures_dialog()
                        self._play_success_sound()

                elif message_type == "file_scanned":
                    # Buffered, not shown immediately - see
                    # _reveal_next_scan_row(), which pops these into the
                    # table no faster than SCAN_REVEAL_INTERVAL_MS apart.
                    self._pending_scan_reveals.append(content)

                elif message_type == "mention_added":
                    self.mention_counts[content] = self.mention_counts.get(content, 0) + 1
                    already_active = content in self.mentions_listbox.get(0, "end")

                    if re.fullmatch(r"\s*by\s*fuvi\s*clan\s*", content, re.IGNORECASE):
                        # FuviClan mentions are noise the user always wants removed -
                        # skip the Suggested step and activate them right away.
                        if not already_active:
                            self.mentions_listbox.insert("end", content)
                            self._refresh_all_detected_titles()
                    elif not already_active:
                        self._refresh_suggested_entry(content)

                elif message_type == "soundcloud_rate_limited":
                    if not self.soundcloud_rate_limit_warned:
                        self.soundcloud_rate_limit_warned = True
                        self._rate_limited_messages_this_scan.append(
                            "SoundCloud's request limit has been reached - no cover will be fetched from it "
                            "for the rest of this scan."
                        )
                        self._notify_rate_limited("SoundCloud")

                elif message_type == "itunes_rate_limited":
                    if not self.itunes_rate_limit_warned:
                        self.itunes_rate_limit_warned = True
                        self._rate_limited_messages_this_scan.append(
                            "iTunes' request limit has been reached - it'll be paused for "
                            f"{tagger.ITUNES_RATE_LIMIT_COOLDOWN_SECONDS}s."
                        )
                        self._notify_rate_limited("iTunes")

                elif message_type == "spotify_rate_limited":
                    # Logged only, no popup (unlike the other 3 sources
                    # below) - per request, this one was showing up too
                    # often to be worth interrupting the user for; Spotify
                    # is the last-resort source anyway (see USE_SPOTIFY),
                    # so the scan just keeps going on iTunes/SoundCloud
                    # without it.
                    if not self.spotify_rate_limit_warned:
                        self.spotify_rate_limit_warned = True
                        self._append_to_journal(
                            "Spotify's request limit has been reached - no cover will be fetched from it "
                            "for the rest of this scan."
                        )
                        self._notify_rate_limited("Spotify")

                elif message_type == "acoustid_rate_limited":
                    if not self.acoustid_rate_limit_warned:
                        self.acoustid_rate_limit_warned = True
                        self._rate_limited_messages_this_scan.append(
                            "AcoustID's request limit has been reached - it'll be paused for "
                            f"{tagger.ACOUSTID_RATE_LIMIT_COOLDOWN_SECONDS}s."
                        )
                        self._notify_rate_limited("AcoustID")

                elif message_type == "auth_error":
                    source, error_message = content
                    already_warned = self.source_auth_error_warned.get(source, False)
                    if not already_warned:
                        self.source_auth_error_warned[source] = True
                        messagebox.showwarning(
                            f"{source} authentication failed",
                            f"{source} is enabled as a cover source with credentials configured, but "
                            f"authentication failed:\n\n{error_message}\n\n"
                            f"No cover will come from {source} until this is fixed - check the "
                            f"credentials in Settings.",
                            parent=self.window,
                        )

                elif message_type == "source_health_checked":
                    broken_credentials, blocked_sources = content
                    if broken_credentials or blocked_sources:
                        paragraphs = []
                        if broken_credentials:
                            names = ", ".join(broken_credentials)
                            paragraphs.append(
                                f"{names} authentication failed - the shared app "
                                f"{'credential' if len(broken_credentials) == 1 else 'credentials'} may have "
                                "been revoked or expired."
                            )
                        if blocked_sources:
                            names = ", ".join(blocked_sources)
                            verb = "seems" if len(blocked_sources) == 1 else "seem"
                            paragraphs.append(
                                f"{names} {verb} unreachable even though you're online - a firewall or "
                                "network filter may be blocking it."
                            )
                        messagebox.showwarning(
                            "Cover source issue detected",
                            "\n\n".join(paragraphs) + "\n\nCover matching may be less reliable until this is fixed.",
                            parent=self.window,
                        )

                elif message_type == "scan_done":
                    # Not finalized right away - the backend scan may well
                    # have finished before every buffered row has been
                    # revealed at the paced 1/s rate (see
                    # _reveal_next_scan_row). Held here and finalized once
                    # the reveal queue actually catches up, so the summary/
                    # buttons don't jump ahead of what's still visibly
                    # trickling into the table.
                    self._pending_scan_done = content

                elif message_type == "extract_progress":
                    index, total = content
                    fraction = (index / total) if total else 0
                    self._update_progress_bar(self.extract_progress_canvas, fraction, f"{round(fraction * 100)} %")

                elif message_type == "extract_done":
                    folder, moved_count, removed_count, cancelled, error = content
                    self.extract_browse_button.configure(state="normal")
                    self.extract_button.configure(text="Extract", command=self._start_extraction, state="normal")
                    self.extract_reset_button.configure(state="normal")
                    self.extract_progress_canvas.pack_forget()
                    self._set_tabs_locked(False)

                    if error:
                        messagebox.showerror("Extraction error", error, parent=self.window)
                    elif cancelled:
                        messagebox.showinfo(
                            "Extraction cancelled",
                            f"Stopped early - {moved_count} file(s) extracted, "
                            f"{removed_count} empty folder(s) removed so far.",
                            parent=self.window,
                        )
                    else:
                        messagebox.showinfo(
                            "Extraction complete",
                            f"{moved_count} file(s) extracted, {removed_count} empty folder(s) removed.",
                            parent=self.window,
                        )
                        try:
                            open_with_default_app(folder)
                        except Exception:
                            pass

                elif message_type == "quality_scan_progress":
                    # Only the total is kept - the bar itself tracks how
                    # many rows have actually been REVEALED (see
                    # _add_quality_row), not how far the background
                    # analysis has gotten, since that runs far ahead of the
                    # paced 1/s reveal and would make the bar hit 100%
                    # while most rows are still trickling into the table.
                    _index, total = content
                    self._quality_scan_total = total

                elif message_type == "quality_scan_row":
                    # Not displayed the instant it's ready - see
                    # _reveal_next_quality_row(): queued here so the table
                    # reveals tracks no faster than SCAN_REVEAL_INTERVAL_MS
                    # apart, while the actual scan keeps running at full
                    # speed underneath, unaffected.
                    self._pending_quality_reveals.append(content)

                elif message_type == "quality_scan_done":
                    self._pending_quality_scan_done = content

                elif message_type == "quality_spectrogram_ready":
                    request_id, data, error = content
                    entry = self._quality_spectrogram_requests.pop(request_id, None)
                    if entry is not None:
                        dialog, canvas, status_label = entry
                        # The dialog may have been closed (Close button, or
                        # the whole app) while the background decode/FFT was
                        # still running - nothing left to update.
                        if dialog.winfo_exists():
                            if error or not data:
                                status_label.configure(text="Could not analyze this file's spectrogram.")
                            else:
                                status_label.pack_forget()
                                self._draw_quality_spectrogram(canvas, data)

                elif message_type == "internet_status":
                    is_online, is_startup_check = content
                    self._is_online = is_online
                    if is_online:
                        self.internet_status_label.configure(text="● Online", foreground="#2ecc71")
                    else:
                        self.internet_status_label.configure(text="● Offline", foreground="#e74c3c")
                        if is_startup_check:
                            messagebox.showwarning(
                                "No internet connection",
                                "No internet connection was detected.\n\nOnline cover search "
                                "(iTunes/SoundCloud) won't be available until your "
                                "connection is restored.",
                                parent=self.window,
                            )
                    self._refresh_tagger_buttons_for_connectivity()

                elif message_type == "fix_row_search_result":
                    self._apply_fix_row_search_result(content)

                elif message_type == "update_available":
                    latest_version, release_url, installer_url, expected_sha256 = content
                    self._offer_update(latest_version, release_url, installer_url, expected_sha256)

                elif message_type == "manual_update_check_result":
                    is_newer, latest_version, release_url, installer_url, expected_sha256 = content
                    self.check_update_button.configure(state="normal", text="Check for updates")

                    if is_newer:
                        self._offer_update(latest_version, release_url, installer_url, expected_sha256)
                    elif latest_version:
                        messagebox.showinfo(
                            "Up to date", f"You already have the latest version ({tagger.APP_VERSION}).",
                            parent=self.window,
                        )
                    else:
                        messagebox.showerror(
                            "Update check failed",
                            "Could not check for updates - check your internet connection and try again.",
                            parent=self.window,
                        )

                elif message_type == "update_download_progress":
                    downloaded, total = content
                    if total:
                        percent = downloaded / total * 100
                        self._update_progress_var.set(percent)
                        self._update_percent_label.configure(text=f"{percent:.0f}%")

                elif message_type == "update_download_done":
                    success, dest_path = content
                    self._finish_in_app_update(success, dest_path)

                elif message_type == "report_sent":
                    success, reason = content
                    if success:
                        self._append_to_journal("Track reported, thanks!")
                    elif reason == "http_error":
                        messagebox.showerror(
                            "Report failed", "Discord rejected the report - try again in a moment.",
                            parent=self.window,
                        )
                    else:
                        messagebox.showerror(
                            "Report failed", "Could not send the report - check your internet connection and try again.",
                            parent=self.window,
                        )

                elif message_type == "file_processed":
                    identifier, success, reason = content
                    if not success:
                        self._processing_failures.append((identifier, reason or "Unknown error"))
                    if self.table.exists(identifier):
                        info = next((i for i in self.scanned_plan if i["file"] == identifier), None)
                        if info:
                            self.table.item(identifier, values=self._build_row_values(info))

        except queue.Empty:
            pass

        self.window.after(100, self._start_message_loop)

    # --- Running the processing ---

    def _is_filter_active(self):
        """Whether the table is currently narrowed down by the search box
        and/or the no-cover checkbox - used to warn before Apply, since it
        always processes every scanned file regardless of what's hidden."""
        if self.no_cover_filter_var.get():
            return True
        if not getattr(self.table_filter_entry, "placeholder_active", False) and self.table_filter_entry.get().strip():
            return True
        return False

    def _start_processing(self):
        if self.processing_in_progress:
            return

        if not self.scanned_plan:
            messagebox.showwarning("No scan", "Please scan the files first before processing them.", parent=self.window)
            return

        # Only rows with something actually pending (checked for apply, or
        # marked to convert) - a fully-untouched row (unchecked, no
        # conversion queued) must stay untouched and selectable for a LATER
        # Apply run. process_files() marks every row it's given as
        # "processed" (locked) unconditionally, whether or not its tags
        # actually changed, so previously every unchecked row got swept up
        # and permanently locked out just because Apply ran at all.
        to_process = [
            i for i in self.scanned_plan
            if not i.get("processed") and (i.get("apply_changes") or i.get("convert"))
        ]
        fixes = [i for i in self.scanned_plan if i.get("processed") and i.get("fix_pending")]

        if not to_process and not fixes:
            messagebox.showinfo("Nothing to do", "No new file and no pending change.", parent=self.window)
            return

        confirmed = messagebox.askyesno(
            "Apply changes?",
            "This will overwrite the original artist/title/cover info for every "
            "selected track.\n\nThe original values are saved in the processing "
            "history and can be restored from there if needed.\n\nContinue?",
            parent=self.window,
        )
        if not confirmed:
            return

        if self._is_filter_active():
            visible_ids = set(self.table.get_children())
            hidden_count = sum(1 for i in to_process + fixes if i["file"] not in visible_ids)
            if hidden_count:
                confirmed = messagebox.askyesno(
                    "Filter active",
                    f"{hidden_count} track(s) are hidden by the current filter and will also be processed.\n\n"
                    "Continue?",
                    parent=self.window,
                )
                if not confirmed:
                    return

        to_convert = [i for i in to_process if i["format"] != "MP3" and i.get("convert")]
        mp3_count = sum(1 for i in to_convert if tagger._resolve_conversion_target(i["file"]) == "mp3")
        aiff_count = len(to_convert) - mp3_count
        if to_convert:
            parts = []
            if mp3_count:
                parts.append(f"{mp3_count} file(s) to MP3 (320 kbps, takes noticeably longer than just updating tags)")
            if aiff_count:
                parts.append(f"{aiff_count} file(s) to AIFF (lossless, quick)")
            confirmed = messagebox.askyesno(
                "Confirm conversion",
                "\n".join(parts) + ".\n\nContinue?",
                parent=self.window,
            )
            if not confirmed:
                return

        folder = self.folder_variable.get().strip()
        if folder:
            tagger.MUSIC_FOLDER = folder
        self._sync_mentions_to_remove()

        tagger.log_action(
            f"Apply started: {len(to_process)} file(s), {len(fixes)} pending fix(es) (folder: '{folder}')"
        )

        if not self.progress_canvas.winfo_ismapped():
            self.progress_canvas.pack(fill="x")
            self._adjust_window_height()

        self.journal_text.configure(state="normal")
        self.journal_text.delete("1.0", "end")
        self.journal_text.configure(state="disabled")

        self._update_progress_bar(self.progress_canvas, 0, "0 %")
        self.processing_in_progress = True
        self.cancel_requested.clear()
        self._processing_failures = []  # (identifier, reason) pairs - see _show_processing_failures_dialog

        self.browse_button.configure(state="disabled")
        self.scan_button.configure(state="disabled")
        self.reset_button.configure(state="disabled")
        self.apply_button.configure(text="Cancel", command=self._request_cancel, state="normal")

        thread = threading.Thread(
            target=self._run_processing,
            args=(to_process, fixes),
            daemon=True,
        )
        thread.start()

    def _request_cancel(self):
        self.cancel_requested.set()
        self._append_to_journal("Cancellation requested — stopping after the current file...")
        self.apply_button.configure(state="disabled")

    def _run_processing(self, plan, fixes):
        try:
            if plan:
                tagger.process_files(
                    plan,
                    log=self._append_to_journal,
                    on_progress=self._update_progress,
                    on_file_processed=self._file_processed,
                    should_cancel=self.cancel_requested.is_set,
                )

            for info in fixes:
                if self.cancel_requested.is_set():
                    self._append_to_journal("Processing cancelled.")
                    break

                final_artist = info["artist_override"] or info["detected_artist"]
                final_title = info["title_override"] or info["detected_title"]

                fix_error = None
                if final_artist and final_title:
                    try:
                        tagger.fix_title_artist(info, final_artist, final_title)
                        self._append_to_journal(f"Fix applied: '{final_artist} - {final_title}'")
                        info["fix_pending"] = False
                    except Exception as error:
                        fix_error = str(error)
                        self._append_to_journal(f"Error while applying fix: {error}")

                self.message_queue.put(("file_processed", (info["file"], fix_error is None, fix_error)))

        except Exception as error:
            self._append_to_journal(f"Unexpected error: {error}")
        finally:
            self.message_queue.put(("done", self.cancel_requested.is_set()))


if __name__ == "__main__":
    if not acquire_single_instance_lock():
        # Another instance is already running (e.g. the user double-clicked
        # the shortcut/exe more than once in quick succession) - just exit
        # quietly instead of opening a second window.
        sys.exit(0)

    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("KEVZ.TrackTidy.1")
    except Exception:
        pass  # not on Windows, or the call failed: harmless, just skip it

    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except ImportError:
        # tkinterdnd2 isn't installed: the app still works, just without drag-and-drop.
        root = tk.Tk()

    app = TaggerInterface(root)
    root.mainloop()
