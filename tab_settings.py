"""Settings tab - split out of interface.py (see TaggerInterface)."""
import os
import re
import webbrowser
from tkinter import ttk, messagebox

import track_tidy as tagger
from ui_common import (
    open_with_default_app,
    MUTED_TEXT_COLOR,
    LINK_ACCENT_COLOR,
)


class SettingsTabMixin:
    def _build_settings_tab(self, soundcloud_tab):
        # ============================== Settings tab ==============================

        appearance_frame = ttk.LabelFrame(soundcloud_tab, text="Appearance")
        appearance_frame.pack(fill="x", padx=10, pady=(15, 10))
        self._theme_radio_buttons = {}
        for value, label in (("auto", "Auto"), ("light", "Light"), ("dark", "Dark")):
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
        ).pack(fill="x", padx=10, pady=(0, 5))
        ttk.Checkbutton(
            app_frame, text="Send anonymous usage telemetry", variable=self.use_telemetry_var,
            command=self._on_use_telemetry_changed,
        ).pack(anchor="w", padx=10, pady=(0, 10))

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
            foreground=LINK_ACCENT_COLOR, cursor="hand2", font=("TkDefaultFont", 8),
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
            style="Credit.TLabel",
        )
        self.dev_credit_label.pack(side="left")
        self.kevz_credit_label = ttk.Label(
            credit_frame, text="KEVZ", foreground=MUTED_TEXT_COLOR, font=("TkDefaultFont", 8, "bold"), cursor="hand2",
            style="Credit.TLabel",
        )
        self.kevz_credit_label.pack(side="left")
        self.kevz_credit_label.bind("<Button-1>", self._open_kevz_instagram)

        ttk.Separator(soundcloud_tab, orient="horizontal").pack(fill="x", padx=10, pady=(20, 10), side="bottom")


    def _on_auto_convert_changed(self):
        enabled = self.auto_convert_var.get()
        if enabled and self.auto_convert_wav_aiff_var.get():
            self.auto_convert_wav_aiff_var.set(False)
            tagger.AUTO_CONVERT_WAV_TO_AIFF = False
            tagger.save_setting("auto_convert_wav_to_aiff", False)
            # The checkbox unchecking itself is already the visible
            # confirmation - a popup on top of that was redundant.
            self._append_to_journal(
                "\"Convert WAV to AIFF\" turned off (mutually exclusive with "
                "\"Convert everything to MP3\")."
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
            self._append_to_journal(
                "\"Convert everything to MP3\" turned off (mutually exclusive "
                "with \"Convert WAV to AIFF\")."
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

    def _on_use_telemetry_changed(self):
        enabled = self.use_telemetry_var.get()
        tagger.SEND_USAGE_TELEMETRY = enabled
        tagger.save_setting("send_usage_telemetry", enabled)
        tagger.log_action(f"Send usage telemetry: {enabled}")

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

    def _check_for_update_manual(self):
        """Same check as on startup, but always reports back (up to date /
        failed / available) since the user explicitly asked for it here."""
        self.check_update_button.configure(state="disabled", text="Checking...")

        def _run_check():
            result = tagger.check_for_update()
            self.message_queue.put(("manual_update_check_result", result))

        self._run_in_background(_run_check)

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
