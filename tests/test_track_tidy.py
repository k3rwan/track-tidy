"""
Unit tests for the pure parsing/matching logic in track_tidy.py, plus the
history log (isolated to a temp file - no network, no GUI).
Run with: python -m unittest discover -s tests
"""

import hashlib
import os
import sys
import json
import shutil
import time
import threading
import unittest
import tempfile
import keyring
import keyring.backend

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

    def test_en_dash_and_em_dash_recognized_as_separator(self):
        # Some sources (e.g. Vinyl On Demand releases) use "–"/"—"
        # instead of a plain hyphen - previously fell through to (None, None)
        # since every split pattern only matched a literal ASCII "-".
        self.assertEqual(
            tagger.parse_filename("UNKLE – Only You (&ME Remix).mp3"),
            ("UNKLE", "Only You (&ME Remix)"),
        )
        self.assertEqual(
            tagger.parse_filename("Artist — Title (Remix).mp3"),
            ("Artist", "Title (Remix)"),
        )

    def test_entirely_lowercase_gets_titlecased(self):
        artist, title = tagger.parse_filename("daft_punk - one_more_time.mp3")
        self.assertEqual((artist, title), ("Daft Punk", "One More Time"))

    def test_mixed_case_left_alone(self):
        # Has an uppercase letter already -> not touched, to protect stylized names.
        artist, title = tagger.parse_filename("SCH - Otto.mp3")
        self.assertEqual((artist, title), ("SCH", "Otto"))

    def test_bare_feature_credit_moves_to_artist(self):
        self.assertEqual(
            tagger.parse_filename("Chris Brown - Gimme That Remix ft. Lil' Wayne.wav"),
            ("Chris Brown ft. Lil' Wayne", "Gimme That Remix"),
        )

    def test_bare_feature_credit_with_trailing_parenthesized_suffix(self):
        # Reported bug: the trailing "(Royale BR Bootleg)" (already
        # parenthesized) was misread as the ARTIST by the "Title - Mix -
        # Artist" special case (triggered by "Remix" appearing in what's
        # actually the title, not a mix descriptor), and separately risked
        # being double-wrapped in parens by reformat_trailing_dash_mix.
        self.assertEqual(
            tagger.parse_filename("Chris Brown - Gimme That Remix ft. Lil' Wayne - (Royale BR Bootleg).wav"),
            ("Chris Brown ft. Lil' Wayne", "Gimme That Remix (Royale BR Bootleg)"),
        )

    def test_bare_feature_credit_before_a_separate_mix_suffix(self):
        self.assertEqual(
            tagger.parse_filename("DJ Snake - Turn Down for What ft. Lil Jon (Extended Mix).mp3"),
            ("DJ Snake ft. Lil Jon", "Turn Down for What (Extended Mix)"),
        )

    def test_multiple_bare_featured_artists_kept_together(self):
        self.assertEqual(
            tagger.parse_filename("Major Lazer - Lean On ft. MO & DJ Snake.mp3"),
            ("Major Lazer ft. MO & DJ Snake", "Lean On"),
        )

    def test_already_parenthesized_feature_credit_left_in_title(self):
        # Distinct from the bare "ft. X" case above - a feat. credit that's
        # already its own "(feat. X)" group is left as part of the title,
        # matching strip_feature_suffix()/extract_feature_names()'s existing
        # (separate) handling of that form for cover-search matching.
        self.assertEqual(
            tagger.parse_filename("Artist - Title (feat. Someone).mp3"),
            ("Artist", "Title (feat. Someone)"),
        )

    def test_dash_mix_artist_case_still_works_with_unparenthesized_artist(self):
        # Fix 1's guard (bail out when the last dash-part is parenthesized)
        # must not break the legitimate "Title - Mix - Artist" case this
        # was designed for.
        self.assertEqual(
            tagger.parse_filename("My City's On Fire - Notre Dame Remix - Jimi Jules.mp3"),
            ("Jimi Jules", "My City's On Fire (Notre Dame Remix)"),
        )

    def test_downloader_site_prefix_is_stripped_title_then_artist(self):
        # Reported: some track-downloader tools stamp their own domain as
        # a prefix and put the title BEFORE the artist (the reverse of the
        # usual "Artist - Title") - "SpotiDownloader.com - Imagination -
        # Samm" means artist "Samm", title "Imagination", not artist
        # "SpotiDownloader.com".
        self.assertEqual(
            tagger.parse_filename("SpotiDownloader.com - Imagination - Samm.mp3"),
            ("Samm", "Imagination"),
        )

    def test_downloader_site_prefix_generalizes_to_other_domains(self):
        self.assertEqual(
            tagger.parse_filename("YTMP3.cc - Some Song - Some Artist.mp3"),
            ("Some Artist", "Some Song"),
        )

    def test_three_dash_parts_without_a_domain_looking_first_part_falls_through(self):
        # Must not misfire just because there happen to be 3 dash-separated
        # parts - only a genuinely domain-shaped first part counts.
        self.assertEqual(
            tagger.parse_filename("Artist Name - Song - Extra Info Not A Site.mp3"),
            ("Artist Name", "Song - Extra Info Not A Site"),
        )


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

    def test_strip_generic_mix_suffix_removes_dash_form(self):
        self.assertEqual(tagger.strip_generic_mix_suffix("Dialogo - Radio Edit"), "Dialogo")
        self.assertEqual(tagger.strip_generic_mix_suffix("Astronomia - Extended Mix"), "Astronomia")

    def test_strip_generic_mix_suffix_keeps_named_dash_remix(self):
        self.assertEqual(tagger.strip_generic_mix_suffix("Astronomia - DJ Snake Remix"), "Astronomia - DJ Snake Remix")

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


