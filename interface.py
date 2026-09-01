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
import os
import re
import socket
import sys
import tempfile
import threading
import time
import traceback
import queue
import webbrowser
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter import font as tkfont
from PIL import Image, ImageTk, ImageDraw

# When launched via pythonw.exe (no console), sys.stdout/stderr are None.
# Any leftover print() call would then crash with AttributeError. Redirect
# them to a no-op stream so nothing ever breaks silently because of this.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# Without this, Windows treats the whole process as "DPI-unaware" and
# compensates for a scaled display (125%/150%/200%...) by stretching the
# ENTIRE rendered window as a bitmap instead of letting it render natively
# at that resolution - the classic cause of a blurry Tkinter app on a
# scaled Windows display (see TaggerInterface.__init__ for the matching
# `tk scaling` fix, needed so fonts/widgets come out the right SIZE once
# Windows stops doing that automatic stretch for us).
#
# Must run before ANY Tk window is created (tk.Tk()/Toplevel()) - hence
# module level, not inside __main__ or TaggerInterface.__init__, since a
# script that only imports this module (e.g. tests/gui_smoke_test.py)
# creates its own tk.Tk() after importing it.
if sys.platform == "win32":
    import ctypes
    try:
        # Per-Monitor v2 (Windows 10 1703+) - correct behavior if the
        # window is ever moved between two monitors with different scaling.
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-Monitor v1 (Windows 8.1+)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()  # System-DPI-aware (Vista+)
            except Exception:
                pass

import track_tidy as tagger


# --- Cross-platform OS integration (Windows / macOS) ---

from ui_common import (
    open_with_default_app,
    resource_path,
    AUTO_THEME_LIGHT_START_HOUR,
    AUTO_THEME_DARK_START_HOUR,
    AUTO_THEME_RECHECK_INTERVAL_MS,
    TABLE_ROW_HEIGHT,
    DARK_COLORS,
    LIGHT_COLORS,
    INDICATOR_CHECKED_BG,
)


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

from tab_tagger import TaggerTabMixin
from tab_extractor import ExtractorTabMixin
from tab_quality import QualityTabMixin
from tab_settings import SettingsTabMixin


