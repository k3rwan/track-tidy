"""Extractor tab - split out of interface.py (see TaggerInterface)."""
import getpass
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk

import track_tidy as tagger
from ui_common import (
    resource_path,
    LINK_ACCENT_COLOR,
)


class ExtractorTabMixin:
    def _build_extractor_tab(self, extractor_tab):
        # ============================== Extractor tab ==============================

        # Same "Parent folder:" LabelFrame + icon + entry-row structure as
        # the Tagger tab's own folder picker (folder_frame above) - was
        # previously a bare Label + ungrouped Entry/buttons here.
        extract_folder_frame = ttk.LabelFrame(extractor_tab, text="Folder to flatten:")
        extract_folder_frame.pack(fill="x", padx=10, pady=(10, 2))

        # "ⓘ" placed inside the LabelFrame's top-right corner - see
        # tagger_info_icon above.
        extractor_info_icon = ttk.Label(extract_folder_frame, text="ⓘ", foreground=LINK_ACCENT_COLOR, cursor="hand2")
        extractor_info_icon.place(relx=1.0, x=-6, y=-18, anchor="ne")
        extractor_info_text = (
            "Flattens a messy music folder: audio files buried in nested\n"
            "subfolders move straight into the folder below, and any\n"
            "subfolders left empty are cleaned up automatically.\n"
            "\n"
            "Works on every common audio format (MP3, WAV, FLAC, AAC, M4A,\n"
            "OGG, WMA...). Files already directly inside the folder are left\n"
            "alone."
        )
        extractor_info_icon.bind("<Enter>", lambda e: self._show_tooltip(extractor_info_text, e))
        extractor_info_icon.bind("<Leave>", lambda e: self._hide_tooltip())

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

    def _choose_extract_folder(self):
        folder = filedialog.askdirectory(title="Choose the folder to flatten")
        if folder:
            self._sync_all_folder_pickers(folder)

    def _setup_extractor_drag_and_drop(self):
        """Same drag-and-drop mechanism as Tagger/Quality's own (see
        _setup_drag_and_drop / _setup_quality_drag_and_drop) - without
        this, the Extractor tab had none of its own, so a drop while
        viewing it fell through to Tagger's window/notebook-level
        registration and silently started a Tagger scan instead (same bug
        already found and fixed for Quality). Scoped to this tab's own
        widgets so a drop landing here is caught before it can fall
        through. Silently does nothing if tkinterdnd2 isn't installed.

        Deliberately only fills the folder field (like Browse... does),
        NOT an immediate _start_extraction() the way Tagger's drop starts
        a scan or Quality's starts an analysis - both of those are
        read-only previews the user can still back out of, but extraction
        actually moves files on disk the moment it runs, with no review
        step first. A stray drop shouldn't be able to restructure a
        folder without the user then deliberately clicking Extract."""
        try:
            from tkinterdnd2 import DND_FILES
        except ImportError:
            return

        for widget in (
            self.extractor_tab, self.extractor_preview_frame,
            self.extractor_preview_before_label, self.extractor_preview_after_label,
        ):
            try:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_extractor_files_dropped)
            except Exception:
                pass  # not fatal - the app works fine without drag-and-drop

    def _on_extractor_files_dropped(self, event):
        if str(self.extract_browse_button.cget("state")) == "disabled":
            self._append_to_journal("Ignored dropped file(s) - wait for the current extraction to finish first.")
            return

        raw_paths = self.window.tk.splitlist(event.data)
        if not raw_paths:
            return

        first_path = os.path.normpath(raw_paths[0].strip("{}"))
        # Extraction flattens a FOLDER's subfolders - there's no per-file
        # equivalent of Tagger/Quality's own single-file handling, so a
        # dropped file resolves to the folder it's in, same folder
        # Browse... would land you in by picking it yourself.
        folder = first_path if os.path.isdir(first_path) else os.path.dirname(first_path)
        if not os.path.isdir(folder):
            return

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
            moved_count, failed_count = tagger.extract_audio_files(
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
            self.message_queue.put(("extract_done", (folder, moved_count, removed_count, failed_count, cancelled, None)))
        except Exception as error:
            tagger.send_extraction_report(reporter_name=reporter_name, error=str(error))
            self.message_queue.put(("extract_done", (folder, 0, 0, 0, False, str(error))))

    def _reset_extract(self):
        """Extractor has no persistent results table like Tagger/Quality -
        it's fire-and-forget, with the outcome left on the progress bar's
        own final label (see "extract_done") - so there's nothing left to
        clear except that still-visible progress bar/button state. Mainly
        here so Extractor isn't the only tab without a Reset, matching
        what's asked for. Doesn't touch the chosen folder - same as
        _reset_app not touching folder_variable. No processing_in_progress-
        style guard needed: that flag only tracks Tagger's own Apply run,
        and extract_reset_button is already disabled for the whole
        duration of an extraction (see _start_extraction/"extract_done")."""
        self.extract_progress_canvas.pack_forget()
        self._update_progress_bar(self.extract_progress_canvas, 0, "")
        self.extract_button.configure(text="Extract", command=self._start_extraction)