class ComputeSearchTitlesTests(unittest.TestCase):
    """Reported bug: 'Move (Original Mix)' matched a SoundCloud upload of a
    COMPLETELY different song, 'Rinketin (Original Mix)', by the same
    artist duo - both title_words_overlap() checks (search_cover_soundcloud)
    treated the generic "original"/"mix" words as a meaningful match.
    remix_qualified_title must drop a purely generic qualifier entirely
    (falling back to the plain title) - only a genuinely NAMED one
    (remixer/bootleg credit) is worth keeping attached for the search."""

    def test_generic_qualifier_is_dropped(self):
        self.assertEqual(
            tagger.compute_search_titles("Move (Original Mix)"),
            ("Move", "Move"),
        )

    def test_named_qualifier_is_kept(self):
        self.assertEqual(
            tagger.compute_search_titles("Gimme That Remix (Royale BR Bootleg)"),
            ("Gimme That Remix", "Gimme That Remix (Royale BR Bootleg)"),
        )

    def test_no_parenthetical_returns_same_value_twice(self):
        self.assertEqual(tagger.compute_search_titles("Astronomia"), ("Astronomia", "Astronomia"))

    def test_generic_qualifier_no_longer_overlaps_an_unrelated_title(self):
        search_title, remix_qualified_title = tagger.compute_search_titles("Move (Original Mix)")
        self.assertFalse(tagger.title_words_overlap(remix_qualified_title, "Rinketin (Original Mix)"))
        self.assertEqual(search_title, remix_qualified_title)


class TitleHasNamedQualifierTests(unittest.TestCase):
    def test_named_bootleg_credit_is_named(self):
        self.assertTrue(tagger.title_has_named_qualifier("Gimme That Remix (Royale BR Bootleg)"))

    def test_named_remixer_credit_is_named(self):
        self.assertTrue(tagger.title_has_named_qualifier("Astronomia (DJ Snake Remix)"))

    def test_bare_remix_with_no_name_is_not_named(self):
        self.assertFalse(tagger.title_has_named_qualifier("Title (Remix)"))

    def test_generic_mix_label_is_not_named(self):
        self.assertFalse(tagger.title_has_named_qualifier("Astronomia (Extended Mix)"))

    def test_no_parenthetical_is_not_named(self):
        self.assertFalse(tagger.title_has_named_qualifier("Astronomia"))

    def test_unrelated_parenthetical_is_not_named(self):
        self.assertFalse(tagger.title_has_named_qualifier("Title (feat. Someone)"))


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

    def test_artist_names_match_tolerates_missing_spaces(self):
        # e.g. a SoundCloud username/handle that drops spaces entirely
        self.assertTrue(tagger.artist_names_match("Boltron, Spicy Market", "SpicyMarket"))
        self.assertTrue(tagger.artist_names_match("Spicy Market", "SpicyMarket"))

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


class ITunesRetryTests(unittest.TestCase):
    """A transient HTTP 403 from iTunes' search endpoint (observed in
    practice - looks like a short-lived rate-limit/bot-detection block, not
    a real refusal) should be retried, not treated as a final failure."""

    def setUp(self):
        self.original_get = tagger.requests.get
        self.original_sleep = tagger.time.sleep
        tagger.time.sleep = lambda seconds: None
        tagger._itunes_rate_limited_until = 0

    def tearDown(self):
        tagger.requests.get = self.original_get
        tagger.time.sleep = self.original_sleep
        tagger._itunes_rate_limited_until = 0

    class FakeResponse:
        def __init__(self, status_code, payload=None, content=b""):
            self.status_code = status_code
            self._payload = payload or {}
            self.content = content
            self.text = ""

        def json(self):
            return self._payload

    def test_retries_after_transient_403(self):
        calls = {"search": 0}

        def fake_get(url, params=None, timeout=None):
            if "itunes.apple.com" in url:
                calls["search"] += 1
                if calls["search"] == 1:
                    return self.FakeResponse(403)
                return self.FakeResponse(200, {"results": [
                    {
                        "artistName": "PEATY, Jawora", "trackName": "One Time",
                        "artworkUrl100": "https://example.com/cover100x100.jpg",
                    },
                ]})
            return self.FakeResponse(200, content=b"fake-image-bytes")

        tagger.requests.get = fake_get
        result = tagger.search_cover_itunes("PEATY, Jawora", "One Time")

        self.assertEqual(calls["search"], 2)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], b"fake-image-bytes")

    def test_gives_up_after_persistent_403(self):
        calls = {"search": 0}

        def fake_get(url, params=None, timeout=None):
            calls["search"] += 1
            return self.FakeResponse(403)

        tagger.requests.get = fake_get
        result = tagger.search_cover_itunes("PEATY, Jawora", "One Time")

        self.assertIsNone(result)
        self.assertEqual(calls["search"], 3)  # initial attempt + 2 retries (max_retries=2)

    def test_persistent_429_trips_the_shared_cooldown(self):
        # A real large-batch scan hit a genuine, sustained HTTP 429 (not
        # just transient 403s) - every further iTunes call across the rest
        # of that scan (other concurrent workers, later sequential
        # AcoustID-triggered re-searches) should back off immediately
        # instead of each independently retrying into the same wall.
        calls = {"search": 0}

        def fake_get(url, params=None, timeout=None):
            calls["search"] += 1
            return self.FakeResponse(429)

        tagger.requests.get = fake_get
        result = tagger.search_cover_itunes("PEATY, Jawora", "One Time")

        self.assertIsNone(result)
        self.assertEqual(calls["search"], 3)  # this call still does its own retries
        self.assertGreater(tagger._itunes_rate_limited_until, time.time())

        # A second call, still within the cooldown, is skipped outright -
        # no further HTTP request at all.
        result2 = tagger.search_cover_itunes("Someone Else", "Another Title")
        self.assertIsNone(result2)
        self.assertEqual(calls["search"], 3)  # unchanged - no new request made

    def test_cooldown_expiring_allows_requests_again(self):
        tagger._itunes_rate_limited_until = time.time() - 1  # already expired

        def fake_get(url, params=None, timeout=None):
            return self.FakeResponse(200, {"results": [
                {
                    "artistName": "PEATY, Jawora", "trackName": "One Time",
                    "artworkUrl100": "https://example.com/cover100x100.jpg",
                },
            ]}) if "itunes.apple.com" in url else self.FakeResponse(200, content=b"fake-image-bytes")

        tagger.requests.get = fake_get
        result = tagger.search_cover_itunes("PEATY, Jawora", "One Time")

        self.assertIsNotNone(result)


