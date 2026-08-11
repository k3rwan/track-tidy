"""
Unit tests for the pure parsing/matching logic in track_tidy.py, plus the
history log (isolated to a temp file - no network, no GUI).
Run with: python -m unittest discover -s tests
"""

import os
import sys
import json
import shutil
import unittest
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import track_tidy as tagger


class ParseFilenameTests(unittest.TestCase):
    def test_standard_artist_dash_title(self):
        self.assertEqual(tagger.parse_filename("Daft Punk - One More Time.mp3"), ("Daft Punk", "One More Time"))

    def test_drops_leading_track_number(self):
        self.assertEqual(tagger.parse_filename("01. Daft Punk - One More Time.mp3"), ("Daft Punk", "One More Time"))
        self.assertEqual(tagger.parse_filename("076 - Daft Punk - One More Time.mp3"), ("Daft Punk", "One More Time"))

    def test_trailing_dash_mix_reformatted(self):
        self.assertEqual(
            tagger.parse_filename("Vicetone - Astronomia - Original Mix.mp3"),
            ("Vicetone", "Astronomia (Original Mix)"),
        )

    def test_title_mix_artist_three_parts(self):
        self.assertEqual(
            tagger.parse_filename("My City's On Fire - Notre Dame Remix - Jimi Jules.mp3"),
            ("Jimi Jules", "My City's On Fire (Notre Dame Remix)"),
        )

    def test_no_dash_returns_none(self):
        self.assertEqual(tagger.parse_filename("JustATitleNoArtist.mp3"), (None, None))

    def test_entirely_lowercase_gets_titlecased(self):
        artist, title = tagger.parse_filename("daft_punk - one_more_time.mp3")
        self.assertEqual((artist, title), ("Daft Punk", "One More Time"))

    def test_mixed_case_left_alone(self):
        # Has an uppercase letter already -> not touched, to protect stylized names.
        artist, title = tagger.parse_filename("SCH - Otto.mp3")
        self.assertEqual((artist, title), ("SCH", "Otto"))


class CleanTitleTests(unittest.TestCase):
    def setUp(self):
        self._original_mentions = list(tagger.MENTIONS_TO_REMOVE)

    def tearDown(self):
        tagger.MENTIONS_TO_REMOVE[:] = self._original_mentions

    def test_removes_configured_mention_case_insensitive(self):
        tagger.MENTIONS_TO_REMOVE[:] = ["By Fuvi Clan"]
        self.assertEqual(tagger.clean_title("Agent Secret (Extended Mix by fuvi clan)"), "Agent Secret (Extended Mix)")

    def test_noop_when_no_mentions_configured(self):
        tagger.MENTIONS_TO_REMOVE[:] = []
        self.assertEqual(tagger.clean_title("Untouched Title"), "Untouched Title")


class StripHelpersTests(unittest.TestCase):
    def test_strip_generic_mix_suffix_removes_generic_label(self):
        self.assertEqual(tagger.strip_generic_mix_suffix("Astronomia (Extended Mix)"), "Astronomia")
        self.assertEqual(tagger.strip_generic_mix_suffix("Astronomia (Original Mix)"), "Astronomia")

    def test_strip_generic_mix_suffix_keeps_named_remix(self):
        self.assertEqual(tagger.strip_generic_mix_suffix("Astronomia (DJ Snake Remix)"), "Astronomia (DJ Snake Remix)")

    def test_strip_feature_suffix_removes_feat_credit(self):
        self.assertEqual(
            tagger.strip_feature_suffix("C'est quoi le boulot (feat. Heuss L'enfoiré)"),
            "C'est quoi le boulot",
        )
        self.assertEqual(tagger.strip_feature_suffix("Yeah! (feat. Lil Jon & Ludacris)"), "Yeah!")

    def test_strip_feature_suffix_leaves_plain_title_alone(self):
        self.assertEqual(tagger.strip_feature_suffix("Astronomia"), "Astronomia")

    def test_strip_parentheses_removes_all_bracketed_groups(self):
        self.assertEqual(
            tagger.strip_parentheses("Water (Dj Nasty Remix) [AMAPIANO]"),
            "Water",
        )


