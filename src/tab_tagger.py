"""Tagger tab - split out of interface.py (see TaggerInterface)."""
import getpass
import io
import os
import re
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk, filedialog, scrolledtext, messagebox
from PIL import Image, ImageTk, ImageDraw

import track_tidy as tagger
from ui_common import (
    open_with_default_app,
    reveal_in_file_manager,
    play_short_sound,
    resource_path,
    CHECKED_BOX,
    EMPTY_BOX,
    PROCESSED_CHECK,
    ALREADY_APPLIED_MARK,
    SCAN_REVEAL_INTERVAL_MS,
    MAX_TRACKS_PER_SCAN,
    MAX_UNDO_STACK_SIZE,
    NO_COVER_REPORT_THRESHOLD,
    THUMBNAIL_SIZE,
    TABLE_ROW_HEIGHT,
    COLUMNS,
    NO_COVER_SUMMARY_ROW_ID,
    SEARCH_RESULT_SUMMARY_ROW_ID,
    LINK_ACCENT_COLOR,
    setup_placeholder,
)


class TaggerTabMixin:
    def _build_tagger_tab(self, tagger_tab):
        # ============================== Tagger tab ==============================

        # --- Folder selection ---
        folder_frame = ttk.LabelFrame(tagger_tab, text="Parent folder:")
        folder_frame.pack(fill="x", padx=10, pady=(10, 2))

        # "ⓘ" placed (not packed) inside the LabelFrame itself, top-right
        # corner - see quality_info_icon's original placement for the style
        # (blue/hand2 clickable look); its tooltip holds the tool
        # description, kept out of a permanently-visible label so the tab
        # isn't cluttered with text every time it's opened.
        tagger_info_icon = ttk.Label(folder_frame, text="ⓘ", foreground=LINK_ACCENT_COLOR, cursor="hand2")
        tagger_info_icon.place(relx=1.0, x=-6, y=-18, anchor="ne")
        tagger_info_text = (
            "Matches tracks in a folder against online catalogs to fill in\n"
            "missing cover art, artist, and title tags.\n"
            "\n"
            "Automated matching isn't perfect - review the suggested\n"
            "Artist/Title in the table before clicking Apply, especially any\n"
            "low-confidence or \U0001f3a7 AcoustID-identified rows."
        )
        tagger_info_icon.bind("<Enter>", lambda e: self._show_tooltip(tagger_info_text, e))
        tagger_info_icon.bind("<Leave>", lambda e: self._hide_tooltip())

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

        self.advanced_toggle = ttk.Label(tagger_tab, text="▸ ⚙️", cursor="hand2", foreground=LINK_ACCENT_COLOR)
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

        self.journal_toggle = ttk.Label(tagger_tab, text="▸ Log", cursor="hand2", foreground=LINK_ACCENT_COLOR)
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


    def _sync_mentions_to_remove(self):
        """Pushes the current "To remove" listbox contents to the tagger
        module - a Tk widget read, so it must happen on the main thread,
        before handing off to any background scan/rescan thread that reads
        tagger.MENTIONS_TO_REMOVE."""
        tagger.MENTIONS_TO_REMOVE = list(self.mentions_listbox.get(0, "end"))

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
        # Browse/Scan/Apply are already disabled while a run is active (see
        # _is_run_active) precisely so the folder/table can't change out
        # from under a running scan or Apply - but drag-and-drop is a raw
        # window-level event binding, not gated by any button's state, so
        # without this check a drop here could still switch
        # tagger.MUSIC_FOLDER (and clear/replace scanned_plan) while a scan
        # or Apply thread is mid-run against the OLD folder, corrupting
        # that thread's path resolution for the rest of its run.
        if self._is_run_active():
            self._append_to_journal("Ignored dropped file(s) - wait for the current run to finish first.")
            return

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
            if folder == getattr(self, "last_scanned_folder", None) and any(
                info["file"] == relative_name for info in self.scanned_plan
            ):
                self._append_to_journal(f"Ignored '{relative_name}' - already in the table.")
                continue
            relative_names.append(relative_name)

        if not relative_names:
            return

        relative_names = self._apply_track_count_limit(relative_names)

        # Switching to a different folder than whatever's currently shown
        # must drop the old rows first - otherwise tagger.MUSIC_FOLDER
        # below now points at the new folder while a stale row from the
        # old one is still in scanned_plan/the table, so any action on
        # that row (Play, Apply, cover edit...) would resolve against the
        # WRONG file. Mirrors the equivalent check in
        # _start_dropped_folder_scan just above.
        if folder != getattr(self, "last_scanned_folder", None):
            for row in self.table.get_children():
                self.table.delete(row)
            self.tk_images.clear()
            self.tk_images_hover.clear()
            self._thumbnail_pil_images.clear()
            self.scanned_plan = []
            self._update_empty_state_hint()

        tagger.MUSIC_FOLDER = folder
        self.last_scanned_folder = folder
        self._reset_scan_run_state()

        self.notebook.select(0)
        self._set_buttons_enabled(False)
        self._show_scan_progress_bar()
        self._run_in_background(self._run_scan, relative_names)

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

    def _choose_folder(self):
        """Windows' native folder-browser dialog (which askdirectory uses)
        never shows files, only folders - a Tk/Windows limitation, not
        something this app can turn on. As the next best thing, log how
        many audio files are actually in the chosen folder right away,
        instead of only finding out once Scan is clicked."""
        folder = filedialog.askdirectory(title="Choose the audio files folder")
        if folder:
            self._apply_picked_folder(folder)

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
        # Otherwise Ctrl+Z right after a Reset can resurrect a row removed
        # from the PREVIOUS folder into a table that's supposed to
        # represent the new (or no) folder, with a path that may no
        # longer even resolve against tagger.MUSIC_FOLDER.
        self._undo_stack = []
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
            foreground=LINK_ACCENT_COLOR if enabled else "#999999", cursor="hand2" if enabled else "arrow",
        )
        if enabled:
            self.scan_button.configure(text="Scan")
            self._update_apply_button_label()
            self.apply_button.configure(text="Apply", command=self._start_processing, state="normal")
        else:
            self.cancel_requested.clear()
            self.apply_button.configure(text="Cancel", command=self._request_cancel, state="normal")
        self._set_tabs_locked(not enabled)

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
                    # Neutral, expected outcome (not an error) - a log line
                    # is enough, no popup needed to click through.
                    self._append_to_journal("Every track in this folder has already been scanned before.")
                    return

        self._show_scan_progress_bar()
        self._run_in_background(self._run_scan, files_to_scan)

    def _ask_scan_mode(self, already_scanned_count, new_count):
        """Small choice dialog shown when some of the files about to be
        scanned have already been scanned before - "Scan only new tracks"
        is always the pre-selected default (even with zero new tracks;
        clicking Scan as-is then just logs "nothing new to scan" - see the
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
        # clicking "Scan" as-is then just logs "nothing new to scan" (see
        # the caller), which is a clearer outcome than silently switching
        # the default to "Rescan everything" underneath the user without
        # them choosing that.
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
        try:
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
        except Exception:
            # This ticks for the app's entire lifetime - a bug in any one
            # row/finalize call must not silently stop it forever. See the
            # identical guard on _reveal_next_quality_row and _start_message_loop.
            self._report_crash(*sys.exc_info(), context="scan_reveal_loop")
        finally:
            self.window.after(SCAN_REVEAL_INTERVAL_MS, self._reveal_next_scan_row)

    def _update_empty_state_hint(self):
        """Shows the drag-and-drop/select-a-folder hint centered over the
        table only while it has no rows at all - called after every table
        mutation that could take it to (or from) zero rows."""
        if self.table.get_children():
            self.empty_state_frame.place_forget()
        else:
            self.empty_state_frame.place(relx=0.5, rely=0.5, anchor="center")

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

    def _flash_new_row(self, file_iid, tree=None, final_tags=None):
        """Reusable across both the Tagger table and the Quality table
        (tree=self.quality_table) - same accent-tint-to-normal-color flash,
        just parameterized over which Treeview and which tags the row
        should settle back into (defaults match the Tagger table's own
        original always-plain-striped behavior)."""
        tree = tree if tree is not None else self.table
        start_color = self.theme_colors["select_bg"]
        is_even = tree.index(file_iid) % 2 == 0
        end_color = self.theme_colors["tree_bg"] if is_even else self.theme_colors["tree_odd_row"]
        fg_color = self.theme_colors["tree_fg"]

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

        end_color = self.theme_colors["bg"]
        fg_color = self.theme_colors["tree_fg"]

        pending = {}
        for item_id in item_ids:
            if not self.table.exists(item_id):
                continue
            is_even = self.table.index(item_id) % 2 == 0
            start_color = self.theme_colors["tree_bg"] if is_even else self.theme_colors["tree_odd_row"]
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

    def _acoustid_marker(self, info):
        """"🎧" for a row AcoustID identified from the audio itself (not the
        filename/tags) that the user hasn't reviewed yet - a real, if
        uncommon, way for it to be confidently (high score) wrong: two
        different tracks/remixes in similar genres (e.g. house/techno) can
        fingerprint close enough to collide. Flagged so the user knows to
        double check it before trusting Apply - cleared once they've
        actually reviewed it (title_override no longer None, whether they
        kept it or corrected it). Shared by the main table (_build_row_values)
        and the track info dialog (_show_track_info)."""
        return " 🎧" if info.get("acoustid_identified") and info["title_override"] is None else ""

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

        acoustid_marker = self._acoustid_marker(info)
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
            # "is not None" (not a plain truthy/"or" check) so a title/artist
            # deliberately cleared to "" - a real override, not "no
            # override yet" - shows as "(empty)" instead of silently
            # falling back to the old suggestion (real report).
            if info["title_override"] is not None:
                displayed_title = info["title_override"] or "(empty)"
            else:
                displayed_title = info["detected_title"] or "?"
            displayed_title += acoustid_marker + no_cover_marker
            if info["artist_override"] is not None:
                displayed_artist = info["artist_override"] or "(empty)"
            else:
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
            displayed_title = info["title_override"] or "(empty)"
        elif apply:
            displayed_title = (info["detected_title"] or "?") + acoustid_marker + no_cover_marker
        else:
            displayed_title = info["current_title"] or "(empty)"

        if info["artist_override"] is not None:
            displayed_artist = info["artist_override"] or "(empty)"
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
        self._push_undo("removal", removed)
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

    def _push_undo(self, action_type, payload):
        """Records one Ctrl+Z step (a row removal or a cell edit), capping
        how far back a session can accumulate - nobody undoes more than a
        handful of steps back in practice, so this is just a bound on
        otherwise-unlimited memory growth over a long session, not a
        real usage limit."""
        self._undo_stack.append((action_type, payload))
        if len(self._undo_stack) > MAX_UNDO_STACK_SIZE:
            del self._undo_stack[: len(self._undo_stack) - MAX_UNDO_STACK_SIZE]

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
        full_path = self._resolve_full_path(info)

        if not os.path.exists(full_path):
            self._append_to_journal(f"Can't open location, file not found: '{full_path}'")
            messagebox.showwarning(
                "File not found",
                "This file isn't available anymore (moved, renamed, or deleted since the scan).",
                parent=self.window,
            )
            return

        try:
            reveal_in_file_manager(full_path)
        except Exception as error:
            self._append_to_journal(f"Error opening file location: {error}")
            messagebox.showerror("Could not open file location", str(error), parent=self.window)

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
            suggested_display = tagger.build_display_name(suggested_artist, suggested_title) + self._acoustid_marker(info)
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

    def _raw_field_value(self, info, field):
        """Same title/artist fallback chain as _build_row_values, but never
        with the ⚠️/🎧 review markers appended - used to seed the inline-edit
        box, since editing a still-unreviewed row must not bake that marker
        glyph into the saved override text (it used to: the edit box was
        pre-filled from the marker-decorated display string, so unless the
        user retyped the whole title rather than tweaking part of it, the
        emoji rode along into title_override and got applied to the file)."""
        if info.get("processed"):
            if field == "title":
                title_override = info["title_override"]
                return title_override if title_override is not None else (info["detected_title"] or "?")
            override = info["artist_override"]
            return override if override is not None else (info["detected_artist"] or "(empty)")

        override = info[f"{field}_override"]
        if override is not None:
            return override
        if info["apply_changes"]:
            if field == "title":
                return info["detected_title"] or "?"
            return info["detected_artist"] if info["detected_artist"] else "(empty)"
        return info[f"current_{field}"] or "(empty)"

    def _edit_cell(self, item_id, info, field, column_id):
        """Opens an input field directly on the cell to edit title/artist."""
        bbox = self.table.bbox(item_id, column_id)
        if not bbox:
            return
        x, y, width, height = bbox

        current_value = self._raw_field_value(info, field)

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
            # Deliberately kept as "" rather than collapsed to None when the
            # user clears the field entirely - None means "no override yet,
            # follow the auto-suggestion", so collapsing an intentionally-
            # emptied field to None silently reverted it back to the
            # previous/suggested value instead of actually clearing it, on
            # both a fresh row and one already processed (real report).
            # Ctrl+Z (_undo_edit) is the actual "I want it back" escape
            # hatch, so there's no need for blank-to-revert as well.
            if new_value != info.get(f"{field}_override"):
                self._push_undo("edit", {
                    "info": info, "field": field,
                    "old_override": info.get(f"{field}_override"),
                    "old_override_is_manual": info.get(f"{field}_override_is_manual"),
                    "old_fix_pending": info.get("fix_pending"),
                })
            info[f"{field}_override"] = new_value
            info[f"{field}_override_is_manual"] = True
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

        # One combined confirmation instead of up to 3 stacked dialogs
        # (base confirm, filter warning, conversion warning) - same
        # "combine every applicable notice into one dialog" pattern as
        # _finalize_scan's rate-limit warning below.
        paragraphs = [
            "This will overwrite the original artist/title/cover info for every "
            "selected track.\n\nThe original values are saved in the processing "
            "history and can be restored from there if needed."
        ]

        if self._is_filter_active():
            visible_ids = set(self.table.get_children())
            hidden_count = sum(1 for i in to_process + fixes if i["file"] not in visible_ids)
            if hidden_count:
                paragraphs.append(
                    f"{hidden_count} track(s) are hidden by the current filter and will also be processed."
                )

        to_convert = [i for i in to_process if i["format"] != "MP3" and i.get("convert")]
        mp3_count = sum(1 for i in to_convert if tagger._resolve_conversion_target(i["file"]) == "mp3")
        aiff_count = len(to_convert) - mp3_count
        if to_convert:
            parts = []
            if mp3_count:
                parts.append(f"{mp3_count} file(s) to MP3 (320 kbps, takes noticeably longer than just updating tags)")
            if aiff_count:
                parts.append(f"{aiff_count} file(s) to AIFF (lossless, quick)")
            paragraphs.append("Will also convert " + ", ".join(parts) + ".")

        confirmed = messagebox.askyesno(
            "Apply changes?", "\n\n".join(paragraphs) + "\n\nContinue?", parent=self.window,
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