class SearchCoverSoundcloudTests(unittest.TestCase):
    """search_cover_soundcloud() - no real network, requests.get is faked."""

    def setUp(self):
        self.original_get = tagger.requests.get

    def tearDown(self):
        tagger.requests.get = self.original_get

    class FakeResponse:
        def __init__(self, status_code, payload=None, content=b""):
            self.status_code = status_code
            self._payload = payload if payload is not None else []
            self.content = content
            self.text = ""

        def json(self):
            return self._payload

    def _track(self, title, uploader, has_artwork=True):
        return {
            "title": title,
            "user": {"username": uploader},
            "artwork_url": "https://example.com/cover-large.jpg" if has_artwork else None,
        }

    def test_checks_candidates_beyond_the_first(self):
        # Reported bug: the exact "Royale BR" bootleg ranked #2 in real
        # SoundCloud search results, behind an unrelated plain upload of
        # the same base song - only checking the top result missed it
        # entirely.
        tracks = [
            self._track("Chris Brown - Gimme That (remix) ft. Lil Wayne", "Polly_Pockety"),
            self._track("Chris Brown - Gimme That Remix Ft. Lil Wayne - (Royale BR Bootleg)", "Royale BR"),
        ]

        def fake_get(url, headers=None, params=None, timeout=None):
            if "api.soundcloud.com" in url:
                return self.FakeResponse(200, tracks)
            return self.FakeResponse(200, content=b"fake-image-bytes")

        tagger.requests.get = fake_get
        result = tagger.search_cover_soundcloud(
            "Chris Brown ft. Lil' Wayne", "Gimme That Remix (Royale BR Bootleg)", "token",
        )

        self.assertIsNotNone(result)
        self.assertEqual(result[1], "Royale BR")

    def test_rejects_a_candidate_missing_the_named_qualifier(self):
        # The #1 (only) candidate shares the base title/artist but doesn't
        # credit the specific named bootleg anywhere - must be rejected,
        # not accepted as "close enough".
        tracks = [self._track("Chris Brown - Gimme That (remix) ft. Lil Wayne", "Polly_Pockety")]

        def fake_get(url, headers=None, params=None, timeout=None):
            return self.FakeResponse(200, tracks)

        tagger.requests.get = fake_get
        result = tagger.search_cover_soundcloud(
            "Chris Brown ft. Lil' Wayne", "Gimme That Remix (Royale BR Bootleg)", "token",
        )

        self.assertIsNone(result)

    def test_no_named_qualifier_accepts_the_first_good_match(self):
        tracks = [self._track("Some Song", "Some Artist")]

        def fake_get(url, headers=None, params=None, timeout=None):
            if "api.soundcloud.com" in url:
                return self.FakeResponse(200, tracks)
            return self.FakeResponse(200, content=b"fake-image-bytes")

        tagger.requests.get = fake_get
        result = tagger.search_cover_soundcloud("Some Artist", "Some Song", "token")

        self.assertIsNotNone(result)

    def test_no_token_returns_none_without_a_request(self):
        self.assertIsNone(tagger.search_cover_soundcloud("Artist", "Title", None))


class IdentifyViaAcoustidTests(unittest.TestCase):
    """identify_via_acoustid() - last-resort audio-content identification
    for badly-named files. acoustid.match() itself is faked - no real
    fpcalc/network involved, same reasoning as the other cover sources."""

    def setUp(self):
        self._original_api_key = tagger.ACOUSTID_API_KEY
        self._original_match = tagger.acoustid.match
        tagger.ACOUSTID_API_KEY = "test-api-key"

    def tearDown(self):
        tagger.ACOUSTID_API_KEY = self._original_api_key
        tagger.acoustid.match = self._original_match

    def test_no_api_key_returns_none_without_calling_match(self):
        tagger.ACOUSTID_API_KEY = None
        calls = []
        tagger.acoustid.match = lambda *a, **k: calls.append(1) or []
        self.assertIsNone(tagger.identify_via_acoustid("song.mp3"))
        self.assertEqual(calls, [])

    def test_returns_the_best_confident_match(self):
        tagger.acoustid.match = lambda apikey, path: iter([
            (0.95, "rec-1", "Real Title", "Real Artist"),
            (0.6, "rec-2", "Other Title", "Other Artist"),
        ])
        self.assertEqual(tagger.identify_via_acoustid("song.mp3"), ("Real Artist", "Real Title"))

    def test_rejects_matches_below_the_score_threshold(self):
        tagger.acoustid.match = lambda apikey, path: iter([
            (0.2, "rec-1", "Unsure Title", "Unsure Artist"),
        ])
        self.assertIsNone(tagger.identify_via_acoustid("song.mp3"))

    def test_no_results_returns_none(self):
        tagger.acoustid.match = lambda apikey, path: iter([])
        self.assertIsNone(tagger.identify_via_acoustid("song.mp3"))

    def test_no_backend_error_returns_none_without_raising(self):
        def raise_no_backend(apikey, path):
            raise tagger.acoustid.NoBackendError("fpcalc not found")
        tagger.acoustid.match = raise_no_backend
        self.assertIsNone(tagger.identify_via_acoustid("song.mp3"))

    def test_web_service_error_retries_then_gives_up(self):
        # Connection resets/timeouts are usually transient (flaky network,
        # antivirus HTTP inspection) - retried a couple of times before
        # giving up, unlike the deterministic fingerprinting errors above.
        calls = []
        def raise_web_error(apikey, path):
            calls.append(1)
            raise tagger.acoustid.WebServiceError("network down")
        tagger.acoustid.match = raise_web_error
        original_sleep = tagger.time.sleep
        tagger.time.sleep = lambda seconds: None
        try:
            self.assertIsNone(tagger.identify_via_acoustid("song.mp3"))
        finally:
            tagger.time.sleep = original_sleep
        self.assertEqual(len(calls), 3)

    def test_web_service_error_retries_and_recovers(self):
        calls = []
        def flaky_then_ok(apikey, path):
            calls.append(1)
            if len(calls) < 2:
                raise tagger.acoustid.WebServiceError("connection reset")
            return iter([(0.95, "rec-1", "Real Title", "Real Artist")])
        tagger.acoustid.match = flaky_then_ok
        original_sleep = tagger.time.sleep
        tagger.time.sleep = lambda seconds: None
        try:
            result = tagger.identify_via_acoustid("song.mp3")
        finally:
            tagger.time.sleep = original_sleep
        self.assertEqual(result, ("Real Artist", "Real Title"))
        self.assertEqual(len(calls), 2)