class ExactMatchTests(unittest.TestCase):
    def test_case_and_whitespace_insensitive(self):
        self.assertTrue(tagger.exact_match("  Astronomia ", "astronomia"))

    def test_different_text_not_equal(self):
        self.assertFalse(tagger.exact_match("Astronomia", "Astronomia (Remix)"))

    def test_accent_insensitive(self):
        self.assertTrue(tagger.exact_match("Ete", "Été"))


class ArtistMatchingTests(unittest.TestCase):
    def test_split_artist_names_common_separators(self):
        self.assertEqual(
            tagger.split_artist_names("A, B & C"),
            tagger.split_artist_names("C x A vs B"),
        )

    def test_artist_sets_match_ignores_order_and_separators(self):
        self.assertTrue(tagger.artist_sets_match("Tujamo, Sidney Samson", "Sidney Samson & Tujamo"))

    def test_artist_sets_match_rejects_different_artists(self):
        self.assertFalse(tagger.artist_sets_match("Tujamo", "Sidney Samson"))

    def test_artist_names_match_substring_either_direction(self):
        self.assertTrue(tagger.artist_names_match("Vegedream", "Vegedream, Naps"))
        self.assertTrue(tagger.artist_names_match("Vegedream, Naps", "Vegedream"))

    def test_artist_names_match_rejects_unrelated_artist(self):
        self.assertFalse(tagger.artist_names_match("Vegedream", "Naps"))

    def test_artist_names_match_no_returned_artist(self):
        self.assertFalse(tagger.artist_names_match("Vegedream", ""))

    def test_strip_sanitized_chars_removes_forbidden_windows_chars_and_underscore(self):
        self.assertEqual(tagger.strip_sanitized_chars('blond_ish'), "blondish")
        self.assertEqual(tagger.strip_sanitized_chars('blond:ish'), "blondish")
        self.assertEqual(tagger.strip_sanitized_chars('a/b\\c*d?e"f<g>h|i'), "abcdefghi")

    def test_artist_sets_match_tolerates_sanitized_colon(self):
        # "BLOND:ISH" can't appear in a Windows filename/tag copied from one -
        # sanitize_filename() turns it into "BLOND_ISH". The real iTunes
        # credit still has the colon, plus a different artist order.
        self.assertTrue(tagger.artist_sets_match(
            "BLOND_ISH, Amadou & Mariam, Francis Mercier",
            "BLOND:ISH, Francis Mercier & Amadou & Mariam",
        ))

    def test_artist_names_match_tolerates_sanitized_colon(self):
        self.assertTrue(tagger.artist_names_match("BLOND_ISH", "BLOND:ISH, Francis Mercier"))

    def test_strip_accents(self):
        self.assertEqual(tagger.strip_accents("Bolémvn"), "Bolemvn")
        self.assertEqual(tagger.strip_accents("Été"), "Ete")
        self.assertEqual(tagger.strip_accents("Plain"), "Plain")

    def test_artist_sets_match_tolerates_missing_accent(self):
        # Our tag/filename dropped the accent ("Bolemvn"), but iTunes' real
        # credit has it ("Bolémvn") - same artist.
        self.assertTrue(tagger.artist_sets_match("Bolemvn, Jungeli", "Bolémvn & Jungeli"))

    def test_artist_names_match_tolerates_missing_accent(self):
        self.assertTrue(tagger.artist_names_match("Bolemvn", "Bolémvn & Jungeli"))

    def test_extract_feature_names_from_title_suffix(self):
        self.assertEqual(tagger.extract_feature_names("Gucci Slides (feat. Kxne)"), {"kxne"})
        self.assertEqual(tagger.extract_feature_names("Gucci Slides [ft. A & B]"), {"a", "b"})
        self.assertEqual(tagger.extract_feature_names("Gucci Slides"), set())

    def test_artist_sets_match_folds_in_featured_artist_from_title(self):
        # A store often credits only the primary artist in the artist field,
        # crediting the featured one in the title instead - while our own
        # tags/filename list every artist together in the artist field.
        self.assertTrue(tagger.artist_sets_match(
            "Brandon, Kxne", "BRANDON", "Gucci Slides (feat. Kxne)",
        ))

    def test_artist_sets_match_still_rejects_unrelated_feature(self):
        self.assertFalse(tagger.artist_sets_match(
            "Brandon, Kxne", "BRANDON", "Gucci Slides (feat. Someone Else)",
        ))


