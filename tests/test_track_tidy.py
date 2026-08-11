"""
Unit tests for the pure parsing/matching logic in track_tidy.py (no network,
no file I/O, no GUI). Run with: python -m unittest discover -s tests
"""

import os
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
