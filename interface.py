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
import sys
import subprocess
import tempfile
import threading
import queue
import winsound
import webbrowser
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
from tkinter import font as tkfont
from PIL import Image, ImageTk, ImageDraw

# When launched via pythonw.exe (no console), sys.stdout/stderr are None.
# Any leftover print() call would then crash with AttributeError. Redirect
# them to a no-op stream so nothing ever breaks silently because of this.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import track_tidy as tagger


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

THUMBNAIL_SIZE = (44, 44)
TABLE_ROW_HEIGHT = 48
# Widened from the original 500px so the bigger thumbnails and wider
# Title/Artist columns actually have room to breathe.
WINDOW_WIDTH = 620

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
        self.base_title = "Track Tidy"
        self.window.title(self.base_title)
        icon_path = resource_path("track-tidy_icon.ico")
        icon_png_path = resource_path("track-tidy_icon.png")

        if os.path.exists(icon_path):
            self.window.iconbitmap(icon_path)
        if os.path.exists(icon_png_path):
            self._icon_image_ref = ImageTk.PhotoImage(file=icon_png_path)  # keep a reference
            self.window.iconphoto(True, self._icon_image_ref)

        self.window.geometry(f"{WINDOW_WIDTH}x650")
        self.window.resizable(False, False)  # prevents fullscreen / resizing

        self.message_queue = queue.Queue()
        self.processing_in_progress = False
        self.cancel_requested = threading.Event()
        self.scanned_plan = []
        self.tk_images = {}  # keeps a reference to PhotoImages (otherwise Tkinter clears them)
        self.tk_images_hover = {}  # same thumbnails, with a magnifier badge - built lazily on first hover
        self._thumbnail_pil_images = {}  # pre-PhotoImage PIL images, so the hover badge can be composited cheaply
        self._fix_dialog_rows = {}  # file -> row widgets, while the "fix no-cover tracks" dialog is open
        self._hovered_cover_row = None
        self._tooltip_window = None
        self._tooltip_key = None
        self._pending_double_click_after_id = None
        self._removal_undo_stack = []  # list of [(original_index, info), ...] per "Remove from list" action
        self._drag_row_id = None  # row being dragged to reorder, if any
        self._drag_moved = False
        self._table_font = tkfont.nametofont("TkDefaultFont")
        self.soundcloud_rate_limit_warned = False
        self.mention_counts = {}  # raw mention text -> number of times seen

        self._native_theme = ttk.Style().theme_use()  # so "light" can restore it later
        self.theme_colors = None  # None while light/native; DARK_COLORS once dark is applied
        saved_theme = tagger.load_settings().get("theme", "light")
        if saved_theme not in ("light", "dark"):
            saved_theme = "light"  # e.g. an old "system" preference from before that option existed
        self.theme_var = tk.StringVar(value=saved_theme)

        saved_settings = tagger.load_settings()
        self.use_itunes_var = tk.BooleanVar(value=saved_settings.get("use_itunes", True))
        self.use_spotify_var = tk.BooleanVar(value=saved_settings.get("use_spotify", False))
        self.use_soundcloud_var = tk.BooleanVar(value=saved_settings.get("use_soundcloud", True))
        self.auto_convert_var = tk.BooleanVar(value=saved_settings.get("auto_convert_mp3", True))
        self.show_log_var = tk.BooleanVar(value=saved_settings.get("show_log_section", False))
        self._tagger_resize_pending = False
        tagger.USE_ITUNES = self.use_itunes_var.get()
        tagger.USE_SPOTIFY = self.use_spotify_var.get()
        tagger.USE_SOUNDCLOUD = self.use_soundcloud_var.get()
        tagger.AUTO_CONVERT_MP3 = self.auto_convert_var.get()

        self._build_interface()
        self._setup_drag_and_drop()
        self._adjust_window_height()
        self._apply_theme(self.theme_var.get())
        # Warm-up pass: the very first paint (before the window is ever
        # actually mapped) computes native-theme widget metrics a few
        # pixels too small - re-applying the theme once the window is
        # really up fixes it, but doing this synchronously here is too
        # early (mainloop() hasn't started yet), so it's deferred.
        self.window.after(100, self._rewarm_theme)
        self._start_message_loop()
        self._check_cover_source_credentials_on_startup()
        self._check_for_update_on_startup()
        self._check_internet_connection(is_startup_check=True)
        self._notify_new_install_on_startup()

    # --- Theme & dialog helpers ---

    def _rewarm_theme(self, step=0):
        """See the comment at the __init__ call site: fixes the native
        theme's widget metrics being subtly wrong on the very first paint.
        Each step is a separate event-loop turn (via after()) rather than
        back-to-back calls - ttk/Windows only recomputes native-theme
        metrics correctly with a real idle turn between theme switches."""
        steps = ("light", "dark", self.theme_var.get())
        self._apply_theme(steps[step])
        if step + 1 < len(steps):
            self.window.after(50, lambda: self._rewarm_theme(step + 1))
        else:
            self._adjust_window_height()
            self.window.deiconify()

    def _on_theme_changed(self):
        choice = self.theme_var.get()
        self._apply_theme(choice)
        tagger.save_setting("theme", choice)

    def _on_use_itunes_changed(self):
        enabled = self.use_itunes_var.get()
        if enabled and self.use_spotify_var.get():
            messagebox.showinfo(
                "iTunes and Spotify are exclusive",
                "iTunes and Spotify can't both be enabled as cover sources at "
                "the same time.\n\nTurn off Spotify first if you want to use iTunes.",
                parent=self.window,
            )
            self.use_itunes_var.set(False)
            return
        tagger.USE_ITUNES = enabled
        tagger.save_setting("use_itunes", enabled)

    def _on_use_spotify_changed(self):
        enabled = self.use_spotify_var.get()
        if enabled and self.use_itunes_var.get():
            # iTunes and Spotify are exclusive - Spotify silently wins here,
            # since checking it is the more deliberate action (iTunes is the
            # default already on, so unchecking it would take an extra step
            # otherwise). Re-checking iTunes afterward is what explains the
            # exclusivity, in _on_use_itunes_changed().
            self.use_itunes_var.set(False)
            tagger.USE_ITUNES = False
            tagger.save_setting("use_itunes", False)
        tagger.USE_SPOTIFY = enabled
        tagger.save_setting("use_spotify", enabled)

    def _on_use_soundcloud_changed(self):
        enabled = self.use_soundcloud_var.get()
        tagger.USE_SOUNDCLOUD = enabled
        tagger.save_setting("use_soundcloud", enabled)

    def _on_auto_convert_changed(self):
        enabled = self.auto_convert_var.get()
        if not enabled:
            messagebox.showwarning(
                "Convert to MP3 disabled",
                "WAV files will now be ignored when scanning - they can't be "
                "tagged without converting to MP3 first.",
                parent=self.window,
            )
        tagger.AUTO_CONVERT_MP3 = enabled
        tagger.save_setting("auto_convert_mp3", enabled)

    def _on_show_log_changed(self):
        enabled = self.show_log_var.get()
        tagger.save_setting("show_log_section", enabled)

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
            self._adjust_window_height()

    def _style_toplevel(self, dialog):
        """Applies the current theme's background (and title bar) to a dialog
        window (its ttk children already follow the active ttk style
        automatically)."""
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

    def _sync_mentions_to_remove(self):
        """Pushes the current "To remove" listbox contents to the tagger
        module - a Tk widget read, so it must happen on the main thread,
        before handing off to any background scan/rescan thread that reads
        tagger.MENTIONS_TO_REMOVE."""
        tagger.MENTIONS_TO_REMOVE = list(self.mentions_listbox.get(0, "end"))

    def _center_dialog(self, dialog):
        """Centers a dialog over the main window, clamped to stay fully
        on-screen - a dialog wider/taller than the main window (e.g. the
        history table) would otherwise end up centered partly off-screen."""
        dialog.update_idletasks()
        x = self.window.winfo_x() + (self.window.winfo_width() - dialog.winfo_width()) // 2
        y = self.window.winfo_y() + (self.window.winfo_height() - dialog.winfo_height()) // 2
        x = max(0, min(x, dialog.winfo_screenwidth() - dialog.winfo_width()))
        y = max(0, min(y, dialog.winfo_screenheight() - dialog.winfo_height()))
        dialog.geometry(f"+{x}+{y}")

    def _bind_entry_context_menu(self, entry, readonly=False):
        """Adds a right-click Cut/Copy/Paste/Select All menu to an Entry -
        unlike native Windows edit controls, Tk Entry widgets don't get one
        for free."""
        def show_menu(event):
            menu = tk.Menu(entry, tearoff=0)
            if self.theme_colors:
                menu.configure(
                    bg=self.theme_colors["menu_bg"], fg=self.theme_colors["menu_fg"],
                    activebackground=self.theme_colors["select_bg"], activeforeground=self.theme_colors["select_fg"],
                )
            if not readonly:
                menu.add_command(label="Cut", command=lambda: entry.event_generate("<<Cut>>"))
            menu.add_command(label="Copy", command=lambda: entry.event_generate("<<Copy>>"))
            if not readonly:
                menu.add_command(label="Paste", command=lambda: entry.event_generate("<<Paste>>"))
            menu.add_separator()
            menu.add_command(label="Select All", command=lambda: entry.select_range(0, "end"))
            menu.tk_popup(event.x_root, event.y_root)

        entry.bind("<Button-3>", show_menu)

    def _build_folder_icon_photo(self, size=16, color="#8a8a8a"):
        """Small flat folder glyph shown to the left of the parent-folder
        path - drawn as a raster image (like the checkbox indicator) rather
        than a Unicode folder emoji, so it's a plain solid color instead of
        an uncontrollable colored glyph that wouldn't match the theme."""
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle([size * 0.06, size * 0.19, size * 0.44, size * 0.31], fill=color)
        draw.rectangle([size * 0.06, size * 0.31, size * 0.94, size * 0.81], fill=color)
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
            style.configure("TNotebook", background=colors["bg"], borderwidth=0)
            style.configure(
                "TNotebook.Tab", background=colors["entry_bg"], foreground=colors["fg"],
                bordercolor=colors["border"],
            )
            style.map("TNotebook.Tab", background=[("selected", colors["bg"])])
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
                bordercolor=colors["border"], lightcolor=colors["border"], darkcolor=colors["border"],
                borderwidth=1,
            )
            style.map(
                "Table.Treeview",
                background=[("selected", colors["select_bg"])], foreground=[("selected", colors["select_fg"])],
            )
            style.configure(
                "Treeview.Heading", background=colors["tree_heading_bg"], foreground=colors["fg"],
                bordercolor=colors["border"], lightcolor=colors["tree_heading_bg"], darkcolor=colors["tree_heading_bg"],
                relief="flat",
            )
            style.map(
                "ReadonlyWhite.TEntry",
                fieldbackground=[("readonly", colors["entry_bg"])],
                foreground=[("readonly", colors["entry_fg"])],
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
            self.progress_canvas.configure(bg=colors["progress_track"])
            self.progress_canvas.itemconfig(self.progress_rect, fill=colors["progress_fill"])
            self.progress_canvas.itemconfig(self.progress_text, fill=colors["progress_text"])
            self.table.tag_configure("odd_row", background=colors["tree_odd_row"], foreground=colors["tree_fg"])
            self.table.tag_configure("even_row", background=colors["tree_bg"], foreground=colors["tree_fg"])
        else:
            style.map(
                "ReadonlyWhite.TEntry",
                fieldbackground=[("readonly", "white")],
                foreground=[("readonly", "black")],
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
            self.progress_canvas.configure(bg="#e2e2e2")
            self.progress_canvas.itemconfig(self.progress_rect, fill="#4a90d9")
            self.progress_canvas.itemconfig(self.progress_text, fill="#1a1a1a")
            self.table.tag_configure("odd_row", background="#e9e9e9", foreground="black")
            self.table.tag_configure("even_row", background="white", foreground="black")

        for entry in (self.new_mention_entry, self.table_filter_entry):
            entry.normal_color = colors["entry_fg"] if dark else "black"
            if not getattr(entry, "placeholder_active", False):
                entry.configure(foreground=entry.normal_color)

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
        (Windows 10 1809+ / 11 only). Silently does nothing if unsupported -
        the app is still fully usable, just with a light title bar.
        """
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
        """Pings Discord once (ever, per Windows account) to let the
        developer know a new person/machine started using the app -
        disclosed in Settings' legal notice text (see the "View license &
        third-party notices" area), not sent silently with no mention
        anywhere. Gated by a saved setting so it only ever fires once per
        %APPDATA%\\Track-Tidy (i.e. once per machine + Windows account,
        until that folder is cleared)."""
        if tagger.load_settings().get("install_notified"):
            return
        tagger.save_setting("install_notified", True)
        try:
            reporter_name = getpass.getuser()
        except Exception:
            reporter_name = ""

        def _send():
            tagger.send_new_install_notification(reporter_name=reporter_name)

        self._run_in_background(_send)

    def _check_internet_connection(self, is_startup_check=False):
        """Checks connectivity in the background, updates the status
        indicator, and (only for the initial startup check) warns once via
        a popup if there's no connection. Re-checks itself every 30s so the
        indicator stays accurate for the life of the run."""
        def _run_check():
            is_online = tagger.check_internet_connection()
            self.message_queue.put(("internet_status", (is_online, is_startup_check)))

        self._run_in_background(_run_check)
        self.window.after(30000, self._check_internet_connection)

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
                f"(you have v{tagger.APP_VERSION}).\n\nOpen the download page?",
                parent=self.window,
            )
            if open_page:
                webbrowser.open(release_url)
            return

        update_now = messagebox.askyesno(
            "Update available",
            f"A new version ({latest_version}) of Track-Tidy is available "
            f"(you have v{tagger.APP_VERSION}).\n\n"
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
            dest_path = os.path.join(tempfile.gettempdir(), f"Track-Tidy-Setup-{latest_version}.exe")
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
            subprocess.Popen([dest_path])
        except Exception as error:
            messagebox.showerror("Update failed", f"Could not launch the installer: {error}", parent=self.window)
            return

        self.window.destroy()

    # --- Startup checks ---

    def _check_cover_source_credentials_on_startup(self):
        """Nags (once, on startup) about any ENABLED cover source that's
        missing its credentials - iTunes needs none, so only SoundCloud/
        Spotify can trigger this."""
        if tagger.USE_SOUNDCLOUD and (not tagger.SOUNDCLOUD_CLIENT_ID or not tagger.SOUNDCLOUD_CLIENT_SECRET):
            messagebox.showinfo(
                "SoundCloud not configured",
                "SoundCloud is enabled as a cover source in Settings, but no Client ID / "
                "Client Secret is configured yet - add them in Settings, or turn it off.",
                parent=self.window,
            )
            self.notebook.select(2)  # Settings tab

        if tagger.USE_SPOTIFY and (not tagger.SPOTIFY_CLIENT_ID or not tagger.SPOTIFY_CLIENT_SECRET):
            messagebox.showinfo(
                "Spotify not configured",
                "Spotify is enabled as a cover source in Settings, but no Client ID / "
                "Client Secret is configured yet - add them in Settings, or turn it off.",
                parent=self.window,
            )
            self.notebook.select(2)  # Settings tab

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
        # tab content too, to cover the whole visible surface.
        for widget in (self.window, self.notebook, self.tagger_tab, self.table):
            try:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_files_dropped)
            except Exception:
                pass  # not fatal - the app works fine without drag-and-drop

    def _on_files_dropped(self, event):
        raw_paths = self.window.tk.splitlist(event.data)
        if not raw_paths:
            return

        first_path = os.path.normpath(raw_paths[0].strip("{}"))

        if os.path.isdir(first_path):
            self._start_dropped_folder_scan(first_path)
        else:
            self._start_single_file_scan(first_path)

    def _start_dropped_folder_scan(self, folder):
        """Drop of a folder: scans it fully, WITHOUT touching the visible
        'Parent folder' field (which only ever reflects an explicit Browse...)."""
        if not os.path.isdir(folder):
            return

        tagger.MUSIC_FOLDER = folder
        self._sync_mentions_to_remove()
        self.soundcloud_rate_limit_warned = False

        if folder != getattr(self, "last_scanned_folder", None):
            for row in self.table.get_children():
                self.table.delete(row)
            self.tk_images.clear()
            self.tk_images_hover.clear()
            self._thumbnail_pil_images.clear()
            self.scanned_plan = []
            self.last_scanned_folder = folder

        self.notebook.select(0)
        self._set_buttons_enabled(False)
        self._run_in_background(self._run_scan)

    def _start_single_file_scan(self, file_path):
        """Drop of a single audio file: tag just that file, without scanning
        everything else that happens to sit in the same folder."""
        if not os.path.isfile(file_path):
            return
        if not file_path.lower().endswith(tagger.SUPPORTED_EXTENSIONS):
            return
        if file_path.lower().endswith(".wav") and not tagger.AUTO_CONVERT_MP3:
            self._append_to_journal(
                f"Ignored '{os.path.basename(file_path)}' - WAV files are disabled "
                "(Settings > Convert everything to MP3)."
            )
            return

        folder = os.path.dirname(file_path)
        relative_name = os.path.basename(file_path)

        if any(info["file"] == relative_name for info in self.scanned_plan):
            return  # already in the table

        tagger.MUSIC_FOLDER = folder
        self.last_scanned_folder = folder
        self.soundcloud_rate_limit_warned = False

        self.notebook.select(0)
        self._set_buttons_enabled(False)
        self._run_in_background(self._run_scan, [relative_name])

    # --- UI construction ---

    def _build_interface(self):
        self.notebook = ttk.Notebook(self.window)
        self.notebook.pack(fill="both", expand=True)

        version_label = ttk.Label(self.window, text=f"v{tagger.APP_VERSION}", foreground="#999999")
        version_label.place(relx=1.0, rely=1.0, x=-6, y=-4, anchor="se")

        tagger_tab = ttk.Frame(self.notebook)
        extractor_tab = ttk.Frame(self.notebook)
        soundcloud_tab = ttk.Frame(self.notebook)
        self.tagger_tab = tagger_tab
        self.notebook.add(tagger_tab, text="Tagger")
        self.notebook.add(extractor_tab, text="Extractor")
        self.notebook.add(soundcloud_tab, text="Settings")
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # ============================== Tagger tab ==============================

        # --- Folder selection ---
        folder_frame = ttk.LabelFrame(tagger_tab, text="Parent folder:")
        folder_frame.pack(fill="x", padx=10, pady=(10, 2))

        self.folder_variable = tk.StringVar(value=os.path.abspath(tagger.MUSIC_FOLDER) if tagger.MUSIC_FOLDER else "")

        folder_entry_row = ttk.Frame(folder_frame)
        folder_entry_row.pack(fill="x", padx=10, pady=(10, 5))

        self._folder_icon_photo = self._build_folder_icon_photo()
        ttk.Label(folder_entry_row, image=self._folder_icon_photo).pack(side="left", padx=(0, 6))

        # "ReadonlyWhite.TEntry"'s actual colors are (re)configured by _apply_theme,
        # since they depend on the light/dark choice and ttk styles are per-theme.
        folder_entry = ttk.Entry(
            folder_entry_row, textvariable=self.folder_variable, state="readonly", style="ReadonlyWhite.TEntry"
        )
        folder_entry.pack(side="left", fill="x", expand=True)
        self._bind_entry_context_menu(folder_entry, readonly=True)

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

        self.advanced_frame = ttk.LabelFrame(tagger_tab, text="")
        # not shown by default (pack() is called/undone in _toggle_advanced_section)

        columns_frame = ttk.Frame(self.advanced_frame)
        columns_frame.pack(fill="x", padx=10, pady=(10, 5))

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
        ).pack(anchor="w", padx=10, pady=(0, 10))

        # --- Scan results table ---
        table_frame = ttk.LabelFrame(tagger_tab, text="")
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
        self.table = ttk.Treeview(scrollbars_frame, columns=COLUMNS, show="tree headings", height=8)
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

        self.table.bind("<Button-1>", self._toggle_cell, add="+")
        self.table.bind("<Button-1>", self._on_row_drag_start, add="+")
        self.table.bind("<B1-Motion>", self._on_row_drag_motion, add="+")
        self.table.bind("<ButtonRelease-1>", self._on_row_drag_release, add="+")
        self.table.bind("<Double-1>", self._toggle_cell_double_click, add="+")
        self.table.bind("<Triple-1>", self._toggle_cell_triple_click, add="+")
        self.table.bind("<Button-3>", self._show_context_menu)
        self.table.bind("<Delete>", self._delete_selected_rows)
        self.table.bind("<Control-a>", self._select_all_rows)
        self.table.bind("<Control-z>", self._undo_last_removal)
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

        self.progress_canvas = tk.Canvas(launch_frame, height=24, bg="#e2e2e2", highlightthickness=0)
        # not packed yet: only shown once a run has actually started (see _start_processing)

        self.progress_rect = self.progress_canvas.create_rectangle(0, 0, 0, 24, fill="#4a90d9", width=0)
        self.progress_text = self.progress_canvas.create_text(
            0, 12, text="", fill="#1a1a1a", font=("TkDefaultFont", 9, "bold")
        )

        # ============================== Extractor tab ==============================

        ttk.Label(
            extractor_tab,
            text=(
                "Flattens a messy music folder: every audio file (MP3, WAV, FLAC, "
                "AAC, M4A, OGG, WMA...) hidden inside any number of nested "
                "subfolders gets moved straight into the folder below.\n"
                "Now-empty subfolders are cleaned up automatically afterwards."
            ),
            justify="left",
            wraplength=440,
        ).pack(anchor="w", padx=10, pady=(15, 10))

        ttk.Label(extractor_tab, text="Folder to flatten:").pack(anchor="w", padx=10)
        self.extract_folder_var = tk.StringVar(value="")
        extract_folder_entry = ttk.Entry(
            extractor_tab, textvariable=self.extract_folder_var, state="readonly", style="ReadonlyWhite.TEntry"
        )
        extract_folder_entry.pack(fill="x", padx=10, pady=(0, 5))
        self._bind_entry_context_menu(extract_folder_entry, readonly=True)

        extract_buttons_frame = ttk.Frame(extractor_tab)
        extract_buttons_frame.pack(fill="x", padx=10, pady=(0, 10))
        self.extract_browse_button = ttk.Button(
            extract_buttons_frame, text="Browse...", command=self._choose_extract_folder
        )
        self.extract_browse_button.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.extract_button = ttk.Button(
            extract_buttons_frame, text="Extract", command=self._start_extraction
        )
        self.extract_button.configure(state="disabled")
        self.extract_button.pack(side="left", fill="x", expand=True)

        # ============================== Settings tab ==============================

        appearance_frame = ttk.LabelFrame(soundcloud_tab, text="Appearance")
        appearance_frame.pack(fill="x", padx=10, pady=(15, 10))
        for value, label in (("light", "Light"), ("dark", "Dark")):
            ttk.Radiobutton(
                appearance_frame, text=label, value=value, variable=self.theme_var,
                command=self._on_theme_changed,
            ).pack(anchor="w", padx=10, pady=(5, 0) if value == "light" else (0, 5))

        sources_frame = ttk.LabelFrame(soundcloud_tab, text="Sources")
        sources_frame.pack(fill="x", padx=10, pady=(0, 10))
        sources_row = ttk.Frame(sources_frame)
        sources_row.pack(fill="x", padx=10, pady=(10, 10))
        ttk.Checkbutton(
            sources_row, text="iTunes", variable=self.use_itunes_var,
            command=self._on_use_itunes_changed,
        ).pack(side="left", padx=(0, 15))
        ttk.Checkbutton(
            sources_row, text="Spotify", variable=self.use_spotify_var,
            command=self._on_use_spotify_changed,
        ).pack(side="left", padx=(0, 15))
        ttk.Checkbutton(
            sources_row, text="SoundCloud", variable=self.use_soundcloud_var,
            command=self._on_use_soundcloud_changed,
        ).pack(side="left")

        credentials_row = ttk.Frame(sources_frame)
        credentials_row.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(
            credentials_row, text="SoundCloud credentials...", command=self._show_soundcloud_credentials_dialog,
        ).pack(side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Button(
            credentials_row, text="Spotify credentials...", command=self._show_spotify_credentials_dialog,
        ).pack(side="left", fill="x", expand=True)

        behavior_frame = ttk.LabelFrame(soundcloud_tab, text="Behavior")
        behavior_frame.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Checkbutton(
            behavior_frame, text="Convert everything to MP3 (320 kbps)", variable=self.auto_convert_var,
            command=self._on_auto_convert_changed,
        ).pack(anchor="w", padx=10, pady=(10, 0))
        ttk.Checkbutton(
            behavior_frame, text="Show log section", variable=self.show_log_var,
            command=self._on_show_log_changed,
        ).pack(anchor="w", padx=10, pady=(0, 10))
        update_history_row = ttk.Frame(soundcloud_tab)
        update_history_row.pack(fill="x", padx=10, pady=(0, 10))
        self.check_update_button = ttk.Button(
            update_history_row, text="Check for updates", command=self._check_for_update_manual,
        )
        self.check_update_button.pack(fill="x", pady=(0, 5))
        ttk.Button(
            update_history_row, text="View processing history", command=self._show_history_window,
        ).pack(fill="x")

        self.internet_status_label = ttk.Label(
            soundcloud_tab, text="● Checking connection...", foreground="#999999",
        )
        self.internet_status_label.pack(anchor="w", padx=10, pady=(0, 10))

        legal_text_label = ttk.Label(
            soundcloud_tab,
            text=(
                "Track Tidy is an independent, personal tool and is not affiliated with, "
                "endorsed by, or sponsored by SoundCloud, Apple, or any other third-party "
                "service it connects to. All trademarks are the property of their "
                "respective owners.\n"
                "Licensed under the GNU General Public License v2 or later - includes "
                "mutagen (GPL-2.0-or-later) and FFmpeg (GPLv3).\n"
                "The first time it's launched on a new Windows account, it sends your "
                "Windows username to the developer (via Discord) so they know a new "
                "person is using it - this happens once."
            ),
            justify="left",
            wraplength=440,
            foreground="#888888",
            font=("TkDefaultFont", 8),
        )
        legal_text_label.pack(anchor="w", padx=10, pady=(0, 2), side="bottom")

        self.legal_notices_link = ttk.Label(
            soundcloud_tab, text="View license & third-party notices",
            foreground="#1a73e8", cursor="hand2", font=("TkDefaultFont", 8),
        )
        self.legal_notices_link.pack(anchor="w", padx=10, pady=(0, 6), side="bottom")
        self.legal_notices_link.bind("<Button-1>", self._open_legal_notices)

        ttk.Label(
            soundcloud_tab,
            text="Developped by KEVZ",
            foreground="#888888",
            font=("TkDefaultFont", 8, "bold"),
        ).pack(anchor="w", padx=10, pady=(0, 2), side="bottom")

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

    def _update_progress_bar(self, fraction, text):
        """Redraws the progress bar (rectangle + text) on the canvas."""
        self.progress_canvas.update_idletasks()
        width = self.progress_canvas.winfo_width() or (WINDOW_WIDTH - 40)
        height = 24

        self.progress_canvas.coords(self.progress_rect, 0, 0, width * fraction, height)
        self.progress_canvas.coords(self.progress_text, width / 2, height / 2)
        self.progress_canvas.itemconfigure(self.progress_text, text=text)

    def _toggle_advanced_section(self):
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

    def _update_soundcloud_save_state(self, event=None):
        id_value = self.sc_client_id_entry.get().strip()
        secret_value = self.sc_client_secret_entry.get().strip()
        both_filled = bool(id_value) and bool(secret_value)
        both_empty = not id_value and not secret_value
        self.sc_save_button.configure(state="normal" if (both_filled or both_empty) else "disabled")

    def _save_soundcloud_credentials(self):
        client_id = self.sc_client_id_entry.get().strip()
        client_secret = self.sc_client_secret_entry.get().strip()

        try:
            tagger.write_credential(tagger.CLIENT_ID_FILE, client_id)
            tagger.write_credential(tagger.CLIENT_SECRET_FILE, client_secret)

            tagger.SOUNDCLOUD_CLIENT_ID = client_id or None
            tagger.SOUNDCLOUD_CLIENT_SECRET = client_secret or None
            tagger.invalidate_soundcloud_token()  # in case the credentials changed

            messagebox.showinfo("Saved", "SoundCloud credentials saved.", parent=self._soundcloud_dialog)
        except Exception as error:
            messagebox.showerror("Error", f"Could not save credentials: {error}", parent=self._soundcloud_dialog)

    def _open_soundcloud_registration(self):
        webbrowser.open("https://soundcloud.com/you/apps")

    def _show_soundcloud_credentials_dialog(self):
        dialog = tk.Toplevel(self.window)
        self._style_toplevel(dialog)
        dialog.title("SoundCloud credentials")
        dialog.resizable(False, False)
        dialog.transient(self.window)
        dialog.grab_set()
        self._soundcloud_dialog = dialog

        ttk.Label(
            dialog,
            text="SoundCloud requires registering an app yourself (Artist Pro account).\n"
                 "Paste the Client ID / Client Secret you get from that page below.",
            justify="left",
            wraplength=440,
        ).pack(anchor="w", padx=10, pady=(15, 10))

        ttk.Label(dialog, text="Client ID:").pack(anchor="w", padx=10)
        self.sc_client_id_entry = ttk.Entry(dialog)
        self.sc_client_id_entry.pack(fill="x", padx=10, pady=(0, 10))
        self.sc_client_id_entry.bind("<KeyRelease>", self._update_soundcloud_save_state)
        self._bind_entry_context_menu(self.sc_client_id_entry)
        if tagger.SOUNDCLOUD_CLIENT_ID:
            self.sc_client_id_entry.insert(0, tagger.SOUNDCLOUD_CLIENT_ID)

        ttk.Label(dialog, text="Client Secret:").pack(anchor="w", padx=10)
        self.sc_client_secret_entry = ttk.Entry(dialog, show="*")
        self.sc_client_secret_entry.pack(fill="x", padx=10, pady=(0, 15))
        self.sc_client_secret_entry.bind("<KeyRelease>", self._update_soundcloud_save_state)
        self._bind_entry_context_menu(self.sc_client_secret_entry)
        if tagger.SOUNDCLOUD_CLIENT_SECRET:
            self.sc_client_secret_entry.insert(0, tagger.SOUNDCLOUD_CLIENT_SECRET)

        soundcloud_buttons_frame = ttk.Frame(dialog)
        soundcloud_buttons_frame.pack(fill="x", padx=10, pady=(0, 10))
        self.sc_save_button = ttk.Button(
            soundcloud_buttons_frame, text="Save", command=self._save_soundcloud_credentials
        )
        self.sc_save_button.pack(side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Button(
            soundcloud_buttons_frame, text="Register a SoundCloud app",
            command=self._open_soundcloud_registration,
        ).pack(side="left", fill="x", expand=True)

        self._update_soundcloud_save_state()

        ttk.Button(dialog, text="Close", command=dialog.destroy).pack(pady=(0, 15))
        dialog.bind("<Escape>", lambda _event: dialog.destroy())

        self._center_dialog(dialog)

    def _update_spotify_save_state(self, event=None):
        id_value = self.sp_client_id_entry.get().strip()
        secret_value = self.sp_client_secret_entry.get().strip()
        both_filled = bool(id_value) and bool(secret_value)
        both_empty = not id_value and not secret_value
        self.sp_save_button.configure(state="normal" if (both_filled or both_empty) else "disabled")

    def _save_spotify_credentials(self):
        client_id = self.sp_client_id_entry.get().strip()
        client_secret = self.sp_client_secret_entry.get().strip()

        try:
            tagger.write_credential(tagger.SPOTIFY_CLIENT_ID_FILE, client_id)
            tagger.write_credential(tagger.SPOTIFY_CLIENT_SECRET_FILE, client_secret)

            tagger.SPOTIFY_CLIENT_ID = client_id or None
            tagger.SPOTIFY_CLIENT_SECRET = client_secret or None
            tagger.invalidate_spotify_token()  # in case the credentials changed

            messagebox.showinfo("Saved", "Spotify credentials saved.", parent=self._spotify_dialog)
        except Exception as error:
            messagebox.showerror("Error", f"Could not save credentials: {error}", parent=self._spotify_dialog)

    def _open_spotify_registration(self):
        webbrowser.open("https://developer.spotify.com/dashboard")

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
            os.startfile(path)
        except Exception as error:
            self._append_to_journal(f"Could not open license notices: {error}")

    def _show_spotify_credentials_dialog(self):
        dialog = tk.Toplevel(self.window)
        self._style_toplevel(dialog)
        dialog.title("Spotify credentials")
        dialog.resizable(False, False)
        dialog.transient(self.window)
        dialog.grab_set()
        self._spotify_dialog = dialog

        ttk.Label(
            dialog,
            text="Spotify requires registering an app yourself (free, at the link below).\n"
                 "Any Redirect URI works (it's never actually used) - paste the Client ID / "
                 "Client Secret you get from that page below.",
            justify="left",
            wraplength=440,
        ).pack(anchor="w", padx=10, pady=(15, 10))

        ttk.Label(dialog, text="Client ID:").pack(anchor="w", padx=10)
        self.sp_client_id_entry = ttk.Entry(dialog)
        self.sp_client_id_entry.pack(fill="x", padx=10, pady=(0, 10))
        self.sp_client_id_entry.bind("<KeyRelease>", self._update_spotify_save_state)
        self._bind_entry_context_menu(self.sp_client_id_entry)
        if tagger.SPOTIFY_CLIENT_ID:
            self.sp_client_id_entry.insert(0, tagger.SPOTIFY_CLIENT_ID)

        ttk.Label(dialog, text="Client Secret:").pack(anchor="w", padx=10)
        self.sp_client_secret_entry = ttk.Entry(dialog, show="*")
        self.sp_client_secret_entry.pack(fill="x", padx=10, pady=(0, 15))
        self.sp_client_secret_entry.bind("<KeyRelease>", self._update_spotify_save_state)
        self._bind_entry_context_menu(self.sp_client_secret_entry)
        if tagger.SPOTIFY_CLIENT_SECRET:
            self.sp_client_secret_entry.insert(0, tagger.SPOTIFY_CLIENT_SECRET)

        spotify_buttons_frame = ttk.Frame(dialog)
        spotify_buttons_frame.pack(fill="x", padx=10, pady=(0, 10))
        self.sp_save_button = ttk.Button(
            spotify_buttons_frame, text="Save", command=self._save_spotify_credentials
        )
        self.sp_save_button.pack(side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Button(
            spotify_buttons_frame, text="Register a Spotify app",
            command=self._open_spotify_registration,
        ).pack(side="left", fill="x", expand=True)

        self._update_spotify_save_state()

        ttk.Button(dialog, text="Close", command=dialog.destroy).pack(pady=(0, 15))
        dialog.bind("<Escape>", lambda _event: dialog.destroy())

        self._center_dialog(dialog)

    # --- Extractor tab actions ---

    def _choose_extract_folder(self):
        folder = filedialog.askdirectory(title="Choose the folder to flatten")
        if folder:
            self.extract_folder_var.set(folder)
            self.extract_button.configure(state="normal")

    def _start_extraction(self):
        folder = self.extract_folder_var.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("Missing folder", "Please choose a valid folder first.", parent=self.window)
            return

        self.extract_browse_button.configure(state="disabled")
        self.extract_button.configure(state="disabled")

        self._run_in_background(self._run_extraction, folder)

    def _run_extraction(self, folder):
        try:
            moved_count = tagger.extract_audio_files(folder, log=self._append_to_journal)
            removed_count = tagger.remove_empty_subfolders(folder, log=self._append_to_journal)
            self.message_queue.put(("extract_done", (folder, moved_count, removed_count, None)))
        except Exception as error:
            self.message_queue.put(("extract_done", (folder, 0, 0, str(error))))

    # --- Folder / mention actions ---

    def _choose_folder(self):
        folder = filedialog.askdirectory(title="Choose the audio files folder")
        if folder:
            self.folder_variable.set(folder)
            self.scan_button.configure(state="normal")
            self.reset_button.configure(state="normal")
            self.apply_button.configure(state="normal")

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
        self.scan_button.configure(text="Scan")
        self._update_apply_button_label()

        self.sort_state = {"column": None, "state": 0}
        self.table.heading("title", text="Title")
        self.table.heading("artist", text="Artist")

        self.progress_canvas.pack_forget()
        self._update_progress_bar(0, "")

        self.journal_text.configure(state="normal")
        self.journal_text.delete("1.0", "end")
        self.journal_text.configure(state="disabled")

        self._adjust_window_height()

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
        if enabled:
            self.scan_button.configure(text="Scan")
            self._update_apply_button_label()
            self.apply_button.configure(text="Apply", command=self._start_processing, state="normal")
        else:
            self.cancel_requested.clear()
            self.apply_button.configure(text="Cancel", command=self._request_cancel, state="normal")
        self._set_tabs_locked(not enabled)

    def _set_tabs_locked(self, locked):
        """Prevents switching tabs while a scan or a processing run is in progress."""
        current_index = self.notebook.index("current")
        for index in range(len(self.notebook.tabs())):
            if locked and index != current_index:
                self.notebook.tab(index, state="disabled")
            else:
                self.notebook.tab(index, state="normal")

    def _start_scan(self):
        folder = self.folder_variable.get().strip()
        if not folder:
            messagebox.showwarning("Missing folder", "Please choose a folder before scanning.", parent=self.window)
            return

        tagger.MUSIC_FOLDER = folder
        self._sync_mentions_to_remove()
        self.soundcloud_rate_limit_warned = False

        if folder != getattr(self, "last_scanned_folder", None):
            for row in self.table.get_children():
                self.table.delete(row)
            self.tk_images.clear()
            self.tk_images_hover.clear()
            self._thumbnail_pil_images.clear()
            self.scanned_plan = []
            self.last_scanned_folder = folder

        self._set_buttons_enabled(False)

        self._run_in_background(self._run_scan)

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
                self.message_queue.put(("file_scanned", info))
                self.message_queue.put(("scan_progress", (scanned_count["value"], total)))

            tagger.scan_files(
                new_files,
                on_file_scanned=_on_file_scanned,
                log=self._append_to_journal,
                on_new_mention=lambda mention: self.message_queue.put(("mention_added", mention)),
                on_rate_limited=lambda: self.message_queue.put(("soundcloud_rate_limited", None)),
                should_cancel=self.cancel_requested.is_set,
            )

        except Exception as error:
            self._append_to_journal(f"Error during scan: {error}")
            removed_files = set()

        self.message_queue.put(("scan_done", (removed_files, number_before)))

    def _add_scan_row(self, info):
        """Immediately adds a row to the table, ABOVE the previous ones, as soon as a file has just been scanned."""
        # Re-sync with the CURRENT "To remove" list (main thread, authoritative)
        # before displaying - the background scan thread may have computed this
        # row's title with a slightly stale mentions list (e.g. "By FuviClan"
        # just got auto-activated by an earlier file in the very same scan).
        self._sync_mentions_to_remove()
        if not info.get("processed"):
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

    def _restripe_rows(self):
        """Re-applies alternating row colors based on each row's current position."""
        for index, item_id in enumerate(self.table.get_children()):
            tag = "even_row" if index % 2 == 0 else "odd_row"
            self.table.item(item_id, tags=(tag,))

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
            if no_cover_only and info.get("cover_source"):
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
        """Custom error dialog that plays a fart sound instead of the default Windows error beep."""
        sound_path = resource_path("fart.wav")
        try:
            winsound.PlaySound(sound_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
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
            text="💨 No .mp3 or .wav file was found in this folder\n(or its subfolders).",
            justify="center",
            padding=20,
        ).pack()
        ttk.Button(dialog, text="OK", command=dialog.destroy).pack(pady=(0, 15))

        self._center_dialog(dialog)

    def _compute_processing_summary(self):
        """Counts, among the files actually processed in this session, how
        many were converted - the cover breakdown itself is shown right
        after scanning instead (see _compute_cover_summary), since it's
        already known by then and doesn't change during Apply."""
        converted_count = sum(
            1 for info in self.scanned_plan if info.get("processed") and info.get("convert")
        )
        return converted_count

    def _compute_cover_summary(self):
        """Counts, among all currently scanned tracks, how many got a cover
        from each source, how many kept their original one, and how many
        have none at all - independent of whether Apply has run yet."""
        itunes_count = 0
        spotify_count = 0
        soundcloud_count = 0
        kept_existing_count = 0
        no_cover_count = 0

        for info in self.scanned_plan:
            source = info.get("cover_source")
            if source == "iTunes":
                itunes_count += 1
            elif source == "Spotify":
                spotify_count += 1
            elif source == "SoundCloud":
                soundcloud_count += 1
            elif info.get("has_cover"):
                kept_existing_count += 1
            else:
                no_cover_count += 1

        return itunes_count, spotify_count, soundcloud_count, kept_existing_count, no_cover_count

    def _show_scan_summary_dialog(self, no_cover_infos=None):
        """Cover-source breakdown shown right after a scan finds new files -
        this is the earliest point every track's cover_source is known.
        A single dialog: "OK" alone, or "OK" plus a second button straight
        into fixing Artist/Title for no-cover tracks when there are any -
        not a separate yes/no confirmation chained after this one."""
        itunes_count, spotify_count, soundcloud_count, kept_existing_count, no_cover_count = (
            self._compute_cover_summary()
        )

        dialog = tk.Toplevel(self.window)
        self._style_toplevel(dialog)
        dialog.title("Scan complete")
        dialog.resizable(False, False)
        dialog.transient(self.window)
        dialog.grab_set()

        ttk.Label(
            dialog,
            text=f"{PROCESSED_CHECK} Scan complete.",
            justify="center",
            padding=(20, 20, 20, 5),
        ).pack()

        summary_text = (
            f"Cover from iTunes: {itunes_count}\n"
            f"Cover from Spotify: {spotify_count}\n"
            f"Cover from SoundCloud: {soundcloud_count}\n"
            f"Kept original cover: {kept_existing_count}\n"
            f"No cover at all: {no_cover_count}"
        )
        ttk.Label(dialog, text=summary_text, justify="left", foreground="#555555").pack(padx=20, pady=(0, 15))

        button_row = ttk.Frame(dialog)
        button_row.pack(pady=(0, 15))

        def close():
            dialog.destroy()

        ttk.Button(button_row, text="OK", command=close).pack(side="left", padx=(0, 5) if no_cover_infos else 0)

        if no_cover_infos:
            count = len(no_cover_infos)
            unit = "track" if count == 1 else "tracks"

            def fix_no_cover():
                dialog.destroy()
                self._show_fix_no_cover_dialog(no_cover_infos)

            ttk.Button(button_row, text=f"Fix {count} {unit}...", command=fix_no_cover).pack(side="left")

        dialog.protocol("WM_DELETE_WINDOW", close)

        self._center_dialog(dialog)

    def _show_success_dialog(self):
        """Custom success dialog with a green checkmark and a distinct chime
        sound - the cover breakdown is shown earlier at scan time, so this
        one just confirms the run finished and how many files were converted."""
        sound_path = resource_path("success.wav")
        try:
            winsound.PlaySound(sound_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            pass  # if the sound file is missing, just show the dialog silently

        converted_count = self._compute_processing_summary()

        dialog = tk.Toplevel(self.window)
        self._style_toplevel(dialog)
        dialog.title("Processing complete")
        dialog.resizable(False, False)
        dialog.transient(self.window)
        dialog.grab_set()

        ttk.Label(
            dialog,
            text=f"{PROCESSED_CHECK} All files have been processed.",
            justify="center",
            padding=(20, 20, 20, 5),
        ).pack()

        ttk.Label(
            dialog, text=f"Converted to MP3: {converted_count}", justify="left", foreground="#555555",
        ).pack(padx=20, pady=(0, 15))

        ttk.Button(dialog, text="OK", command=dialog.destroy).pack(pady=(0, 15))

        self._center_dialog(dialog)

    def _finalize_scan(self, result):
        removed_files, number_before = result
        self._set_buttons_enabled(True)

        # Removes files that no longer exist on disk
        for file_name in removed_files:
            if self.table.exists(file_name):
                self.table.delete(file_name)
            self.tk_images.pop(file_name, None)
            self.tk_images_hover.pop(file_name, None)
            self._thumbnail_pil_images.pop(file_name, None)
        self.scanned_plan = [info for info in self.scanned_plan if info["file"] not in removed_files]

        number_new = len(self.scanned_plan) - number_before + len(removed_files)

        if not self.scanned_plan:
            self._append_to_journal("No audio file (.mp3/.wav) found in this folder.")
            self._show_no_files_dialog()
        else:
            if number_new == 0 and not removed_files:
                self._append_to_journal("Scan complete: no new file detected.")
            else:
                self._append_to_journal(
                    f"Scan complete: {number_new} new, "
                    f"{len(removed_files)} removed, {len(self.scanned_plan)} total."
                )

            no_cover_infos = [
                info for info in self.scanned_plan if not info.get("processed") and not info.get("cover_source")
            ]
            if no_cover_infos:
                self._append_to_journal(f"{len(no_cover_infos)} track(s) currently have no cover match.")

            if number_new > 0:
                self._show_scan_summary_dialog(no_cover_infos=no_cover_infos)

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
        if not messagebox.askyesno("Duplicate tracks found", message, parent=self.window):
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

    def _show_fix_no_cover_dialog(self, infos):
        """Lets the user correct Artist/Title for each track and retry the
        cover search right there - each row is independent, so some can be
        fixed while others are left for later. Used both for tracks a scan
        couldn't find a cover for, and for the right-click "Rescan" action
        (rescanning blind with the same auto-detected Artist/Title tends to
        just reproduce the same result, so this lets it be corrected
        first)."""
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

            artist_entry = ttk.Entry(fields_row, width=20)
            artist_entry.insert(0, info.get("artist_override") or info.get("detected_artist") or "")
            artist_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
            self._bind_entry_context_menu(artist_entry)

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
            self._fix_dialog_rows = {}
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", on_close)
        self._center_dialog(dialog)

    def _search_fix_row(self, info, artist_entry, title_entry, search_button, status_label):
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
                artist, title, log=self._append_to_journal,
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
        needs_conversion = info["format"] != "MP3"

        if info.get("processed"):
            displayed_title = info["title_override"] or info["detected_title"] or "?"
            displayed_artist = info["artist_override"]
            if displayed_artist is None:
                displayed_artist = info["detected_artist"] if info["detected_artist"] else "(empty)"
            displayed_format = f"MP3 {PROCESSED_CHECK}" if (needs_conversion and info["convert"]) else info["format"]
            return (CHECKED_BOX, displayed_title, displayed_artist, displayed_format)

        apply = info["apply_changes"]

        if info["title_override"] is not None:
            displayed_title = info["title_override"]
        elif apply:
            displayed_title = info["detected_title"] or "?"
        else:
            displayed_title = info["current_title"] or "(empty)"

        if info["artist_override"] is not None:
            displayed_artist = info["artist_override"]
        elif apply:
            displayed_artist = info["detected_artist"] if info["detected_artist"] else "(empty)"
        else:
            displayed_artist = info["current_artist"] or "(empty)"

        apply_box = CHECKED_BOX if apply else EMPTY_BOX

        if needs_conversion:
            convert_box = CHECKED_BOX if info["convert"] else EMPTY_BOX
            format_text = "MP3" if info["convert"] else info["format"]
            displayed_format = f"{format_text} {convert_box}"
        else:
            displayed_format = info["format"]

        return (apply_box, displayed_title, displayed_artist, displayed_format)

    # --- Table interactions (sort, toggle, reorder) ---

    def _toggle_all(self):
        """Checks or unchecks 'apply_changes' for all *visible* rows not yet
        processed - rows currently hidden by a filter (e.g. "Only show
        tracks with no cover match") are left untouched, since the user
        never saw them to make that choice."""
        self.all_checked_state = not self.all_checked_state
        visible_files = set(self.table.get_children())

        for info in self.scanned_plan:
            if not info.get("processed") and info["file"] in visible_files:
                info["apply_changes"] = self.all_checked_state
                self._refresh_row(info)

        self.table.heading("apply", text=CHECKED_BOX if self.all_checked_state else EMPTY_BOX)
        self._update_apply_button_label()

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

    def _toggle_all_convert(self):
        """Toggles 'convert' for all non-MP3 files not yet processed."""
        self.all_convert_state = not self.all_convert_state

        for info in self.scanned_plan:
            if not info.get("processed") and info["format"] != "MP3":
                info["convert"] = self.all_convert_state
                self.table.item(info["file"], values=self._build_row_values(info))

        self.table.heading("format", text=CHECKED_BOX if self.all_convert_state else EMPTY_BOX)

    def _reorder_table_rows(self):
        """Reorders the table rows to match the current order of self.scanned_plan."""
        for new_index, info in enumerate(self.scanned_plan):
            self.table.move(info["file"], "", new_index)
            tag = "even_row" if new_index % 2 == 0 else "odd_row"
            self.table.item(info["file"], tags=(tag,))

    def _toggle_cell(self, event):
        """Single click on '✓' or 'Format' only: toggles the value."""
        item_id = self.table.identify_row(event.y)
        column_id = self.table.identify_column(event.x)  # "#0", "#1", "#2"...

        if not item_id:
            return

        info = next((i for i in self.scanned_plan if i["file"] == item_id), None)
        if not info:
            return

        if info.get("processed"):
            return  # checkbox and format are locked once the file has been processed

        if column_id == "#0":
            self._show_cover_zoom(info)
        elif column_id == f"#{COLUMNS.index('apply') + 1}":
            info["apply_changes"] = not info["apply_changes"]
            self._refresh_row(info)  # the image also changes based on current/suggested
            self._update_apply_button_label()
        elif column_id == f"#{COLUMNS.index('format') + 1}":
            if info["format"] != "WAV":
                return  # nothing to toggle for mp3s
            info["convert"] = not info["convert"]
            self.table.item(item_id, values=self._build_row_values(info))

    # --- Cover zoom ---

    def _resolve_full_path(self, info):
        """Absolute on-disk path for a scanned/processed row's file."""
        relative_path = info.get("final_path") or info["file"]
        base_folder = tagger.MUSIC_FOLDER
        if not os.path.isabs(base_folder):
            base_folder = os.path.join(tagger.app_base_dir(), base_folder)
        return os.path.abspath(os.path.join(base_folder, relative_path))

    def _show_cover_zoom(self, info):
        """Click on the cover thumbnail: shows it full-size in a popup, with
        buttons to import a replacement or remove it - both write straight to
        the file on disk immediately, independent of the Apply workflow."""
        dialog = tk.Toplevel(self.window)
        self._style_toplevel(dialog)
        dialog.title(tagger.build_display_name(info.get("detected_artist"), info.get("detected_title")))
        dialog.resizable(False, False)
        dialog.transient(self.window)

        # Covers are typically already small (iTunes/Spotify/SoundCloud
        # artwork rarely exceeds ~600px) - cap the popup size without
        # upscaling anything past its native resolution.
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
            full_path = self._resolve_full_path(info)
            if not os.path.exists(full_path):
                messagebox.showerror("File not found", "Could not locate this file on disk anymore.", parent=dialog)
                return
            try:
                tagger.write_tags(
                    full_path, artist="", title="", cover_image=new_bytes,
                    force_remove_if_missing=True, update_title=False, update_artist=False, update_cover=True,
                )
            except Exception as error:
                messagebox.showerror("Error", f"Could not update the cover: {error}", parent=dialog)
                return

            info["current_cover_bytes"] = new_bytes
            info["found_cover_image"] = new_bytes
            info["has_cover"] = new_bytes is not None
            info["cover_source"] = "Manual" if new_bytes else None
            self._refresh_row(info)
            self._append_to_journal(
                f"Cover {'updated' if new_bytes else 'removed'} for '{info['file']}'"
            )
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
            if messagebox.askyesno("Remove cover", "Remove the cover from this file?", parent=dialog):
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
        dialog.geometry("840x440")
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
            table_frame, columns=columns, show="tree headings", height=15, selectmode="extended",
            style="Table.Treeview",
        )
        tree.heading("#0", text="When")
        tree.column("#0", width=140, anchor="w")
        for col in columns:
            tree.heading(col, text=headings[col])
            tree.column(col, width=widths[col], anchor="w")

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="left", fill="y")

        search_frame = ttk.Frame(dialog)
        search_frame.pack(fill="x", padx=10, pady=(0, 5))
        history_filter_entry = ttk.Entry(search_frame)
        history_filter_entry.pack(fill="x", expand=True)
        self._bind_entry_context_menu(history_filter_entry)

        empty_label = ttk.Label(dialog, text="No files have been processed yet.", padding=20)
        entries_by_parent = {}

        def matches_query(entry, query):
            haystack = " ".join(str(entry.get(key) or "") for key in (
                "old_file", "old_artist", "old_title", "new_file", "new_artist", "new_title",
            )).lower()
            return query in haystack

        def populate():
            for row in tree.get_children():
                tree.delete(row)
            entries_by_parent.clear()

            if getattr(history_filter_entry, "placeholder_active", False):
                query = ""
            else:
                query = history_filter_entry.get().strip().lower()

            all_entries = list(reversed(tagger.load_history_entries()))
            entries = [e for e in all_entries if matches_query(e, query)] if query else all_entries

            empty_label.pack_forget()
            if not entries:
                empty_label.configure(
                    text="No matching entries." if query else "No files have been processed yet."
                )
                empty_label.pack()
                return

            for entry in entries:
                timestamp = entry.get("timestamp", "")
                try:
                    date_display = datetime.fromisoformat(timestamp).astimezone().strftime("%Y-%m-%d %H:%M")
                except ValueError:
                    date_display = timestamp

                parent_id = tree.insert(
                    "", "end", text=date_display,
                    values=(
                        entry.get("old_file", ""), entry.get("old_artist") or "-", entry.get("old_title") or "-",
                        "", "",
                    ),
                )
                tree.insert(
                    parent_id, "end", text="↳ Applied",
                    values=(
                        entry.get("new_file", ""), entry.get("new_artist") or "-", entry.get("new_title") or "-",
                        "Yes" if entry.get("cover_updated") else "No",
                        "Yes" if entry.get("converted") else "No",
                    ),
                )
                tree.item(parent_id, open=True)
                entries_by_parent[parent_id] = entry

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
            so only old-info rows are meant to be selectable."""
            current = tree.selection()
            corrected, seen = [], set()
            for item_id in current:
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

            if not messagebox.askyesno("Restore previous version(s)", prompt, parent=dialog):
                return

            successes, failures = 0, []
            for entry in entries:
                try:
                    tagger.restore_history_entry(entry, log=self._append_to_journal)
                    successes += 1
                except Exception as error:
                    failures.append((entry.get("new_file", "?"), str(error)))

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
                parent=dialog,
            ):
                return

            tagger.delete_history_entries(entries)
            populate()

        def show_context_menu(event):
            row_id = tree.identify_row(event.y)
            if not row_id:
                return
            target = row_id if row_id in entries_by_parent else tree.parent(row_id)
            if target and target not in tree.selection():
                tree.selection_set(target)

            menu = tk.Menu(dialog, tearoff=0)
            if self.theme_colors:
                menu.configure(
                    bg=self.theme_colors["menu_bg"], fg=self.theme_colors["menu_fg"],
                    activebackground=self.theme_colors["select_bg"], activeforeground=self.theme_colors["select_fg"],
                )
            menu.add_command(label="Delete", command=delete_selected)
            menu.tk_popup(event.x_root, event.y_root)

        tree.bind("<Button-3>", show_context_menu)

        def reset_history():
            if not tagger.load_history_entries():
                messagebox.showinfo("Nothing to reset", "The processing history is already empty.", parent=dialog)
                return
            if not messagebox.askyesno(
                "Reset history",
                "Permanently delete the entire processing history?\n\n"
                "This only removes the log - it doesn't touch any audio file. "
                "This cannot be undone.",
                parent=dialog,
            ):
                return
            tagger.clear_history_entries()
            populate()

        populate()

        button_frame = ttk.Frame(dialog)
        button_frame.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(button_frame, text="Restore selected", command=restore_selected).pack(side="left")
        ttk.Button(button_frame, text="Reset", command=reset_history).pack(side="left", padx=(5, 0))

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
        """Shows a tooltip with the full text of a Title/Artist cell, but
        only when the displayed text doesn't actually fit in the column
        (so it never fires on rows that don't need it)."""
        col_name = {"#2": "title", "#3": "artist"}.get(column_id) if row_id else None
        text = ""

        if col_name and self.table.exists(row_id):
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

    def _show_tooltip(self, text, event):
        self._tooltip_window = tk.Toplevel(self.window)
        self._tooltip_window.overrideredirect(True)
        self._tooltip_window.attributes("-topmost", True)
        ttk.Label(
            self._tooltip_window, text=text, background="#ffffe0", foreground="#1a1a1a",
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
            os.startfile(full_path)
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
        the actual file on disk. Ctrl+Z (_undo_last_removal) brings them
        back at their original position."""
        selected_items = self.table.selection()
        if not selected_items:
            return

        for item_id in selected_items:
            if self.table.exists(item_id):
                self.table.delete(item_id)
            self.tk_images.pop(item_id, None)
            self.tk_images_hover.pop(item_id, None)
            self._thumbnail_pil_images.pop(item_id, None)

        selected_set = set(selected_items)
        removed = [
            (index, info) for index, info in enumerate(self.scanned_plan) if info["file"] in selected_set
        ]
        self._removal_undo_stack.append(removed)
        self.scanned_plan = [info for info in self.scanned_plan if info["file"] not in selected_set]

        self._restripe_rows()
        self._update_apply_button_label()

    def _undo_last_removal(self, event=None):
        """Ctrl+Z: brings back the most recently removed row(s) (via Delete
        or "Remove from list"), each at its original position in the list."""
        if not self._removal_undo_stack:
            return
        removed = self._removal_undo_stack.pop()

        for index, info in sorted(removed, key=lambda pair: pair[0]):
            self.scanned_plan.insert(min(index, len(self.scanned_plan)), info)
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
        self._drag_moved = False

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
        self._drag_moved = True

        visible_files = set(self.table.get_children())
        for info in self.scanned_plan:
            if info["file"] in visible_files:
                self.table.move(info["file"], "", "end")

        self._restripe_rows()

    def _on_row_drag_release(self, event):
        self._drag_row_id = None
        self._drag_moved = False

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

        menu = tk.Menu(self.window, tearoff=0)
        if self.theme_colors:
            menu.configure(
                bg=self.theme_colors["menu_bg"], fg=self.theme_colors["menu_fg"],
                activebackground=self.theme_colors["select_bg"], activeforeground=self.theme_colors["select_fg"],
            )
        rescan_label = "Rescan this file" if len(selected_infos) <= 1 else f"Rescan selected ({len(selected_infos)})"
        menu.add_command(label="Info", command=lambda: self._show_track_info(info))
        menu.add_command(label=rescan_label, command=lambda: self._show_fix_no_cover_dialog(selected_infos))
        menu.add_command(label="Open file location", command=lambda: self._open_file_location(info))
        menu.add_separator()
        menu.add_command(label="Move up", command=lambda: self._move_row(info, -1))
        menu.add_command(label="Move down", command=lambda: self._move_row(info, 1))
        menu.add_separator()
        menu.add_command(label="Report track...", command=lambda: self._report_track(info))
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
            subprocess.run(["explorer", f"/select,{full_path}"])
        except Exception as error:
            self._append_to_journal(f"Error opening file location: {error}")

    def _show_track_info(self, info):
        """Shows a read-only summary of everything known about this row -
        current vs. suggested tags, cover status, and which platform (if
        any) the cover match came from."""
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

        suggested_artist = info["artist_override"] if info.get("artist_override") is not None else info.get("detected_artist")
        suggested_title = info["title_override"] if info.get("title_override") is not None else info.get("detected_title")

        rows = [
            ("File", info.get("file") or "?"),
            ("Format", info.get("format") or "?"),
            ("Current artist", info.get("current_artist") or "(none)"),
            ("Current title", info.get("current_title") or "(none)"),
            ("Suggested artist", suggested_artist or "(none)"),
            ("Suggested title", suggested_title or "(none)"),
            ("Cover match", cover_summary),
            ("Mention detected", info.get("mention_detected") or "(none)"),
            ("Apply changes", "Yes" if info.get("apply_changes") else "No"),
            ("Convert to MP3", "Yes" if info.get("convert") else "No"),
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
            success = tagger.send_track_report(info, reporter_name=reporter_name)
            self.message_queue.put(("report_sent", success))

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
            info[f"{field}_override"] = new_value if new_value else None
            info[f"{field}_override_is_manual"] = bool(new_value)
            edit_entry.destroy()

            if info.get("processed"):
                info["fix_pending"] = True
                self._append_to_journal(
                    "Change pending — will be applied on the next click on 'Apply'."
                )

            self.table.item(item_id, values=self._build_row_values(info))

        edit_entry.bind("<Return>", confirm)
        edit_entry.bind("<FocusOut>", confirm)
        edit_entry.bind("<Escape>", lambda e: edit_entry.destroy())
        self._bind_entry_context_menu(edit_entry)

    # --- Log / progress (thread-safe) ---

    def _append_to_journal(self, text):
        self.message_queue.put(("log", text))

    def _update_progress(self, index, total):
        self.message_queue.put(("progress", (index, total)))

    def _file_processed(self, identifier, success):
        self.message_queue.put(("file_processed", identifier))

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
                    self._update_progress_bar(index / total if total else 0, f"{percentage} %")

                elif message_type == "done":
                    cancelled = content
                    self.processing_in_progress = False
                    self._set_buttons_enabled(True)
                    self._update_progress_bar(1.0 if not cancelled else 0, "Cancelled" if cancelled else "Done ✓")
                    if not cancelled:
                        self._show_success_dialog()

                elif message_type == "file_scanned":
                    self._add_scan_row(content)

                elif message_type == "scan_progress":
                    scanned_count, total = content
                    self.scan_button.configure(text=f"Scan - {scanned_count}/{total}")

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
                        messagebox.showwarning(
                            "SoundCloud rate limit reached",
                            "SoundCloud's request limit has been reached for now.\n"
                            "No cover will be fetched for this scan — try again later.",
                            parent=self.window,
                        )

                elif message_type == "scan_done":
                    self._finalize_scan(content)

                elif message_type == "extract_done":
                    folder, moved_count, removed_count, error = content
                    self.extract_browse_button.configure(state="normal")
                    self.extract_button.configure(state="normal")

                    if error:
                        messagebox.showerror("Extraction error", error, parent=self.window)
                    else:
                        messagebox.showinfo(
                            "Extraction complete",
                            f"{moved_count} file(s) extracted, {removed_count} empty folder(s) removed.",
                            parent=self.window,
                        )
                        try:
                            os.startfile(folder)
                        except Exception:
                            pass

                elif message_type == "internet_status":
                    is_online, is_startup_check = content
                    if is_online:
                        self.internet_status_label.configure(text="● Online", foreground="#2ecc71")
                    else:
                        self.internet_status_label.configure(text="● Offline", foreground="#e74c3c")
                        if is_startup_check:
                            messagebox.showwarning(
                                "No internet connection",
                                "No internet connection was detected.\n\nOnline cover search "
                                "(iTunes/Spotify/SoundCloud) won't be available until your "
                                "connection is restored.",
                                parent=self.window,
                            )

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
                            "Up to date", f"You already have the latest version (v{tagger.APP_VERSION}).",
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
                    success = content
                    if success:
                        self._append_to_journal("Track reported, thanks!")
                    else:
                        messagebox.showerror(
                            "Report failed", "Could not send the report - check your internet connection and try again.",
                            parent=self.window,
                        )

                elif message_type == "file_processed":
                    identifier = content
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

        to_process = [i for i in self.scanned_plan if not i.get("processed")]
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

        conversions_count = sum(
            1 for i in to_process if i["format"] != "MP3" and i.get("convert")
        )
        if conversions_count:
            confirmed = messagebox.askyesno(
                "Confirm conversion",
                f"{conversions_count} file(s) will be converted to MP3 (320 kbps).\n"
                "This takes noticeably longer than just updating tags.\n\n"
                "Continue?",
                parent=self.window,
            )
            if not confirmed:
                return

        folder = self.folder_variable.get().strip()
        if folder:
            tagger.MUSIC_FOLDER = folder
        self._sync_mentions_to_remove()

        if not self.progress_canvas.winfo_ismapped():
            self.progress_canvas.pack(fill="x")
            self._adjust_window_height()

        self.journal_text.configure(state="normal")
        self.journal_text.delete("1.0", "end")
        self.journal_text.configure(state="disabled")

        self._update_progress_bar(0, "0 %")
        self.processing_in_progress = True
        self.cancel_requested.clear()

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

                if final_artist and final_title:
                    try:
                        tagger.fix_title_artist(info, final_artist, final_title)
                        self._append_to_journal(f"Fix applied: '{final_artist} - {final_title}'")
                        info["fix_pending"] = False
                    except Exception as error:
                        self._append_to_journal(f"Error while applying fix: {error}")

                self.message_queue.put(("file_processed", info["file"]))

        except Exception as error:
            self._append_to_journal(f"Unexpected error: {error}")
        finally:
            self.message_queue.put(("done", self.cancel_requested.is_set()))


if __name__ == "__main__":
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