class ITunesQueryTests(unittest.TestCase):
    def test_build_search_query_strips_punctuation(self):
        self.assertEqual(
            tagger.build_search_query("Bob Sinclar, Steve Edwards", "World Hold On (THEMBA Extended Remix)"),
            "Bob Sinclar Steve Edwards World Hold On THEMBA Extended Remix",
        )

    def test_strip_all_trailing_groups(self):
        core, groups = tagger.strip_all_trailing_groups(
            "World Hold On (Children Of The Sky) [feat. Steve Edwards & THEMBA] [THEMBA Extended Remix]"
        )
        self.assertEqual(core, "World Hold On")
        self.assertEqual(groups, ["THEMBA Extended Remix", "feat. Steve Edwards & THEMBA", "Children Of The Sky"])

    def test_strip_all_trailing_groups_no_groups(self):
        self.assertEqual(tagger.strip_all_trailing_groups("World Hold On"), ("World Hold On", []))

    def test_loose_remix_match_tolerates_extra_subtitle_and_feat_position(self):
        self.assertTrue(tagger.loose_remix_match(
            "World Hold On (THEMBA Extended Remix)",
            "World Hold On (Children Of The Sky) [feat. Steve Edwards & THEMBA] [THEMBA Extended Remix]",
        ))

    def test_loose_remix_match_rejects_different_remix(self):
        self.assertFalse(tagger.loose_remix_match(
            "World Hold On (THEMBA Extended Remix)",
            "World Hold On (Children Of The Sky) [feat. Steve Edwards] [FISHER Rework]",
        ))

    def test_loose_remix_match_rejects_different_core_title(self):
        self.assertFalse(tagger.loose_remix_match(
            "Some Other Song (THEMBA Extended Remix)",
            "World Hold On (Children Of The Sky) [THEMBA Extended Remix]",
        ))

    def test_extract_feature_names_from_groups(self):
        self.assertEqual(
            tagger.extract_feature_names_from_groups(["THEMBA Extended Remix", "feat. Steve Edwards & THEMBA"]),
            {"steve edwards", "themba"},
        )
        self.assertEqual(tagger.extract_feature_names_from_groups(["Extended Mix"]), set())


class SearchOneSourceTests(unittest.TestCase):
    """_search_one_source() applies one provider's own query strategy - no
    network involved, so the providers are swapped out with fakes."""

    def setUp(self):
        self._original_itunes = tagger.search_cover_itunes
        self._original_spotify = tagger.search_cover_spotify
        self._original_soundcloud = tagger.search_cover_soundcloud

    def tearDown(self):
        tagger.search_cover_itunes = self._original_itunes
        tagger.search_cover_spotify = self._original_spotify
        tagger.search_cover_soundcloud = self._original_soundcloud

    def test_itunes_tries_plain_title_first(self):
        calls = []

        def fake_itunes(artist, title, log=None, **kwargs):
            calls.append(title)
            return (b"cover", artist, title)

        tagger.search_cover_itunes = fake_itunes

        match_result, source = tagger._search_one_source(
            "itunes", "Artist", "Title", "Title", None, None, print
        )
        self.assertEqual(source, "iTunes")
        self.assertEqual(calls, ["Title"])
        self.assertIsNotNone(match_result)

    def test_itunes_retries_with_remix_qualified_title_on_miss(self):
        calls = []

        def fake_itunes(artist, title, log=None, **kwargs):
            calls.append(title)
            return (b"cover", artist, title) if title == "Title (Remix)" else None

        tagger.search_cover_itunes = fake_itunes

        match_result, source = tagger._search_one_source(
            "itunes", "Artist", "Title", "Title (Remix)", None, None, print
        )
        self.assertEqual(calls, ["Title", "Title (Remix)"])
        self.assertEqual(source, "iTunes")
        self.assertIsNotNone(match_result)

    def test_itunes_no_retry_when_remix_qualified_title_matches_plain(self):
        calls = []
        tagger.search_cover_itunes = lambda artist, title, log=None, **k: calls.append(title) or None

        match_result, source = tagger._search_one_source(
            "itunes", "Artist", "Title", "Title", None, None, print
        )
        self.assertEqual(calls, ["Title"])
        self.assertIsNone(match_result)
        self.assertIsNone(source)

    def test_spotify_tries_plain_then_remix_qualified_title(self):
        calls = []

        def fake_spotify(artist, title, token, log=None):
            calls.append(title)
            return (b"cover", artist, title) if title == "Title (Remix)" else None

        tagger.search_cover_spotify = fake_spotify

        match_result, source = tagger._search_one_source(
            "spotify", "Artist", "Title", "Title (Remix)", "token", None, print
        )
        self.assertEqual(calls, ["Title", "Title (Remix)"])
        self.assertEqual(source, "Spotify")
        self.assertIsNotNone(match_result)

    def test_soundcloud_uses_remix_qualified_title_directly(self):
        calls = []

        def fake_soundcloud(artist, title, token, log=None):
            calls.append(title)
            return (b"cover", artist, title)

        tagger.search_cover_soundcloud = fake_soundcloud

        match_result, source = tagger._search_one_source(
            "soundcloud", "Artist", "Title", "Title (Remix)", None, "token", print
        )
        # SoundCloud goes straight for the remix-qualified title - no
        # separate plain-title attempt first.
        self.assertEqual(calls, ["Title (Remix)"])
        self.assertEqual(source, "SoundCloud")
        self.assertIsNotNone(match_result)