class TryAcoustidCorrectionTests(unittest.TestCase):
    """_try_acoustid_correction() - the scan-pipeline glue that applies a
    confident AcoustID match to a prepared scan's detected_artist/title."""

    def setUp(self):
        self._original_use_acoustid = tagger.USE_ACOUSTID_FALLBACK
        self._original_music_folder = tagger.MUSIC_FOLDER
        self._original_identify = tagger.identify_via_acoustid
        tagger.USE_ACOUSTID_FALLBACK = True
        tagger.MUSIC_FOLDER = "/music"

    def tearDown(self):
        tagger.USE_ACOUSTID_FALLBACK = self._original_use_acoustid
        tagger.MUSIC_FOLDER = self._original_music_folder
        tagger.identify_via_acoustid = self._original_identify

    def _prepared(self):
        return {
            "file_name": "garbled_track_047.mp3",
            "detected_artist": None,
            "detected_title": None,
            "search_title": None,
            "remix_qualified_title": None,
        }

    def test_disabled_returns_false_and_leaves_prepared_untouched(self):
        tagger.USE_ACOUSTID_FALLBACK = False
        tagger.identify_via_acoustid = lambda path, log=None: ("Should Not", "Be Used")
        prepared = self._prepared()

        self.assertFalse(tagger._try_acoustid_correction(prepared))
        self.assertIsNone(prepared["detected_artist"])

    def test_no_match_returns_false(self):
        tagger.identify_via_acoustid = lambda path, log=None: None
        prepared = self._prepared()

        self.assertFalse(tagger._try_acoustid_correction(prepared))
        self.assertIsNone(prepared["detected_artist"])

    def test_confident_match_updates_prepared_fields(self):
        tagger.identify_via_acoustid = lambda path, log=None: ("Daft Punk", "One More Time")
        prepared = self._prepared()

        self.assertTrue(tagger._try_acoustid_correction(prepared))
        self.assertEqual(prepared["detected_artist"], "Daft Punk")
        self.assertEqual(prepared["detected_title"], "One More Time")
        self.assertEqual(prepared["search_title"], "One More Time")
        self.assertEqual(prepared["remix_qualified_title"], "One More Time")

    def test_confident_match_with_remix_qualifier_computes_search_titles(self):
        tagger.identify_via_acoustid = lambda path, log=None: ("Artist", "Title (Some Remix)")
        prepared = self._prepared()

        self.assertTrue(tagger._try_acoustid_correction(prepared))
        self.assertEqual(prepared["search_title"], "Title")
        self.assertEqual(prepared["remix_qualified_title"], "Title (Some Remix)")


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

    def test_itunes_skips_plain_title_for_a_named_qualifier(self):
        # Reported bug: a bare "(Remix)" tries the plain title first (see
        # test above), but a NAMED one (e.g. "(Royale BR Bootleg)") must
        # NOT - the plain title risks matching a different, unrelated
        # official release (e.g. an official "(Remix)" single) instead of
        # the specific unofficial version actually wanted.
        calls = []

        def fake_itunes(artist, title, log=None, **kwargs):
            calls.append(title)
            # Only the WRONG plain-title query would find anything here -
            # if it's ever sent, the bug has regressed.
            return (b"wrong cover", artist, title) if title == "Title" else None

        tagger.search_cover_itunes = fake_itunes

        match_result, source = tagger._search_one_source(
            "itunes", "Artist", "Title", "Title (Royale BR Bootleg)", None, None, print
        )
        self.assertEqual(calls, ["Title (Royale BR Bootleg)"])
        self.assertIsNone(match_result)
        self.assertIsNone(source)

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


class SearchCoverManualTests(unittest.TestCase):
    """search_cover_manual() - the "fix Artist/Title and search again" flow
    after a scan finds no match - searches with the given artist/title
    directly, stopping at the first enabled source that matches."""

    def setUp(self):
        self._original_itunes = tagger.search_cover_itunes
        self._original_soundcloud = tagger.search_cover_soundcloud
        self._original_use_itunes = tagger.USE_ITUNES
        self._original_use_spotify = tagger.USE_SPOTIFY
        self._original_use_soundcloud = tagger.USE_SOUNDCLOUD
        self._original_soundcloud_rate_limited = tagger.SOUNDCLOUD_RATE_LIMITED
        self._original_soundcloud_unavailable = tagger.SOUNDCLOUD_UNAVAILABLE
        tagger.USE_ITUNES = True
        tagger.USE_SPOTIFY = False
        tagger.USE_SOUNDCLOUD = True
        # search_cover_manual() doesn't reset these itself (only scan_files()
        # does, at the start of each run) - force known-good values so a
        # flag left set by an earlier test (e.g. no SoundCloud credentials
        # configured in this environment) can't silently skip the
        # SoundCloud branch these tests are actually testing.
        tagger.SOUNDCLOUD_RATE_LIMITED = False
        tagger.SOUNDCLOUD_UNAVAILABLE = False

    def tearDown(self):
        tagger.search_cover_itunes = self._original_itunes
        tagger.search_cover_soundcloud = self._original_soundcloud
        tagger.USE_ITUNES = self._original_use_itunes
        tagger.USE_SPOTIFY = self._original_use_spotify
        tagger.USE_SOUNDCLOUD = self._original_use_soundcloud
        tagger.SOUNDCLOUD_RATE_LIMITED = self._original_soundcloud_rate_limited
        tagger.SOUNDCLOUD_UNAVAILABLE = self._original_soundcloud_unavailable

    def test_returns_none_for_empty_artist_or_title(self):
        self.assertEqual(
            tagger.search_cover_manual("", "Title", None), (None, None, None, None),
        )
        self.assertEqual(
            tagger.search_cover_manual("Artist", "", None), (None, None, None, None),
        )

    def test_uses_the_given_artist_title_directly(self):
        calls = []

        def fake_itunes(artist, title, log=None, **kwargs):
            calls.append((artist, title))
            return (b"cover", "Real Artist", "Real Title")

        tagger.search_cover_itunes = fake_itunes

        result = tagger.search_cover_manual("Corrected Artist", "Corrected Title", None)
        self.assertEqual(calls, [("Corrected Artist", "Corrected Title")])
        self.assertEqual(result, (b"cover", "iTunes", "Real Artist", "Real Title"))

    def test_falls_through_to_the_next_enabled_source_on_miss(self):
        tagger.search_cover_itunes = lambda artist, title, log=None, **kwargs: None
        tagger.search_cover_soundcloud = lambda artist, title, token, log=None: (b"cover", artist, title)

        result = tagger.search_cover_manual("Artist", "Title", "sc-token")
        self.assertEqual(result, (b"cover", "SoundCloud", "Artist", "Title"))

    def test_no_match_from_any_source_returns_none_tuple(self):
        tagger.search_cover_itunes = lambda artist, title, log=None, **kwargs: None
        tagger.search_cover_soundcloud = lambda artist, title, token, log=None: None

        result = tagger.search_cover_manual("Artist", "Title", None)
        self.assertEqual(result, (None, None, None, None))


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

    def test_excludes_non_wav_non_mp3_when_auto_convert_disabled(self):
        # WAV can be tagged directly (no conversion needed), so it stays
        # included either way - only FLAC (needs converting to be taggable
        # at all) drops out when auto-convert is off.
        tagger.AUTO_CONVERT_MP3 = False
        self.assertEqual(set(tagger.list_audio_files()), {"Song.mp3", "Track.wav"})