class TaggerInterface(TaggerTabMixin, ExtractorTabMixin, QualityTabMixin, SettingsTabMixin):
    def _apply_dpi_scaling(self):
        """Companion to the module-level SetProcessDpiAwarenessContext call
        above: declaring the process DPI-aware stops Windows from
        stretching the whole window as a blurry bitmap, but Tk itself still
        assumes 96 DPI (scaling = 1.333, i.e. 96/72 px-per-point) unless
        told otherwise - fonts sized in points (a positive integer, e.g.
        `("TkDefaultFont", 8)`, used throughout this app) would then render
        at their 96-DPI physical size on a monitor whose real pixel density
        is now fully exposed, i.e. visibly too small on any scaled display.
        Telling Tk the real px-per-point ratio up front (before any font or
        widget is built) makes those point-sized fonts come out the
        correct physical size again. No-op on macOS, which has no such
        two-step DPI model - AppKit already renders natively at the
        display's real backing resolution.

        WINDOW_WIDTH/window_scale's own sizing (right below this call, in
        __init__) doesn't need a matching adjustment here - it already
        computes itself fresh from winfo_screenwidth()/winfo_screenheight(),
        which return the REAL physical pixel resolution once the process is
        DPI-aware, same as before this fix on an unscaled (100%) display.
        """
        if sys.platform != "win32":
            return
        try:
            dpi = ctypes.windll.user32.GetDpiForWindow(self.window.winfo_id())
            self.window.tk.call("tk", "scaling", dpi / 72)
        except Exception:
            pass

    def __init__(self, window):
        self.window = window
        self._apply_dpi_scaling()
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

        self.theme_colors = None  # set to DARK_COLORS/LIGHT_COLORS by the first _apply_theme() call below
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
        self._setup_quality_drag_and_drop()
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
            resolved = self._resolve_theme_choice("auto")
            # _apply_theme does a full, non-cheap rebuild (every ttk style
            # plus several hand-drawn PhotoImages, including re-reading two
            # PNGs off disk) - skip it entirely on the far more common case
            # where this tick's resolved theme is the same one already
            # showing, rather than redoing all of that for nothing every
            # AUTO_THEME_RECHECK_INTERVAL_MS.
            if (self.theme_colors is DARK_COLORS) != (resolved == "dark"):
                self._apply_theme(resolved)
            self._schedule_auto_theme_recheck()

        self.window.after(AUTO_THEME_RECHECK_INTERVAL_MS, _recheck)

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
        colors = DARK_COLORS if dark else LIGHT_COLORS
        box_bg, box_border = colors["entry_bg"], colors["border"]
        unchecked_photo = self._build_checkbox_indicator_photo(box_bg, box_border, checked=False)
        checked_photo = self._build_checkbox_indicator_photo(box_bg, box_border, checked=True)
        self._checkbox_indicator_photos[theme_key] = (unchecked_photo, checked_photo)
        style.element_create(element_name, "image", unchecked_photo, ("selected", checked_photo))

    def _apply_theme(self, choice):
        dark = choice == "dark"
        colors = DARK_COLORS if dark else LIGHT_COLORS
        style = ttk.Style()
        style.theme_use("clam")

        # macOS's native "aqua" Notebook.tab is a compiled, natively-drawn
        # element (not the generic box-model one every other theme uses) -
        # it ignores style overrides the same way its Radiobutton/Scrollbar
        # elements do (see below), and centers the tab row instead of
        # packing it against the left edge. Borrowing clam's generic
        # tab/client elements - same "from clam" trick as those - swaps in
        # standard left-to-right box layout on macOS specifically, matching
        # every other platform/theme (clam is already what dark mode uses
        # everywhere, so this is a no-op there).
        if sys.platform == "darwin":
            for element_name, source_element in (
                ("Uniform.Notebook.tab", "Notebook.tab"),
                ("Uniform.Notebook.client", "Notebook.client"),
            ):
                if element_name not in style.element_names():
                    style.element_create(element_name, "from", "clam", source_element)
            style.layout("TNotebook", [("Uniform.Notebook.client", {"sticky": "nswe"})])
            style.layout("TNotebook.Tab", [
                ("Uniform.Notebook.tab", {"sticky": "nswe", "children": [
                    ("Notebook.padding", {"side": "top", "sticky": "nswe", "children": [
                        ("Notebook.focus", {"side": "top", "sticky": "nswe", "children": [
                            ("Notebook.label", {"side": "top", "sticky": ""}),
                        ]}),
                    ]}),
                ]}),
            ])

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
        indicator_bg, indicator_border = colors["entry_bg"], colors["border"]
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
        scrollbar_colors = dict(
            thumb=colors["border"], trough=colors["tree_bg"], arrow=colors["fg"], active_thumb=colors["select_bg"],
        )
        for scrollbar_style in ("TScrollbar", "Vertical.TScrollbar", "Horizontal.TScrollbar"):
            style.configure(
                scrollbar_style,
                background=scrollbar_colors["thumb"], troughcolor=scrollbar_colors["trough"],
                bordercolor=scrollbar_colors["trough"], arrowcolor=scrollbar_colors["arrow"],
                lightcolor=scrollbar_colors["thumb"], darkcolor=scrollbar_colors["thumb"],
                relief="flat", borderwidth=1,
            )
            style.map(scrollbar_style, background=[("active", scrollbar_colors["active_thumb"])])

        # Applied identically for both themes - only the values inside
        # `colors` (DARK_COLORS or LIGHT_COLORS) differ, so light and dark
        # always place every control at the exact same position/size.
        style.configure(".", background=colors["bg"], foreground=colors["fg"])
        style.configure("TFrame", background=colors["bg"])
        style.configure("TLabel", background=colors["bg"], foreground=colors["fg"])
        # Plain TLabel bakes in ~2px of padding on each side (from clam's
        # Label.border/Label.padding wrapper elements) that the widget's own
        # "padding" option can't override - invisible for a single label,
        # but it doubled up as a visible extra gap ("Developed by " | "KEVZ")
        # when two labels sit side by side to make only the second one
        # clickable. Stripping the layout down to the bare label element
        # removes that baked-in padding entirely.
        style.layout("Credit.TLabel", [("Label.label", {"sticky": "nswe"})])
        style.configure("Credit.TLabel", background=colors["bg"])
        # No border at all - panels are set apart from the plain window
        # background by color contrast (entry_bg vs bg) rather than a
        # drawn line, per the "no old-Windows bevels" design.
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
            # the old native theme's compact OS metrics - trimmed down so
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
            # Matching it to the surrounding frame background instead
            # blends the unavoidable 1px away entirely.
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

        for entry in (self.new_mention_entry, self.table_filter_entry):
            entry.normal_color = colors["entry_fg"]
            if not getattr(entry, "placeholder_active", False):
                entry.configure(foreground=entry.normal_color)

        muted_fg = colors["muted_fg"]
        self.dev_credit_label.configure(foreground=muted_fg)
        self.kevz_credit_label.configure(foreground=muted_fg)
        self.legal_text_label.configure(foreground=muted_fg)

        # Matches the table's own background (same "even_row" tag color
        # used above) so the hint blends into the empty table instead of
        # looking like a separate panel dropped on top of it.
        empty_state_bg = colors["tree_bg"]
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

        def _run_check():
            broken_credentials = tagger.check_source_credentials(log=self._append_to_journal)
            is_online = tagger.check_internet_connection()
            blocked_sources = tagger.check_restrictive_firewall() if is_online else []
            # Saved only AFTER the checks actually ran - same reasoning as
            # _notify_new_install_on_startup just above: saving this
            # beforehand would consume the 24h cooldown even if the app
            # was offline at launch or this thread died mid-check, silently
            # skipping a retry until the next day for no reason.
            tagger.save_setting("last_source_health_check", time.time())
            self.message_queue.put(("source_health_checked", (broken_credentials, blocked_sources)))

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
        self.quality_tab = quality_tab
        self.notebook.add(tagger_tab, text="Tagger")
        self.notebook.add(extractor_tab, text="Extractor")
        self.notebook.add(quality_tab, text="Quality")
        self.notebook.add(soundcloud_tab, text="Settings")
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self._build_tagger_tab(tagger_tab)
        self._build_extractor_tab(extractor_tab)
        self._build_quality_tab(quality_tab)
        self._build_settings_tab(soundcloud_tab)

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

    # --- Quality tab actions ---

    # Plain "●" per verdict, colored via the matching Treeview tag - see the
    # tag_configure comment in _build_interface for why not a colored emoji.
    QUALITY_VERDICT_TAG = {
        tagger.QUALITY_GREEN: "verdict_green",
        tagger.QUALITY_ORANGE: "verdict_orange",
        tagger.QUALITY_RED: "verdict_red",
    }

    # Verdict rank per sort state: state 1 puts red on top (worst-first),
    # state 2 puts green on top (best-first); a row with no recognized
    # verdict tag (the "❓" rows) always sorts last in either direction.
    _QUALITY_SORT_RANKS = {
        1: {"verdict_red": 0, "verdict_orange": 1, "verdict_green": 2},
        2: {"verdict_red": 2, "verdict_orange": 1, "verdict_green": 0},
    }

    # Canvas geometry for the spectrogram dialog - a bit larger than the
    # single-curve chart it replaced, to fit a real plot plus a dB color
    # legend bar (like a dedicated spectrogram viewer, e.g. Spek).
    QUALITY_SPECTROGRAM_CANVAS_W = 780
    QUALITY_SPECTROGRAM_CANVAS_H = 420
    QUALITY_SPECTROGRAM_MARGIN = (55, 95, 10, 30)  # left, right, top, bottom
    QUALITY_SPECTROGRAM_LEGEND_W = 18
    QUALITY_SPECTROGRAM_LEGEND_GAP = 20

    # --- Folder / mention actions ---

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

    # --- Scan ---

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

    # --- Truncated-text tooltip ---

    def _show_tooltip(self, text, event):
        # A missed <Leave> (fast mouse movement, focus lost mid-hover) would
        # otherwise overwrite self._tooltip_window without ever destroying
        # the previous one, leaking an orphaned Toplevel.
        self._hide_tooltip()
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
                    folder, moved_count, removed_count, failed_count, cancelled, error = content
                    self.extract_browse_button.configure(state="normal")
                    self.extract_button.configure(text="Extract", command=self._start_extraction, state="normal")
                    self.extract_reset_button.configure(state="normal")
                    self._set_tabs_locked(False)

                    if error:
                        self.extract_progress_canvas.pack_forget()
                        messagebox.showerror("Extraction error", error, parent=self.window)
                    elif cancelled:
                        # Same "leave the bar up with a final label" pattern
                        # as Tagger's own progress_canvas (see "done" above) -
                        # a popup on top of that was a redundant extra click.
                        self._update_progress_bar(
                            self.extract_progress_canvas, 0,
                            f"Cancelled - {moved_count} file(s) extracted, {removed_count} folder(s) removed",
                        )
                    else:
                        self._update_progress_bar(
                            self.extract_progress_canvas, 1.0,
                            f"Done ✓ - {moved_count} file(s) extracted, {removed_count} folder(s) removed",
                        )
                        # Opening the destination folder right here IS the
                        # confirmation - no extra popup needed on top of it.
                        try:
                            open_with_default_app(folder)
                        except Exception:
                            pass

                    # A file the OS refused to move (permission error, open
                    # in another program...) used to only ever show up in
                    # the log, which is hidden by default - easy to miss and
                    # looks like the file was silently ignored. Same
                    # "surface it, don't just log it" fix as Tagger's own
                    # _show_processing_failures_dialog, shown regardless of
                    # error/cancelled state since some files can still fail
                    # even on an otherwise-successful or cancelled run.
                    if failed_count:
                        unit = "file" if failed_count == 1 else "files"
                        messagebox.showwarning(
                            "Some files could not be moved",
                            f"{failed_count} {unit} could not be moved - likely in use by another "
                            "program, or a permissions issue. See the log for details.",
                            parent=self.window,
                        )

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

                    if is_newer:
                        self.check_update_button.configure(state="normal", text="Check for updates")
                        self._offer_update(latest_version, release_url, installer_url, expected_sha256)
                    elif latest_version:
                        # Transient label on the button itself instead of a
                        # popup to click through - "up to date" isn't
                        # actionable, just a flash of confirmation.
                        self.check_update_button.configure(state="disabled", text="Up to date ✓")
                        self.window.after(
                            2000, lambda: self.check_update_button.configure(state="normal", text="Check for updates"),
                        )
                    else:
                        self.check_update_button.configure(state="normal", text="Check for updates")
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
        except Exception:
            # A bug in any single message handler above must not silently
            # kill this loop forever (progress bars/journal frozen, Cancel/
            # Done never resolving, with nothing shown to the user) - report
            # it the same way an uncaught UI-callback exception is, and keep
            # polling regardless via the `finally` below.
            self._report_crash(*sys.exc_info(), context="message_loop")
        finally:
            self.window.after(100, self._start_message_loop)

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