class TitleWordsOverlapTests(unittest.TestCase):
    def test_shared_meaningful_word_overlaps(self):
        self.assertTrue(tagger.title_words_overlap("Je La Connais", "Je La Connais (Remix)"))

    def test_unrelated_titles_do_not_overlap(self):
        self.assertFalse(tagger.title_words_overlap("Je La Connais", "Saint Laurent"))

    def test_empty_expected_title_never_blocks(self):
        self.assertTrue(tagger.title_words_overlap("", "Anything"))


class FixSwappedArtistTitleTests(unittest.TestCase):
    def test_returns_unchanged_when_already_correct(self):
        result = tagger.fix_swapped_artist_title("Vald", "Dragon", "Vald", "Dragon")
        self.assertEqual(result, ("Vald", "Dragon"))

    def test_swaps_and_relocates_qualifier_to_title_side(self):
        # Filename put the title where the artist should be (and vice versa),
        # with a "(Original Mix)" qualifier stuck on the wrong (artist) side.
        artist, title = tagger.fix_swapped_artist_title(
            "Astronomia", "Vicetone (Original Mix)", returned_artist="Vicetone", returned_title="Astronomia"
        )
        self.assertEqual((artist, title), ("Vicetone", "Astronomia (Original Mix)"))

    def test_no_swap_when_titles_unrelated(self):
        result = tagger.fix_swapped_artist_title("Vald", "Dragon", "Someone Else", "Unrelated Song")
        self.assertEqual(result, ("Vald", "Dragon"))


class FilenameHelperTests(unittest.TestCase):
    def test_sanitize_filename_replaces_forbidden_characters(self):
        self.assertEqual(tagger.sanitize_filename('A:B*C?D"E<F>G|H'), "A_B_C_D_E_F_G_H")

    def test_build_display_name_with_and_without_artist(self):
        self.assertEqual(tagger.build_display_name("Vald", "Dragon"), "Vald - Dragon")
        self.assertEqual(tagger.build_display_name("", "Dragon"), "Dragon")


class MentionDetectionTests(unittest.TestCase):
    def test_detect_fuviclan_mention_any_spacing_and_case(self):
        self.assertEqual(tagger.detect_fuviclan_mention("Song (Extended Mix BY FUVI CLAN).mp3"), "BY FUVI CLAN")
        self.assertIsNone(tagger.detect_fuviclan_mention("Song (Extended Mix).mp3"))

    def test_detect_parenthetical_mentions_skips_remix_and_edit(self):
        mentions = tagger.detect_parenthetical_mentions("Song (Radio Edit) (AMAPIANO) (DJ Snake Remix)")
        self.assertEqual(mentions, ["(AMAPIANO)"])