class ScanFilesParallelITunesTests(unittest.TestCase):
    """scan_files() searches iTunes for every file concurrently (bounded by
    ITUNES_SCAN_MAX_WORKERS), while Spotify/SoundCloud stay sequential."""

    def setUp(self):
        self._original_music_folder = tagger.MUSIC_FOLDER
        self._original_use_itunes = tagger.USE_ITUNES
        self._original_use_spotify = tagger.USE_SPOTIFY
        self._original_use_soundcloud = tagger.USE_SOUNDCLOUD
        self._original_itunes_search = tagger.search_cover_itunes
        self._original_soundcloud_search = tagger.search_cover_soundcloud
        self._original_soundcloud_client_id = tagger.SOUNDCLOUD_CLIENT_ID
        self._original_soundcloud_client_secret = tagger.SOUNDCLOUD_CLIENT_SECRET
        self._original_get_soundcloud_token = tagger.get_soundcloud_token

        self._tmp_dir = tempfile.TemporaryDirectory()
        tagger.MUSIC_FOLDER = self._tmp_dir.name
        self.file_names = [f"Artist {i} - Title {i}.mp3" for i in range(6)]
        for name in self.file_names:
            with open(os.path.join(self._tmp_dir.name, name), "w") as f:
                f.write("x")

        tagger.USE_ITUNES = True
        tagger.USE_SPOTIFY = False
        tagger.USE_SOUNDCLOUD = True
        # scan_files() skips SoundCloud entirely (SOUNDCLOUD_UNAVAILABLE) when
        # no Client ID/Secret are configured - fake ones (plus a mocked
        # get_soundcloud_token, so no real auth call happens) here so these
        # tests exercise the actual fallback/parallel logic instead of
        # silently depending on whatever happens to already be saved in
        # this machine's keyring.
        tagger.SOUNDCLOUD_CLIENT_ID = "test-client-id"
        tagger.SOUNDCLOUD_CLIENT_SECRET = "test-client-secret"
        tagger.get_soundcloud_token = lambda log=None, on_rate_limited=None: "fake-token"

    def tearDown(self):
        tagger.MUSIC_FOLDER = self._original_music_folder
        tagger.USE_ITUNES = self._original_use_itunes
        tagger.USE_SPOTIFY = self._original_use_spotify
        tagger.USE_SOUNDCLOUD = self._original_use_soundcloud
        tagger.search_cover_itunes = self._original_itunes_search
        tagger.search_cover_soundcloud = self._original_soundcloud_search
        tagger.SOUNDCLOUD_CLIENT_ID = self._original_soundcloud_client_id
        tagger.SOUNDCLOUD_CLIENT_SECRET = self._original_soundcloud_client_secret
        tagger.get_soundcloud_token = self._original_get_soundcloud_token
        self._tmp_dir.cleanup()

    def test_itunes_searches_run_concurrently(self):
        active = {"count": 0, "max": 0}
        lock = threading.Lock()

        def fake_itunes(artist, title, log=None, **kwargs):
            with lock:
                active["count"] += 1
                active["max"] = max(active["max"], active["count"])
            time.sleep(0.05)  # hold the "slot" briefly so overlap is observable
            with lock:
                active["count"] -= 1
            return (b"cover", artist, title)

        tagger.search_cover_itunes = fake_itunes

        results = tagger.scan_files(self.file_names, log=lambda msg: None)

        self.assertEqual(len(results), len(self.file_names))
        self.assertTrue(all(info["cover_source"] == "iTunes" for info in results))
        self.assertGreater(active["max"], 1, "expected genuine concurrency across files")
        self.assertLessEqual(active["max"], tagger.ITUNES_SCAN_MAX_WORKERS)

    def test_falls_back_to_soundcloud_when_itunes_misses(self):
        tagger.search_cover_itunes = lambda artist, title, log=None, **kwargs: None
        tagger.search_cover_soundcloud = lambda artist, title, token, log=None: (b"cover", artist, title)

        results = tagger.scan_files(self.file_names[:2], log=lambda msg: None)

        self.assertTrue(all(info["cover_source"] == "SoundCloud" for info in results))

    def test_itunes_disabled_skips_the_parallel_phase_entirely(self):
        tagger.USE_ITUNES = False
        calls = {"itunes": 0}

        def fake_itunes(artist, title, log=None, **kwargs):
            calls["itunes"] += 1
            return (b"cover", artist, title)

        tagger.search_cover_itunes = fake_itunes
        tagger.search_cover_soundcloud = lambda artist, title, token, log=None: (b"cover", artist, title)

        results = tagger.scan_files(self.file_names[:2], log=lambda msg: None)

        self.assertEqual(calls["itunes"], 0)
        self.assertTrue(all(info["cover_source"] == "SoundCloud" for info in results))


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

    def test_delete_history_entries_removes_only_matching_timestamps(self):
        tagger.log_history_entry(
            old_file="A.wav", new_file="A.mp3", old_artist="A", old_title="A",
            new_artist="A", new_title="A", cover_updated=False, converted=True,
        )
        tagger.log_history_entry(
            old_file="B.wav", new_file="B.mp3", old_artist="B", old_title="B",
            new_artist="B", new_title="B", cover_updated=True, converted=False,
        )
        entries = tagger.load_history_entries()
        tagger.delete_history_entries([entries[0]])

        remaining = tagger.load_history_entries()
        self.assertEqual([e["old_file"] for e in remaining], ["B.wav"])

    def test_delete_history_entries_missing_file_does_not_raise(self):
        tagger.delete_history_entries([{"timestamp": "2024-01-01T00:00:00"}])  # no file yet


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
        restored_path = tagger.restore_history_entry(entry, log=lambda *_: None)

        self.assertTrue(os.path.isabs(restored_path))
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

    def test_auto_locates_file_moved_to_a_subfolder(self):
        subfolder = os.path.join(self._tmp_dir.name, "moved here")
        os.makedirs(subfolder)
        moved_path = os.path.join(subfolder, "Current Artist - Current Title.wav")
        shutil.move(self.file_path, moved_path)

        entry = {
            "folder": self._tmp_dir.name,
            "new_file": "Current Artist - Current Title.wav",
            "old_artist": "Old Artist", "old_title": "Old Title", "old_cover_b64": None,
        }
        restored_path = tagger.restore_history_entry(entry, log=lambda *_: None)
        self.assertTrue(os.path.exists(restored_path))
        self.assertEqual(os.path.dirname(restored_path), subfolder)

    def test_override_path_restores_at_a_completely_different_location(self):
        elsewhere = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, elsewhere, ignore_errors=True)
        moved_path = os.path.join(elsewhere, "Current Artist - Current Title.wav")
        shutil.move(self.file_path, moved_path)

        entry = {
            "folder": self._tmp_dir.name,  # the ORIGINAL folder - deliberately stale
            "new_file": "Current Artist - Current Title.wav",
            "old_artist": "Old Artist", "old_title": "Old Title", "old_cover_b64": None,
        }
        restored_path = tagger.restore_history_entry(entry, log=lambda *_: None, override_path=moved_path)
        self.assertTrue(os.path.exists(restored_path))
        self.assertEqual(os.path.dirname(restored_path), elsewhere)


