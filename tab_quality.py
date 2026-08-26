"""Quality tab - split out of interface.py (see TaggerInterface)."""
import getpass
import io
import os
import re
import subprocess
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
from PIL import Image, ImageTk, ImageDraw

import track_tidy as tagger
from interface import (
    reveal_in_file_manager,
    SCAN_REVEAL_INTERVAL_MS,
    MUTED_TEXT_COLOR,
    DARK_MUTED_TEXT_COLOR,
)


class QualityTabMixin:
    def _build_quality_tab(self, quality_tab):
        # ============================== Quality tab ==============================

        # Same LabelFrame + icon + entry-row structure as Tagger's own
        # folder_frame (and now Extractor's extract_folder_frame above).
        quality_folder_frame = ttk.LabelFrame(quality_tab, text="Folder to analyze:")
        quality_folder_frame.pack(fill="x", padx=10, pady=(10, 2))

        # "ⓘ" placed inside the LabelFrame's top-right corner - see
        # tagger_info_icon above. Blue/hand2 clickable look matches the "▸"
        # toggle labels used elsewhere (advanced_toggle, journal_toggle)
        # instead of introducing a new affordance style just for this tab.
        quality_info_icon = ttk.Label(quality_folder_frame, text="ⓘ", foreground="#1a73e8", cursor="hand2")
        quality_info_icon.place(relx=1.0, x=-6, y=-18, anchor="ne")
        quality_info_text = (
            "Flags tracks whose real audio doesn't match their declared\n"
            "format/bitrate.\n"
            "\n"
            "Best-effort ESTIMATE, not a certainty. A real track can\n"
            "legitimately roll off high frequencies (mastering, genre), and\n"
            "some lossy sources don't show a detectable trace at all - treat\n"
            "orange/red as \"worth a listen\", not proof."
        )
        quality_info_icon.bind("<Enter>", lambda e: self._show_tooltip(quality_info_text, e))
        quality_info_icon.bind("<Leave>", lambda e: self._hide_tooltip())

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
            quality_buttons_frame, text="Analyze", command=self._start_quality_scan
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
            text="Analyze", command=self._start_quality_scan, state="normal",
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
        self.quality_scan_button.configure(text="Analyze")

        self.quality_progress_canvas.pack_forget()
        self._update_progress_bar(self.quality_progress_canvas, 0, "")

        self._adjust_window_height()

    def _update_quality_empty_state_hint(self):
        """Same idea as _update_empty_state_hint, for the Quality tab's own
        table: explains what the green/orange/red dot means while there's
        nothing scanned yet to show it on directly."""
        if self.quality_table.get_children():
            self.quality_empty_state_frame.place_forget()
        else:
            self.quality_empty_state_frame.place(relx=0.5, rely=0.5, anchor="center")