class EffectiveCoverBytesTests(unittest.TestCase):
    def test_no_online_match_keeps_existing_cover(self):
        # e.g. an unofficial remix absent from iTunes/SoundCloud: nothing found
        # online, but the file already has a good cover -> must not preview as empty.
        info = {
            "apply_changes": True,
            "found_cover_image": None,
            "current_cover_bytes": b"original-cover-bytes",
            "file": "JUL, Kevz (FR) - J'oublie Tout (KEVZ Remix).mp3",
        }
        self.assertEqual(tagger.effective_cover_bytes(info), b"original-cover-bytes")

    def test_online_match_found_uses_new_cover(self):
        info = {
            "apply_changes": True,
            "found_cover_image": b"new-cover-bytes",
            "current_cover_bytes": b"original-cover-bytes",
            "file": "Some Artist - Some Title.mp3",
        }
        self.assertEqual(tagger.effective_cover_bytes(info), b"new-cover-bytes")

    def test_fuviclan_mention_with_no_match_previews_as_removed(self):
        info = {
            "apply_changes": True,
            "found_cover_image": None,
            "current_cover_bytes": b"original-cover-bytes",
            "file": "Song (Extended Mix By Fuvi Clan).mp3",
        }
        self.assertIsNone(tagger.effective_cover_bytes(info))

    def test_apply_changes_unchecked_keeps_current_cover(self):
        info = {
            "apply_changes": False,
            "found_cover_image": b"new-cover-bytes",
            "current_cover_bytes": b"original-cover-bytes",
            "file": "Song.mp3",
        }
        self.assertEqual(tagger.effective_cover_bytes(info), b"original-cover-bytes")


class ListAudioFilesTests(unittest.TestCase):
    def setUp(self):
        self._original_music_folder = tagger.MUSIC_FOLDER
        self._original_auto_convert = tagger.AUTO_CONVERT_MP3
        self._tmp_dir = tempfile.TemporaryDirectory()
        tagger.MUSIC_FOLDER = self._tmp_dir.name
        for name in ("Song.mp3", "Track.wav", "Other.flac"):
            with open(os.path.join(self._tmp_dir.name, name), "w") as f:
                f.write("x")

    def tearDown(self):
        tagger.MUSIC_FOLDER = self._original_music_folder
        tagger.AUTO_CONVERT_MP3 = self._original_auto_convert
        self._tmp_dir.cleanup()

    def test_includes_wav_when_auto_convert_enabled(self):
        tagger.AUTO_CONVERT_MP3 = True
        self.assertEqual(set(tagger.list_audio_files()), {"Song.mp3", "Track.wav", "Other.flac"})

    def test_excludes_wav_when_auto_convert_disabled(self):
        tagger.AUTO_CONVERT_MP3 = False
        self.assertEqual(set(tagger.list_audio_files()), {"Song.mp3", "Other.flac"})


class HistoryLogTests(unittest.TestCase):
    def setUp(self):
        self._original_history_file = tagger.HISTORY_FILE
        self._tmp_dir = tempfile.TemporaryDirectory()
        tagger.HISTORY_FILE = os.path.join(self._tmp_dir.name, "history.jsonl")

    def tearDown(self):
        tagger.HISTORY_FILE = self._original_history_file
        self._tmp_dir.cleanup()

    def test_appends_one_json_line_per_entry(self):
        tagger.log_history_entry(
            old_file="Old Artist - Old Title.wav", new_file="New Artist - New Title.mp3",
            old_artist="Old Artist", old_title="Old Title",
            new_artist="New Artist", new_title="New Title",
            cover_updated=True, converted=True,
        )
        tagger.log_history_entry(
            old_file="Another.wav", new_file="Another.mp3",
            old_artist=None, old_title=None,
            new_artist="Someone", new_title="Something",
            cover_updated=False, converted=True,
        )

        with open(tagger.HISTORY_FILE, encoding="utf-8") as f:
            entries = [json.loads(line) for line in f]

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["old_file"], "Old Artist - Old Title.wav")
        self.assertEqual(entries[0]["new_file"], "New Artist - New Title.mp3")
        self.assertTrue(entries[0]["converted"])
        self.assertIsNone(entries[1]["old_artist"])
        self.assertIn("timestamp", entries[0])

    def test_load_history_entries_missing_file_returns_empty_list(self):
        self.assertEqual(tagger.load_history_entries(), [])

    def test_load_history_entries_reads_back_in_order(self):
        tagger.log_history_entry(
            old_file="A.wav", new_file="A.mp3", old_artist="A", old_title="A",
            new_artist="A", new_title="A", cover_updated=False, converted=True,
        )
        tagger.log_history_entry(
            old_file="B.wav", new_file="B.mp3", old_artist="B", old_title="B",
            new_artist="B", new_title="B", cover_updated=True, converted=False,
        )
        entries = tagger.load_history_entries()
        self.assertEqual([e["old_file"] for e in entries], ["A.wav", "B.wav"])

    def test_load_history_entries_skips_malformed_line(self):
        with open(tagger.HISTORY_FILE, "w", encoding="utf-8") as f:
            f.write('{"old_file": "Good.wav"}\n')
            f.write("not valid json\n")
            f.write('{"old_file": "AlsoGood.wav"}\n')
        entries = tagger.load_history_entries()
        self.assertEqual([e["old_file"] for e in entries], ["Good.wav", "AlsoGood.wav"])