def _read_riff_info_chunk(file_path):
    """Parses a WAV's raw RIFF LIST/INFO sub-chunks (INAM/IART/...) by hand -
    mutagen has no support for reading these, only ID3, so this is the only
    way to verify write_tags() actually wrote them."""
    with open(file_path, "rb") as f:
        data = f.read()
    found = {}
    i = 12
    while i < len(data) - 8:
        chunk_id = data[i:i + 4]
        chunk_size = int.from_bytes(data[i + 4:i + 8], "little")
        if chunk_id == b"LIST" and data[i + 8:i + 12] == b"INFO":
            j = i + 12
            end = i + 8 + chunk_size
            while j < end:
                sub_id = data[j:j + 4]
                sub_size = int.from_bytes(data[j + 4:j + 8], "little")
                found[sub_id] = data[j + 8:j + 8 + sub_size].rstrip(b"\x00").decode("utf-8", "replace")
                j += 8 + sub_size + (sub_size % 2)
        i += 8 + chunk_size + (chunk_size % 2)
    return found


@unittest.skipUnless(shutil.which(tagger.find_ffmpeg()) or os.path.exists(tagger.find_ffmpeg()),
                      "ffmpeg not available in this environment")
class WriteTagsWavRiffInfoTests(unittest.TestCase):
    """write_tags() on a .wav also writes the RIFF LIST/INFO chunk
    (INAM/IART) via FFmpeg, alongside the usual ID3 tags - Windows Explorer
    and several DJ tools only read RIFF INFO for WAV metadata, not ID3."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.file_path = os.path.join(self._tmp_dir.name, "song.wav")
        shutil.copy(os.path.join(project_root, "fart.wav"), self.file_path)

    def tearDown(self):
        self._tmp_dir.cleanup()

    def test_writes_both_riff_info_and_id3(self):
        tagger.write_tags(
            self.file_path, "New Artist", "New Title", cover_image=None, force_remove_if_missing=False,
            update_title=True, update_artist=True, update_cover=False, log=lambda *_: None,
        )

        riff_info = _read_riff_info_chunk(self.file_path)
        self.assertEqual(riff_info.get(b"INAM"), "New Title")
        self.assertEqual(riff_info.get(b"IART"), "New Artist")

        _, artist, title, _ = tagger.read_current_info(self.file_path)
        self.assertEqual(artist, "New Artist")
        self.assertEqual(title, "New Title")

    def test_riff_info_write_failure_does_not_block_id3_write(self):
        original_find_ffmpeg = tagger.find_ffmpeg
        tagger.find_ffmpeg = lambda: os.path.join(self._tmp_dir.name, "no-such-ffmpeg.exe")
        try:
            tagger.write_tags(
                self.file_path, "New Artist", "New Title", cover_image=None, force_remove_if_missing=False,
                update_title=True, update_artist=True, update_cover=False, log=lambda *_: None,
            )
        finally:
            tagger.find_ffmpeg = original_find_ffmpeg

        # RIFF INFO wasn't touched (ffmpeg "failed"), but ID3 still wrote fine
        _, artist, title, _ = tagger.read_current_info(self.file_path)
        self.assertEqual(artist, "New Artist")
        self.assertEqual(title, "New Title")


class ProcessFilesTests(unittest.TestCase):
    """Reported bug: a corrupted file ("can't sync to MPEG frame", a real
    mutagen error for invalid MP3 audio data) raised uncaught from inside
    the per-file loop, aborting process_files() entirely - every file
    queued AFTER the bad one silently never got tagged or renamed, with
    no indication why. Each file must be independent: one failure logs an
    error and moves on to the next."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_music_folder = tagger.MUSIC_FOLDER
        tagger.MUSIC_FOLDER = self._tmp_dir.name
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._good_source = os.path.join(project_root, "fart.wav")

    def tearDown(self):
        tagger.MUSIC_FOLDER = self._original_music_folder
        self._tmp_dir.cleanup()

    def _make_plan_entry(self, file_name, artist, title):
        return {
            "file": file_name,
            "artist_override": None,
            "title_override": None,
            "detected_artist": artist,
            "detected_title": title,
            "current_artist": None,
            "current_title": None,
            "current_cover_bytes": None,
            "has_cover": False,
            "apply_changes": True,
            "convert": False,
            "found_cover_image": None,
            "processed": False,
        }

    def test_corrupted_file_does_not_abort_the_rest_of_the_batch(self):
        corrupted_path = os.path.join(self._tmp_dir.name, "corrupted.mp3")
        with open(corrupted_path, "wb") as f:
            f.write(b"ID3" + b"\x00" * 200)  # not valid MP3 audio data

        good_path = os.path.join(self._tmp_dir.name, "good_track.mp3")
        shutil.copy(self._good_source, good_path)

        plan = [
            self._make_plan_entry("corrupted.mp3", "Bad Artist", "Bad Title"),
            self._make_plan_entry("good_track.mp3", "Daft Punk", "One More Time"),
        ]

        results = []
        tagger.process_files(
            plan, log=lambda *_: None,
            on_file_processed=lambda ident, ok, reason=None: results.append((ident, ok, reason)),
        )

        self.assertEqual(len(results), 2)
        corrupted_ident, corrupted_ok, corrupted_reason = results[0]
        self.assertEqual((corrupted_ident, corrupted_ok), ("corrupted.mp3", False))
        self.assertTrue(corrupted_reason)  # some human-readable reason, exact text is mutagen's own
        self.assertEqual(results[1], ("good_track.mp3", True, None))
        self.assertTrue(plan[0]["processed"])
        self.assertTrue(plan[1]["processed"])

        remaining_files = set(os.listdir(self._tmp_dir.name))
        self.assertIn("Daft Punk - One More Time.mp3", remaining_files)
        # The corrupted file is left alone (not silently deleted/renamed).
        self.assertIn("corrupted.mp3", remaining_files)


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