class RestoreHistoryEntryTests(unittest.TestCase):
    """Uses a real temp copy of fart.wav - restore_history_entry does real
    file I/O (tag writing, renaming), not worth mocking out."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.file_path = os.path.join(self._tmp_dir.name, "Current Artist - Current Title.wav")
        shutil.copy(os.path.join(project_root, "fart.wav"), self.file_path)
        tagger.write_tags(
            self.file_path, "Current Artist", "Current Title", None, False,
            update_title=True, update_artist=True, update_cover=False,
        )

    def tearDown(self):
        self._tmp_dir.cleanup()

    def test_restores_tags_and_removes_cover_when_none_was_logged(self):
        entry = {
            "folder": self._tmp_dir.name,
            "new_file": "Current Artist - Current Title.wav",
            "old_artist": "Old Artist", "old_title": "Old Title",
            "old_cover_b64": None,
        }
        new_relative = tagger.restore_history_entry(entry, log=lambda *_: None)
        restored_path = os.path.join(self._tmp_dir.name, new_relative)

        self.assertTrue(os.path.exists(restored_path))
        _, artist, title, cover = tagger.read_current_info(restored_path)
        self.assertEqual(artist, "Old Artist")
        self.assertEqual(title, "Old Title")
        self.assertIsNone(cover)

    def test_raises_filenotfound_when_file_missing(self):
        entry = {
            "folder": self._tmp_dir.name, "new_file": "Does Not Exist.mp3",
            "old_artist": "X", "old_title": "Y", "old_cover_b64": None,
        }
        with self.assertRaises(FileNotFoundError):
            tagger.restore_history_entry(entry, log=lambda *_: None)

    def test_raises_valueerror_when_entry_has_no_folder(self):
        entry = {"folder": None, "new_file": "x.mp3", "old_artist": "X", "old_title": "Y"}
        with self.assertRaises(ValueError):
            tagger.restore_history_entry(entry, log=lambda *_: None)


class SettingsPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._original_settings_file = tagger.SETTINGS_FILE
        self._tmp_dir = tempfile.TemporaryDirectory()
        tagger.SETTINGS_FILE = os.path.join(self._tmp_dir.name, "settings.json")

    def tearDown(self):
        tagger.SETTINGS_FILE = self._original_settings_file
        self._tmp_dir.cleanup()

    def test_load_settings_missing_file_returns_empty_dict(self):
        self.assertEqual(tagger.load_settings(), {})

    def test_save_and_load_roundtrip(self):
        tagger.save_setting("theme", "dark")
        self.assertEqual(tagger.load_settings(), {"theme": "dark"})

    def test_save_setting_preserves_other_keys(self):
        tagger.save_setting("theme", "dark")
        tagger.save_setting("other_key", "value")
        self.assertEqual(tagger.load_settings(), {"theme": "dark", "other_key": "value"})


class VersionParsingTests(unittest.TestCase):
    def test_parse_version_simple(self):
        self.assertEqual(tagger.parse_version("v0.2"), (0, 2))
        self.assertEqual(tagger.parse_version("1.2.3"), (1, 2, 3))

    def test_parse_version_ordering(self):
        self.assertTrue(tagger.parse_version("0.10") > tagger.parse_version("0.2"))
        self.assertTrue(tagger.parse_version("v0.2") == tagger.parse_version("0.2"))


class SafePrintTests(unittest.TestCase):
    def test_safe_print_does_not_raise_on_emoji(self):
        # A Windows console using a legacy codepage (cp1252 etc.) can't
        # encode emoji/astral characters - a SoundCloud username or track
        # title containing one used to crash the whole search via a bare
        # print(). safe_print() must never raise regardless of the actual
        # console encoding in whatever environment runs this test.
        try:
            tagger.safe_print("Uploader: \U0001f31e Sun Guy")
        except UnicodeEncodeError:
            self.fail("safe_print() should never raise UnicodeEncodeError")


if __name__ == "__main__":
    unittest.main()