class _FakeKeyringBackend(keyring.backend.KeyringBackend):
    """In-memory stand-in for the real OS credential store (Windows
    Credential Manager / macOS Keychain / ...), so tests never touch
    Kevin's actual saved credentials."""
    priority = 1

    def __init__(self):
        self._store = {}

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def get_password(self, service, username):
        return self._store.get((service, username))

    def delete_password(self, service, username):
        self._store.pop((service, username), None)


class CredentialEncryptionTests(unittest.TestCase):
    def setUp(self):
        self._original_keyring = keyring.get_keyring()
        keyring.set_keyring(_FakeKeyringBackend())
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._key = "test_credential_key"
        self._legacy_path = os.path.join(self._tmp_dir.name, "legacy.txt")
        self._original_legacy_files = tagger._LEGACY_CREDENTIAL_FILES
        tagger._LEGACY_CREDENTIAL_FILES = {self._key: self._legacy_path}

    def tearDown(self):
        keyring.set_keyring(self._original_keyring)
        tagger._LEGACY_CREDENTIAL_FILES = self._original_legacy_files
        self._tmp_dir.cleanup()

    def test_write_then_read_roundtrip(self):
        tagger.write_credential(self._key, "super-secret-value")
        self.assertEqual(tagger.read_credential(self._key), "super-secret-value")

    def test_read_credential_missing_returns_none(self):
        self.assertIsNone(tagger.read_credential(self._key))

    def test_read_credential_migrates_legacy_plaintext_file(self):
        with open(self._legacy_path, "w", encoding="utf-8") as f:
            f.write("old-plaintext-secret")

        self.assertEqual(tagger.read_credential(self._key), "old-plaintext-secret")
        self.assertFalse(os.path.exists(self._legacy_path), "legacy file should be removed after migrating")
        # Now saved for real - reading again works with no legacy file left.
        self.assertEqual(tagger.read_credential(self._key), "old-plaintext-secret")


class NewInstallNotificationTests(unittest.TestCase):
    def setUp(self):
        self.original_post = tagger.requests.post

    def tearDown(self):
        tagger.requests.post = self.original_post

    def test_sends_username_and_returns_true_on_success(self):
        captured = {}

        def fake_post(url, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json

            class FakeResponse:
                status_code = 204
            return FakeResponse()

        tagger.requests.post = fake_post
        result = tagger.send_new_install_notification(reporter_name="someuser")

        self.assertTrue(result)
        self.assertEqual(captured["url"], tagger.DISCORD_REPORT_WEBHOOK_URL)
        fields = captured["json"]["embeds"][0]["fields"]
        self.assertEqual(fields[0], {"name": "User", "value": "someuser", "inline": True})

    def test_excluded_user_is_not_notified(self):
        # The developer's own Windows account - see
        # DISCORD_NOTIFICATION_EXCLUDED_USERS, checked case-insensitively.
        calls = []
        tagger.requests.post = lambda *a, **k: calls.append(1)

        self.assertFalse(tagger.send_new_install_notification(reporter_name="Kevin"))
        self.assertEqual(calls, [])


class ScanCompleteNotificationTests(unittest.TestCase):
    def setUp(self):
        self.original_post = tagger.requests.post

    def tearDown(self):
        tagger.requests.post = self.original_post

    def test_sends_counts_and_returns_true_on_success(self):
        captured = {}

        def fake_post(url, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json

            class FakeResponse:
                status_code = 204
            return FakeResponse()

        tagger.requests.post = fake_post
        result = tagger.send_scan_complete_notification(
            reporter_name="someuser", number_new=3, number_removed=1, total=10,
        )

        self.assertTrue(result)
        self.assertEqual(captured["url"], tagger.DISCORD_REPORT_WEBHOOK_URL)
        fields = captured["json"]["embeds"][0]["fields"]
        self.assertEqual(fields[0], {"name": "User", "value": "someuser", "inline": True})
        self.assertEqual(fields[1], {"name": "New files", "value": "3", "inline": True})
        self.assertEqual(fields[2], {"name": "Removed files", "value": "1", "inline": True})
        self.assertEqual(fields[3], {"name": "Total files", "value": "10", "inline": True})

    def test_excluded_user_is_not_notified(self):
        calls = []
        tagger.requests.post = lambda *a, **k: calls.append(1)

        self.assertFalse(tagger.send_scan_complete_notification(reporter_name="kevin", number_new=1))
        self.assertEqual(calls, [])

    def test_returns_false_on_network_failure(self):
        def fake_post(url, json=None, timeout=None):
            raise ConnectionError("no network")

        tagger.requests.post = fake_post
        self.assertFalse(tagger.send_new_install_notification(reporter_name="kevin"))


class UpdateChecksumTests(unittest.TestCase):
    """check_for_update looks for a "<installer>.sha256" release asset, and
    download_installer verifies the download against it before letting the
    caller treat it as trustworthy."""

    class FakeResponse:
        def __init__(self, status_code, payload=None, text="", content=b""):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = text
            self._content = content
            self.headers = {"Content-Length": str(len(content))}

        def json(self):
            return self._payload

        def iter_content(self, chunk_size=262144):
            yield self._content

    def setUp(self):
        self.original_get = tagger.requests.get
        self._tmp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        tagger.requests.get = self.original_get
        self._tmp_dir.cleanup()

    def test_check_for_update_returns_sha256_when_asset_present(self):
        # check_for_update() looks for a .dmg asset on macOS, .exe elsewhere
        # (see its installer_extension line) - the fake release must offer
        # whichever one matches the platform actually running this test.
        installer_name = "Track-Tidy-Setup-v99.0.dmg" if sys.platform == "darwin" else "Track-Tidy-Setup-v99.0.exe"
        release_data = {
            "tag_name": "v99.0",
            "html_url": "https://example.com/release",
            "assets": [
                {"name": installer_name, "browser_download_url": "https://example.com/installer"},
                {"name": installer_name + ".sha256", "browser_download_url": "https://example.com/installer.sha256"},
            ],
        }
        expected_hash = "a" * 64

        def fake_get(url, timeout=None):
            if url.endswith(".sha256"):
                return self.FakeResponse(200, text=f"{expected_hash}  {installer_name}")
            return self.FakeResponse(200, payload=release_data)

        tagger.requests.get = fake_get
        is_newer, latest_tag, release_url, installer_url, expected_sha256 = tagger.check_for_update()

        self.assertTrue(is_newer)
        self.assertEqual(expected_sha256, expected_hash)

    def test_check_for_update_sha256_none_when_no_checksum_asset(self):
        release_data = {
            "tag_name": "v99.0",
            "html_url": "https://example.com/release",
            "assets": [
                {"name": "Track-Tidy-Setup-v99.0.exe", "browser_download_url": "https://example.com/installer.exe"},
            ],
        }
        tagger.requests.get = lambda url, timeout=None: self.FakeResponse(200, payload=release_data)
        result = tagger.check_for_update()

        self.assertIsNone(result[4])

    def test_compute_sha256_matches_known_hash(self):
        path = os.path.join(self._tmp_dir.name, "file.bin")
        with open(path, "wb") as f:
            f.write(b"hello world")
        self.assertEqual(tagger.compute_sha256(path), hashlib.sha256(b"hello world").hexdigest())

    def test_download_installer_accepts_matching_checksum(self):
        content = b"fake-installer-bytes"
        expected_hash = hashlib.sha256(content).hexdigest()
        tagger.requests.get = lambda url, stream=None, timeout=None: self.FakeResponse(200, content=content)
        dest_path = os.path.join(self._tmp_dir.name, "installer.exe")

        success = tagger.download_installer("https://example.com/installer.exe", dest_path, expected_sha256=expected_hash)

        self.assertTrue(success)
        self.assertTrue(os.path.exists(dest_path))

    def test_download_installer_rejects_mismatched_checksum(self):
        content = b"fake-installer-bytes"
        tagger.requests.get = lambda url, stream=None, timeout=None: self.FakeResponse(200, content=content)
        dest_path = os.path.join(self._tmp_dir.name, "installer.exe")

        success = tagger.download_installer(
            "https://example.com/installer.exe", dest_path, expected_sha256="0" * 64, log=lambda text: None,
        )

        self.assertFalse(success)
        self.assertFalse(os.path.exists(dest_path))


class VersionParsingTests(unittest.TestCase):
    def test_parse_version_simple(self):
        self.assertEqual(tagger.parse_version("v0.2"), (0, 2))
        self.assertEqual(tagger.parse_version("1.2.3"), (1, 2, 3))

    def test_parse_version_ordering(self):
        self.assertTrue(tagger.parse_version("0.10") > tagger.parse_version("0.2"))
        self.assertTrue(tagger.parse_version("v0.2") == tagger.parse_version("0.2"))


class InternetConnectionTests(unittest.TestCase):
    def setUp(self):
        self.original_create_connection = tagger.socket.create_connection

    def tearDown(self):
        tagger.socket.create_connection = self.original_create_connection

    def test_check_internet_connection_true_on_success(self):
        class FakeSocket:
            def close(self):
                pass

        tagger.socket.create_connection = lambda *args, **kwargs: FakeSocket()
        self.assertTrue(tagger.check_internet_connection())

    def test_check_internet_connection_false_on_oserror(self):
        def raise_oserror(*args, **kwargs):
            raise OSError("unreachable")

        tagger.socket.create_connection = raise_oserror
        self.assertFalse(tagger.check_internet_connection())


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
