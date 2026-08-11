"""
Organizes tags (Artist/Title/Cover) for audio files based on the filename,
and fetches a cover online (iTunes / SoundCloud API). Any format other than
MP3 (WAV, FLAC, AAC, M4A, OGG, WMA, AIFF, OPUS...) is converted to MP3
(320 kbps) before tagging.

Expected filename format: "Artist - Title.ext"

Contents (in the order they appear below):
    1. Configuration & credentials       - app_base_dir, user_config_dir,
                                            SOUNDCLOUD_CLIENT_ID/SECRET, MUSIC_FOLDER,
                                            SUPPORTED_EXTENSIONS, MENTIONS_TO_REMOVE
    2. Filename & title cleaning          - clean_title, parse_filename, and every
                                            small cleanup rule (track numbers,
                                            brackets, noise words, mentions...)
    3. Reading existing tags              - read_current_info (multi-format)
    4. Folder extraction (flatten)        - extract_audio_files, remove_empty_subfolders
    5. Folder listing & duplicates        - list_audio_files, find_dot_underscore_duplicates
    6. Search query cleaning & mentions   - strip_parentheses, FuviClan/parenthetical detection
    7. Scanning (read-only)               - scan_one_file, scan_files
    8. Cover match validation             - artist_names_match, title_words_overlap,
                                            fix_swapped_artist_title
    9. Cover search - iTunes              - search_cover_itunes
    10. Cover search - SoundCloud         - get_soundcloud_token, search_cover_soundcloud
    11. Format conversion                 - find_ffmpeg, convert_to_mp3
    12. Tag writing                       - open_audio_file, write_tags, fix_title_artist
    13. Processing (Apply)                - process_files, process_folder, main
"""

import os
import struct
import shutil
import re
import sys
import time
import base64
import subprocess
import unicodedata
import requests
from mutagen import File as MutagenFile
from mutagen.mp3 import MP3
from mutagen.wave import WAVE
from mutagen.id3 import TIT2, TPE1, APIC


# ============================================================================
# 1. CONFIGURATION & CREDENTIALS
# ============================================================================

def app_base_dir():
    """
    Folder the app's own files (credentials, .music by default) should live next
    to. When packaged as a onefile .exe (PyInstaller), that's the folder
    containing the .exe itself - NOT the temporary extraction folder.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# --- SoundCloud credentials (read from separate files, next to the app) ---
def user_config_dir():
    """
    A per-user folder that's always writable, regardless of where the app itself
    is installed (e.g. Program Files, which needs admin rights to write into).
    """
    appdata = os.getenv("APPDATA") or os.path.expanduser("~")
    config_dir = os.path.join(appdata, "Track-Tidy")
    os.makedirs(config_dir, exist_ok=True)
    return config_dir


CLIENT_ID_FILE = os.path.join(user_config_dir(), "clientID.txt")
CLIENT_SECRET_FILE = os.path.join(user_config_dir(), "clientSecret.txt")


def read_credential(file_path):
    if not os.path.exists(file_path):
        print(f"  Missing credential file: {file_path}")
        return None
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read().strip()


SOUNDCLOUD_CLIENT_ID = read_credential(CLIENT_ID_FILE)
SOUNDCLOUD_CLIENT_SECRET = read_credential(CLIENT_SECRET_FILE)


def load_id_txt_credentials():
    """
    Convenience file for quick offline testing: "id.txt" next to the app,
    with the Client ID on line 1 and the Client Secret on line 2.
    Takes priority over the normal saved credentials when present.
    """
    path = os.path.join(app_base_dir(), "id.txt")
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines()]
        client_id = lines[0] if len(lines) > 0 and lines[0] else None
        client_secret = lines[1] if len(lines) > 1 and lines[1] else None
        return client_id, client_secret
    except Exception as error:
        print(f"  Could not read id.txt: {error}")
        return None, None


_id_txt_client_id, _id_txt_client_secret = load_id_txt_credentials()
if _id_txt_client_id and _id_txt_client_secret:
    SOUNDCLOUD_CLIENT_ID = _id_txt_client_id
    SOUNDCLOUD_CLIENT_SECRET = _id_txt_client_secret

# Folder containing the audio files to process
MUSIC_FOLDER = ""

# Supported audio file extensions - anything not already .mp3 gets converted
# to .mp3 (320 kbps) before tagging, since each format has its own tagging
# system (ID3, Vorbis comments, MP4 atoms, ASF...) and converting first keeps
# the rest of the pipeline simple and consistent.
SUPPORTED_EXTENSIONS = (
    ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".wma", ".aiff", ".opus",
)


# List of mentions to automatically strip out (add more if needed)
MENTIONS_TO_REMOVE = []

# Whether the album tag should be stripped when writing tags (set by the UI)
DELETE_ALBUM_TAG = True


# ============================================================================
# 2. FILENAME & TITLE CLEANING
# ============================================================================

def clean_title(text):
    for mention in MENTIONS_TO_REMOVE:
        text = re.sub(re.escape(mention), "", text, flags=re.IGNORECASE)
        text = re.sub(r"\(\s+", "(", text)   # trim leftover space right after "("
        text = re.sub(r"\s+\)", ")", text)   # trim leftover space right before ")"
        text = re.sub(r"\s{2,}", " ", text)  # collapse any remaining double spaces
    return text.strip()


def sanitize_filename(name):
    """Replaces characters forbidden in Windows filenames with '_'."""
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def build_display_name(artist, title):
    """Builds 'Artist - Title', or just 'Title' if the artist is blank."""
    return f"{artist} - {title}" if artist else title


def clean_filename(file_name):
    """
    Removes unwanted mentions from the BASE NAME only (keeping the extension and
    any subfolder), then cleans up any leftover extra spaces.
    """
    folder_part, base_name = os.path.split(file_name)
    name_no_ext, extension = os.path.splitext(base_name)
    cleaned = clean_title(name_no_ext)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    new_base = cleaned + extension
    return os.path.join(folder_part, new_base) if folder_part else new_base


def contains_mention_to_remove(file_name):
    base_name = os.path.basename(file_name)
    for mention in MENTIONS_TO_REMOVE:
        if re.search(re.escape(mention), base_name, flags=re.IGNORECASE):
            return True
    return False


# Pattern for names like: "Title Artist, Remix (Remixer) Extended"
# -> becomes artist="Artist", title="Title (Remixer Remix)"
REMIX_WITH_COMMA_PATTERN = re.compile(
    r"^(?P<title>.+)\s+(?P<artist>\S+),\s*Remix\s*\((?P<remixer>[^)]+)\)\s*(?:Extended)?\s*$",
    re.IGNORECASE,
)


GENERIC_MIX_LABELS = {
    "extended mix", "extended edit", "extended",
    "radio edit", "radio mix", "club mix", "original mix", "instrumental mix",
    "mixed", "mix",
}


def remove_redundant_generic_mix(text):
    """
    If the text has a specific named remix/edit credit like "(Raphael Palacci
    Remix)" immediately followed by a generic descriptor like "(Extended Mix)",
    the generic one is redundant (the named remix already implies it) and
    gets removed automatically.
    """
    def is_named_remix(content):
        lowered = content.lower().strip()
        has_named_keyword = any(keyword in lowered for keyword in ("remix", "edit", "reboot", "bootleg"))
        return has_named_keyword and lowered not in GENERIC_MIX_LABELS

    def is_generic_mix(content):
        return content.lower().strip() in GENERIC_MIX_LABELS

    def replacement(match):
        first, second = match.group(1), match.group(2)
        if is_named_remix(first) and is_generic_mix(second):
            return f"({first})"
        return match.group(0)

    return re.sub(r"\(([^)]*)\)\s*\(([^)]*)\)", replacement, text)


def balance_parentheses(text):
    """Adds missing closing parentheses at the end, if some were left unclosed."""
    missing = text.count("(") - text.count(")")
    return text + (")" * missing) if missing > 0 else text


DASH_MIX_KEYWORDS = ("remix", "edit", "mix", "bootleg", "reboot")


def try_split_title_mix_artist(text):
    """
    Detects the "Title - Mix Info - Artist" pattern (exactly two dashes,
    where the MIDDLE part is a remix/mix descriptor), e.g.
    "My City's On Fire - Notre Dame Remix - Jimi Jules" ->
    artist="Jimi Jules", title="My City's On Fire (Notre Dame Remix)".
    Returns (artist, title), or None if the pattern doesn't match.
    """
    parts = re.split(r"\s+-\s+", text)
    if len(parts) != 3:
        return None

    title_part, mix_part, artist_part = parts
    if any(keyword in mix_part.lower() for keyword in DASH_MIX_KEYWORDS):
        return artist_part.strip(), f"{title_part.strip()} ({mix_part.strip()})"
    return None


def reformat_trailing_dash_mix(text):
    """
    If text ends with " - <mix descriptor>" (e.g. "Related - Original Mix"),
    converts it to "Related (Original Mix)". Returns the text unchanged if no
    such pattern is found.
    """
    match = re.match(r"^(.+?)\s+-\s+([^-]+)$", text)
    if not match:
        return text

    before, after = match.group(1).strip(), match.group(2).strip()
    if any(keyword in after.lower() for keyword in DASH_MIX_KEYWORDS):
        return f"{before} ({after})"
    return text


def parse_filename(file_name):
    base_name = os.path.basename(file_name)
    name_no_ext = os.path.splitext(base_name)[0]
    name_no_ext = re.sub(r"^\d{1,3}\s*[.\-]\s*", "", name_no_ext)  # drop a leading track number like "01. ", "01-" or "076 - "

    if name_no_ext == name_no_ext.lower():
        # Entirely lowercase (likely a raw download, not a properly tagged name):
        # turn underscores into spaces and capitalize each word. Left alone if
        # there's already ANY uppercase letter, to avoid ruining stylized artist
        # names like "SCH" or "ACRAZE".
        name_no_ext = name_no_ext.replace("_", " ").title()

    name_no_ext = balance_parentheses(name_no_ext)
    name_no_ext = remove_redundant_generic_mix(name_no_ext)
    name_no_ext = name_no_ext.replace("._", "")
    name_no_ext = clean_title(name_no_ext)

    # Special case: "Title Artist, Remix (Remixer) Extended"
    remix_match = REMIX_WITH_COMMA_PATTERN.match(name_no_ext)
    if remix_match:
        raw_title = remix_match.group("title").strip()
        artist = remix_match.group("artist").strip()
        remixer = remix_match.group("remixer").strip()
        title = f"{raw_title} ({remixer} Remix)"
        return artist, title

    # Special case: "Title - Mix Info - Artist" (three dash-separated parts)
    title_mix_artist = try_split_title_mix_artist(name_no_ext)
    if title_mix_artist:
        return title_mix_artist

    # Standard case: "Artist - Title"
    match = re.match(r"^(.+?)\s*-\s*(.+)$", name_no_ext)
    if match:
        artist = match.group(1).strip()
        title = reformat_trailing_dash_mix(match.group(2).strip())
        return artist, title

    return None, None


def resolve_artist_title(file_name, current_artist, current_title):
    """
    Determines the best (artist, title, tags_already_present) for a file,
    combining the filename-based guess with a check against the file's own
    EXISTING tags, which take priority whenever they look more reliable than
    the filename. This is the single source of truth for this decision - used
    both at scan time and whenever a later mention change requires
    recomputing it, so the two never drift apart.
    """
    detected_artist, detected_title = parse_filename(file_name)

    if detected_artist is None and detected_title is None:
        if current_artist and current_title:
            # No dash in the filename, but the file's own tags already have
            # both an artist and a title -> use those to rename it properly.
            detected_artist = current_artist
            detected_title = clean_title(current_title)
        else:
            # Tags are empty too: bypass, fall back to the raw filename as the
            # title, with a deliberately blank artist rather than skipping it.
            detected_title = os.path.splitext(os.path.basename(file_name))[0]
            detected_artist = ""

    # If the "title" tag itself looks like a full "Artist - Title" string
    # (e.g. a badly-tagged multi-artist collab where the title field got
    # everything and the artist field only kept one name), re-split it instead
    # of trusting the raw tags - this is stronger evidence than a filename.
    # Only do this when the existing (incomplete) artist tag is actually found
    # INSIDE the "before the dash" part, to avoid mangling a normal title that
    # just happens to contain a dash (e.g. "Some Song - Reprise").
    title_looks_combined = False
    if current_title and current_artist:
        combined_match = re.match(r"^(.+?)\s*-\s*(.+)$", current_title)
        if combined_match:
            candidate_artist = combined_match.group(1).strip()
            candidate_artist = re.sub(r"^\d{1,3}\s*[.\-]\s*", "", candidate_artist).strip()
            candidate_title = clean_title(remove_redundant_generic_mix(combined_match.group(2).strip()))
            if current_artist.strip().lower() in candidate_artist.lower():
                detected_artist = candidate_artist
                detected_title = candidate_title
                title_looks_combined = True

        if not title_looks_combined:
            # Also handles a title tag like "Related - Original Mix" (artist tag
            # already correct on its own) - turns the trailing dash-separated
            # mix descriptor into a parenthetical instead.
            reformatted_title = clean_title(reformat_trailing_dash_mix(current_title))
            if reformatted_title != current_title:
                detected_artist = current_artist
                detected_title = reformatted_title
                title_looks_combined = True

    # If the file's OWN tags already have both an artist and a title (and the
    # title tag isn't secretly a combined "Artist - Title" string, handled
    # above), trust those over the filename - a filename can be
    # truncated/garbled even when the tags themselves are correct.
    tags_already_present = bool(current_artist and current_title) and not title_looks_combined
    if tags_already_present:
        detected_artist = current_artist
        detected_title = clean_title(current_title)

    return detected_artist, detected_title, tags_already_present


# ============================================================================
# 3. READING EXISTING TAGS
# ============================================================================

def read_wav_riff_info_tags(file_path):
    """
    Reads Title/Artist from a WAV file's RIFF INFO chunk (INAM/IART) - the
    format many DJ tools (Rekordbox, Serato, Traktor) use instead of ID3.
    Returns (artist, title), with None for whichever isn't found.
    """
    artist = None
    title = None
    try:
        with open(file_path, "rb") as f:
            riff_header = f.read(12)
            if len(riff_header) < 12 or riff_header[:4] != b"RIFF" or riff_header[8:12] != b"WAVE":
                return None, None

            while True:
                chunk_header = f.read(8)
                if len(chunk_header) < 8:
                    break
                chunk_id, chunk_size = struct.unpack("<4sI", chunk_header)

                if chunk_id == b"LIST":
                    list_type = f.read(4)
                    remaining = chunk_size - 4
                    if list_type == b"INFO":
                        end_position = f.tell() + remaining
                        while f.tell() < end_position:
                            sub_header = f.read(8)
                            if len(sub_header) < 8:
                                break
                            sub_id, sub_size = struct.unpack("<4sI", sub_header)
                            sub_data = f.read(sub_size)
                            if sub_size % 2 == 1:
                                f.read(1)  # padding byte
                            text = sub_data.rstrip(b"\x00").decode("utf-8", errors="ignore").strip()
                            if sub_id == b"INAM":
                                title = text or None
                            elif sub_id == b"IART":
                                artist = text or None
                        if chunk_size % 2 == 1:
                            f.read(1)  # padding byte for the LIST chunk itself
                    else:
                        f.seek(remaining, 1)
                        if remaining % 2 == 1:
                            f.read(1)
                else:
                    f.seek(chunk_size, 1)
                    if chunk_size % 2 == 1:
                        f.read(1)

                if artist is not None and title is not None:
                    break
    except Exception:
        pass

    return artist, title


def read_current_info(file_path):
    """
    READ-ONLY read of a file's current tags (doesn't modify anything).
    Handles the different tagging systems used across formats: ID3
    (mp3/wav/aiff), MP4 atoms (m4a/aac/alac), Vorbis comments (flac/ogg/opus),
    and ASF (wma, title/artist only - cover art isn't read for this one).
    For WAV specifically, also falls back to the RIFF INFO chunk (INAM/IART)
    used by many DJ tools instead of ID3.
    Returns (has_cover, current_artist, current_title, cover_bytes).
    """
    try:
        audio = MutagenFile(file_path)
    except Exception:
        audio = None

    current_artist = None
    current_title = None
    has_cover = False
    cover_bytes = None

    if audio is not None and audio.tags is not None:
        tags = audio.tags

        try:
            if hasattr(tags, "getall") and ("TIT2" in tags or "TPE1" in tags or "APIC" in tags):
                # ID3-based: mp3, wav, aiff
                if "TIT2" in tags:
                    current_title = str(tags["TIT2"].text[0])
                if "TPE1" in tags:
                    current_artist = str(tags["TPE1"].text[0])
                covers = tags.getall("APIC")
                has_cover = bool(covers)
                cover_bytes = covers[0].data if covers else None

            elif "\xa9nam" in tags or "\xa9ART" in tags or "covr" in tags:
                # MP4 atoms: m4a, aac, alac
                if "\xa9nam" in tags:
                    current_title = str(tags["\xa9nam"][0])
                if "\xa9ART" in tags:
                    current_artist = str(tags["\xa9ART"][0])
                covers = tags.get("covr")
                has_cover = bool(covers)
                cover_bytes = bytes(covers[0]) if covers else None

            elif "title" in tags or "artist" in tags:
                # Vorbis comments: flac, ogg, opus
                if tags.get("title"):
                    current_title = str(tags["title"][0])
                if tags.get("artist"):
                    current_artist = str(tags["artist"][0])
                pictures = getattr(audio, "pictures", None)
                if pictures:
                    has_cover = True
                    cover_bytes = pictures[0].data

            elif "Title" in tags or "Author" in tags:
                # ASF: wma (cover art not read for this format)
                if tags.get("Title"):
                    current_title = str(tags["Title"][0])
                if tags.get("Author"):
                    current_artist = str(tags["Author"][0])

        except Exception:
            pass

    if file_path.lower().endswith(".wav") and (not current_artist or not current_title):
        # Many DJ tools (Rekordbox, Serato, Traktor) write WAV metadata as a
        # RIFF INFO chunk instead of ID3 - try that if the usual tags are missing.
        riff_artist, riff_title = read_wav_riff_info_tags(file_path)
        current_artist = current_artist or riff_artist
        current_title = current_title or riff_title

    try:
        if current_artist:
            current_artist = re.sub(r"^\d{1,3}\s*[.\-]\s*", "", current_artist).strip() or None
    except Exception:
        pass

    return has_cover, current_artist, current_title, cover_bytes


EXTRACTABLE_AUDIO_EXTENSIONS = SUPPORTED_EXTENSIONS + (".alac",)


# ============================================================================
# 4. FOLDER EXTRACTION (FLATTEN)
# ============================================================================

def extract_audio_files(root_folder, log=print):
    """
    Recursively finds audio files (mp3, wav, flac, aac, m4a, ogg, wma, aiff,
    alac, opus...) sitting inside subfolders of root_folder and moves them
    directly into root_folder (flattening the structure). Files already
    directly in root_folder are left untouched. Returns the number of files
    actually moved.
    """
    moved_count = 0
    root_abspath = os.path.abspath(root_folder)

    for current_folder, _dirs, files in os.walk(root_folder):
        if os.path.abspath(current_folder) == root_abspath:
            continue  # already directly in the target folder, nothing to do

        for name in files:
            if not name.lower().endswith(EXTRACTABLE_AUDIO_EXTENSIONS):
                continue

            source_path = os.path.join(current_folder, name)
            destination_path = os.path.join(root_folder, name)

            if os.path.exists(destination_path):
                base, extension = os.path.splitext(name)
                counter = 1
                while os.path.exists(destination_path):
                    destination_path = os.path.join(root_folder, f"{base} ({counter}){extension}")
                    counter += 1

            try:
                shutil.move(source_path, destination_path)
                relative_source = os.path.relpath(source_path, root_folder)
                log(f"  Extracted: '{relative_source}' -> '{os.path.basename(destination_path)}'")
                moved_count += 1
            except Exception as error:
                log(f"  Error moving '{source_path}': {error}")

    return moved_count


def remove_empty_subfolders(root_folder, log=print):
    """Removes now-empty subfolders left behind after extraction. Returns how many were removed."""
    removed_count = 0
    root_abspath = os.path.abspath(root_folder)

    for current_folder, _dirs, _files in os.walk(root_folder, topdown=False):
        if os.path.abspath(current_folder) == root_abspath:
            continue
        try:
            if not os.listdir(current_folder):
                os.rmdir(current_folder)
                removed_count += 1
        except Exception as error:
            log(f"  Could not remove empty folder '{current_folder}': {error}")

    return removed_count


# ============================================================================
# 5. FOLDER LISTING & DUPLICATES
# ============================================================================

def list_audio_files():
    """
    Recursively walks MUSIC_FOLDER and its subfolders, and returns the sorted list
    of relative paths of the audio files found (without reading any tags).
    Fast: useful for detecting new files without rescanning everything.
    """
    if not os.path.isdir(MUSIC_FOLDER):
        return []

    audio_files = []
    for current_folder, _, file_names in os.walk(MUSIC_FOLDER):
        for name in file_names:
            if name.lower().endswith(SUPPORTED_EXTENSIONS):
                absolute_path = os.path.join(current_folder, name)
                relative_path = os.path.relpath(absolute_path, MUSIC_FOLDER)
                audio_files.append(relative_path)

    audio_files.sort()
    return audio_files


def get_audio_duration(file_path):
    """Returns the audio length in seconds, or None if it can't be read."""
    try:
        audio = MutagenFile(file_path)
        if audio is not None and audio.info is not None:
            return audio.info.length
    except Exception:
        pass
    return None


def find_dot_underscore_duplicates(file_list):
    """
    Detects pairs where one file is named exactly like another but with a
    "._" prefix on its base name (e.g. a leftover macOS sidecar/duplicate).
    Considered a duplicate if either the audio duration matches (within a
    small tolerance), OR the duration couldn't be read at all for one/both
    (the matching filename alone is still a strong enough signal in that case).
    Returns a list of (dot_underscore_file, normal_file) tuples, both paths
    relative to MUSIC_FOLDER.
    """
    file_set = set(file_list)
    pairs = []

    for file_name in file_list:
        folder_part, base_name = os.path.split(file_name)
        if not base_name.startswith("._"):
            continue

        normal_base_name = base_name[2:]
        normal_file = os.path.join(folder_part, normal_base_name) if folder_part else normal_base_name

        if normal_file not in file_set:
            continue

        dot_duration = get_audio_duration(os.path.join(MUSIC_FOLDER, file_name))
        normal_duration = get_audio_duration(os.path.join(MUSIC_FOLDER, normal_file))

        durations_unreadable = dot_duration is None or normal_duration is None
        durations_match = (
            not durations_unreadable and abs(dot_duration - normal_duration) < 2
        )

        if durations_unreadable or durations_match:
            pairs.append((file_name, normal_file))

    return pairs


# ============================================================================
# 6. SEARCH QUERY CLEANING & MENTION DETECTION
# ============================================================================

def strip_trailing_noise_words(text):
    """Removes standalone noise words (mastering labels, not part of the real title)."""
    cleaned = re.sub(r"\bmaster\b", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def strip_parentheses(text):
    """Removes any '(...)' or '[...]' groups AND noise words, for the cover search query only."""
    cleaned = re.sub(r"\([^)]*\)", "", text)
    cleaned = re.sub(r"\[[^\]]*\]", "", cleaned)
    cleaned = strip_trailing_noise_words(cleaned)
    cleaned = re.sub(r"[\(\)\[\]]", "", cleaned)  # drop stray unmatched brackets (typos)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


FUVICLAN_PATTERN = re.compile(r"by\s*fuvi\s*clan", re.IGNORECASE)


def detect_fuviclan_mention(file_name):
    """
    Looks for a "By Fuvi Clan" (any spacing/casing) mention in the raw filename.
    Returns the exact matched substring if found, or None.
    """
    base_name = os.path.basename(file_name)
    match = FUVICLAN_PATTERN.search(base_name)
    return match.group(0) if match else None


def detect_parenthetical_mentions(text):
    """
    Returns every parenthesized OR bracketed group found in the given text
    (meant to be the ALREADY CLEANED title, i.e. after existing mentions have
    been stripped), INCLUDING the brackets/parentheses themselves, except any
    group containing "Edit" or "Remix" (those are considered meaningful and
    are never suggested).
    """
    mentions = []
    for pattern in (r"\([^)]*\)", r"\[[^\]]*\]"):
        for match in re.finditer(pattern, text):
            group = match.group(0)
            lowered = group.lower()

            if any(keyword in lowered for keyword in ("edit", "remix", "reboot", "bootleg")):
                continue  # always kept, never suggested

            if group not in mentions:
                mentions.append(group)

    return mentions


# ============================================================================
# 7. SCANNING (READ-ONLY)
# ============================================================================

def scan_one_file(file_name, soundcloud_token, log=print, on_new_mention=None):
    """
    Analyzes a single file (path relative to MUSIC_FOLDER): current tags,
    info detected from the name, and online cover search.
    Returns the corresponding info dict.
    """
    full_path = os.path.join(MUSIC_FOLDER, file_name)

    # Detect a "By Fuvi Clan" mention and report it as a SUGGESTION only.
    # It won't affect this file's title unless the user promotes it to "To remove".
    fuviclan_mention = detect_fuviclan_mention(file_name)
    if fuviclan_mention and on_new_mention:
        on_new_mention(fuviclan_mention)

    has_cover, current_artist, current_title, current_cover_bytes = read_current_info(full_path)
    log(f"  [debug] Current tags read: artist='{current_artist}', title='{current_title}'")
    needs_conversion = not file_name.lower().endswith(".mp3")

    detected_artist, detected_title, tags_already_present = resolve_artist_title(
        file_name, current_artist, current_title
    )
    search_source_artist = detected_artist
    search_source_title = detected_title

    # Suggest every other parenthesized mention found in the CLEANED title
    # (i.e. what actually gets displayed), so already-handled mentions like
    # "By Fuvi Clan" don't also show up as a redundant separate suggestion.
    if on_new_mention and detected_title:
        for mention in detect_parenthetical_mentions(detected_title):
            on_new_mention(mention)

    found_cover_image = None
    cover_source = None
    if search_source_artist and search_source_title:
        search_title = strip_parentheses(search_source_title)
        has_parenthetical = search_title != search_source_title

        match_result = None
        if has_parenthetical:
            # Likely a remix/edit -> those live on SoundCloud far more often than
            # iTunes, so try it FIRST with a specific query (parentheses/brackets
            # kept, since they help pinpoint the exact remix - only noise words
            # like "Master" are removed).
            if not SOUNDCLOUD_RATE_LIMITED and not SOUNDCLOUD_UNAVAILABLE:
                soundcloud_title = strip_trailing_noise_words(search_source_title)
                match_result = search_cover_soundcloud(
                    search_source_artist, soundcloud_title, soundcloud_token, log=log
                )
                if match_result:
                    cover_source = "SoundCloud"

            if not match_result:
                match_result = search_cover_itunes(search_source_artist, search_title, log=log)
                if match_result:
                    cover_source = "iTunes"
        else:
            match_result = search_cover_itunes(search_source_artist, search_title, log=log)
            if match_result:
                cover_source = "iTunes"
            elif not SOUNDCLOUD_RATE_LIMITED and not SOUNDCLOUD_UNAVAILABLE:
                soundcloud_title = strip_trailing_noise_words(search_source_title)
                match_result = search_cover_soundcloud(
                    search_source_artist, soundcloud_title, soundcloud_token, log=log
                )
                if match_result:
                    cover_source = "SoundCloud"

        if match_result:
            found_cover_image, returned_artist, returned_title = match_result

            # Only try to fix a swap on the FILENAME-derived guess - the file's
            # own existing tags are trusted as-is and never rewritten this way.
            if not tags_already_present:
                corrected_artist, corrected_title = fix_swapped_artist_title(
                    detected_artist, detected_title, returned_artist, returned_title
                )
                if corrected_artist != detected_artist:
                    log(
                        f"  Artist/title looked swapped -> corrected to "
                        f"'{corrected_artist} - {corrected_title}'"
                    )
                    detected_artist, detected_title = corrected_artist, corrected_title

    return {
        "file": file_name,
        "format": os.path.splitext(file_name)[1].lstrip(".").upper(),
        "detected_artist": detected_artist,
        "detected_title": detected_title,
        "current_artist": current_artist,
        "current_title": current_title,
        "has_cover": has_cover,
        "mention_detected": contains_mention_to_remove(file_name),
        "convert": needs_conversion,
        # If the file's own tags are already complete, don't default to
        # overwriting them with a filename-derived guess that could be worse
        # (e.g. a truncated/garbled filename from some export tool).
        "apply_changes": bool(detected_title),
        "found_cover_image": found_cover_image,
        "cover_source": cover_source,
        "current_cover_bytes": current_cover_bytes,
        "title_override": None,
        "artist_override": None,
        "processed": False,
        "final_path": None,
    }


SOUNDCLOUD_RATE_LIMITED = False  # set for the current run once a 429 is hit
SOUNDCLOUD_UNAVAILABLE = False  # set for the current run when no credentials are configured at all


def scan_files(file_list, on_file_scanned=None, log=print, on_new_mention=None, on_rate_limited=None,
               should_cancel=None):
    """
    Scans ONLY the files in the given list (relative paths).
    Useful for an incremental scan (only reprocess new files).
    on_file_scanned(info) is called right after each file is analyzed,
    to allow a progressive display instead of waiting for the whole thing to finish.
    should_cancel() is checked before each file - if it returns True, the scan
    stops early and returns whatever was scanned so far.
    """
    global SOUNDCLOUD_RATE_LIMITED, SOUNDCLOUD_UNAVAILABLE
    SOUNDCLOUD_RATE_LIMITED = False
    SOUNDCLOUD_UNAVAILABLE = False

    if not file_list:
        return []

    if not SOUNDCLOUD_CLIENT_ID or not SOUNDCLOUD_CLIENT_SECRET:
        # No point even trying to authenticate - skip SoundCloud entirely for
        # this run (iTunes is still tried normally for every file).
        log("  [SoundCloud] No credentials configured - skipping SoundCloud for this scan.")
        SOUNDCLOUD_UNAVAILABLE = True
        soundcloud_token = None
    else:
        def _mark_rate_limited():
            global SOUNDCLOUD_RATE_LIMITED
            SOUNDCLOUD_RATE_LIMITED = True
            if on_rate_limited:
                on_rate_limited()

        soundcloud_token = get_soundcloud_token(log=log, on_rate_limited=_mark_rate_limited)

    results = []

    for file_name in file_list:
        if should_cancel and should_cancel():
            log("  Scan cancelled.")
            break

        info = scan_one_file(file_name, soundcloud_token, log=log, on_new_mention=on_new_mention)
        results.append(info)
        if on_file_scanned:
            on_file_scanned(info)

    return results


# ============================================================================
# 8. COVER MATCH VALIDATION
# ============================================================================

def strip_generic_mix_suffix(text):
    """
    Removes a trailing GENERIC descriptor in parentheses, like "(Mixed)" or
    "(Extended Mix)", for comparison purposes only - a NAMED remix (e.g.
    "(DJ Name Remix)") is left untouched, since that's a real difference.
    """
    match = re.search(r"\s*\(([^)]*)\)\s*$", text)
    if match and match.group(1).strip().lower() in GENERIC_MIX_LABELS:
        return text[:match.start()].strip()
    return text


FEATURE_SUFFIX_RE = re.compile(r"\s*[\(\[](?:feat\.?|ft\.?|featuring)\s+[^)\]]*[\)\]]\s*$", re.IGNORECASE)


def strip_feature_suffix(text):
    """
    Removes a trailing "(feat. X)" / "(ft. X)" / "[featuring X]" credit, for
    comparison purposes only - a store's listing often includes the featured
    artist in the title even when our own tags/filename don't, which
    shouldn't by itself count as a mismatch.
    """
    return FEATURE_SUFFIX_RE.sub("", text).strip()


def exact_match(text_a, text_b):
    """Case/whitespace-insensitive EXACT match (not the looser substring/word-based checks below)."""
    def normalize(text):
        return re.sub(r"\s+", " ", text.strip().lower())
    return normalize(text_a) == normalize(text_b)


def split_artist_names(text):
    """Splits a multi-artist string on any common separator (,  &  x  X  vs  feat.  ft.  and)."""
    parts = re.split(r"\s*(?:,|&|/|\bx\b|\bvs\b|\bfeat\.?\b|\bft\.?\b|\band\b)\s*", text, flags=re.IGNORECASE)
    return {p.strip().lower() for p in parts if p.strip()}


def artist_sets_match(expected_artist, returned_artist):
    """
    Exact match for a list of artists, ignoring order and separator style
    (e.g. "A, B, C" vs "C & A x B" are considered the same set of artists).
    """
    return split_artist_names(expected_artist) == split_artist_names(returned_artist)


def artist_names_match(expected_artist, returned_artist):
    """
    Loosely checks whether the artist returned by a search actually corresponds
    to the expected one, to reject unrelated tracks that only match by title
    (e.g. iTunes free-text search returning a same-titled song by another artist).
    """
    if not returned_artist:
        return False

    expected_lower = expected_artist.lower()
    returned_lower = returned_artist.lower()

    # Split on common multi-artist separators (filenames often list several artists)
    fragments = re.split(r"[,&]| feat\.?| ft\.?| x ", expected_lower)

    for fragment in fragments:
        fragment = fragment.strip()
        if fragment and (fragment in returned_lower or returned_lower in fragment):
            return True

    return False


def title_words_overlap(expected_title, returned_title):
    """
    Checks that the returned title shares at least one meaningful word with the
    expected one, to reject a DIFFERENT song by the same (correct) artist
    (e.g. matching "Saint Laurent" when looking for "Je La Connais").
    """
    def significant_words(text):
        return {w for w in re.findall(r"\w+", text.lower()) if len(w) >= 3}

    expected_words = significant_words(expected_title)
    if not expected_words:
        return True  # nothing meaningful to compare against, don't block on it

    returned_words = significant_words(returned_title)
    return bool(expected_words & returned_words)


def fix_swapped_artist_title(detected_artist, detected_title, returned_artist, returned_title):
    """
    If the online match indicates the detected artist/title are actually
    swapped (e.g. filename put the title where the artist should be), returns
    the corrected (artist, title) pair. A trailing "(...)" qualifier (like
    "(Original Mix)") always stays attached to the TITLE side, regardless of
    which raw string it was originally found on. Otherwise returns the
    original values unchanged.
    """
    if artist_names_match(detected_artist, returned_artist):
        return detected_artist, detected_title  # already correct

    looks_swapped = (
        artist_names_match(detected_title, returned_artist)
        and title_words_overlap(detected_artist, returned_title)
    )
    if not looks_swapped:
        return detected_artist, detected_title

    new_artist = detected_title
    new_title = detected_artist

    # Find a "(...)" qualifier ANYWHERE in the (soon-to-be) artist, not just at
    # the very end - trailing junk like a stray track number can follow it -
    # and move it to the title, where it belongs.
    qualifier_match = re.search(r"\s*\(([^)]*)\)\s*", new_artist)
    if qualifier_match:
        qualifier = f"({qualifier_match.group(1)})"
        new_artist = new_artist[:qualifier_match.start()] + " " + new_artist[qualifier_match.end():]
        new_artist = re.sub(r"\s+\d+\s*$", "", new_artist)  # drop a leftover trailing number
        new_artist = re.sub(r"\s{2,}", " ", new_artist).strip()
        new_title = f"{new_title} {qualifier}".strip()

    return new_artist, new_title


# ============================================================================
# 9. COVER SEARCH - ITUNES
# ============================================================================

def search_cover_itunes(artist, title, log=print, max_retries=2):
    """
    Retries on HTTP 429 (rate limited) with a short backoff before giving up.
    Unlike SoundCloud, iTunes needs no token to reuse - a plain retry is
    enough to ride out a short burst instead of silently returning no cover
    for a track that would otherwise have matched.
    """
    try:
        for attempt in range(max_retries + 1):
            response = requests.get(
                "https://itunes.apple.com/search",
                params={"term": f"{artist} {title}", "entity": "song", "limit": 1},
                timeout=10,
            )

            if response.status_code == 429 and attempt < max_retries:
                wait_seconds = 2 * (attempt + 1)
                log(f"  [iTunes] Rate limited, retrying in {wait_seconds}s...")
                time.sleep(wait_seconds)
                continue

            break

        if response.status_code != 200:
            log(f"  [iTunes] Search failed: HTTP {response.status_code} - {response.text[:300]}")
            return None

        results = response.json().get("results", [])
        if not results:
            log(f"  [iTunes] No result at all for '{artist} - {title}'")
            return None

        result = results[0]
        # iTunes sometimes returns accented text in NFD form (e.g. "a" + a
        # combining accent, instead of the precomposed "à"), which looks
        # identical when printed but breaks both string comparison AND
        # printing on a Windows console (cp1252 can't encode combining
        # accents on their own). Normalize to NFC to match how tags/filenames
        # are represented.
        returned_artist = unicodedata.normalize("NFC", result.get("artistName", ""))
        returned_title = unicodedata.normalize("NFC", result.get("trackName", ""))

        title_normalized = strip_feature_suffix(strip_generic_mix_suffix(title))
        returned_title_normalized = strip_feature_suffix(strip_generic_mix_suffix(returned_title))

        artist_ok = artist_sets_match(artist, returned_artist) and exact_match(title_normalized, returned_title_normalized)
        swapped_ok = exact_match(title, returned_artist) and artist_sets_match(artist, returned_title_normalized)

        if not (artist_ok or swapped_ok):
            log(
                f"  [iTunes] Match rejected (not an exact match): expected '{artist} - {title}', "
                f"got '{returned_artist} - {returned_title}'"
            )
            return None

        cover_url = result.get("artworkUrl100")
        if not cover_url:
            log(f"  [iTunes] Match found for '{artist} - {title}' but it has no artwork URL.")
            return None

        cover_url_hd = cover_url.replace("100x100", "600x600")
        image_response = requests.get(cover_url_hd, timeout=10)

        if image_response.status_code == 200:
            return image_response.content, returned_artist, returned_title

        log(f"  [iTunes] Image download failed (HTTP {image_response.status_code}) for '{artist} - {title}'")
        return None

    except Exception as error:
        log(f"  [iTunes] Error while searching for cover: {error}")
        return None


# ============================================================================
# 10. COVER SEARCH - SOUNDCLOUD
# ============================================================================

_cached_soundcloud_token = None
_cached_token_expiry = 0  # Unix timestamp


def get_soundcloud_token(log=print, on_rate_limited=None):
    global _cached_soundcloud_token, _cached_token_expiry

    # Reuse the cached token if it's still valid (with a 60s safety margin)
    if _cached_soundcloud_token and time.time() < _cached_token_expiry - 60:
        return _cached_soundcloud_token

    if not SOUNDCLOUD_CLIENT_ID or not SOUNDCLOUD_CLIENT_SECRET:
        log("  [SoundCloud] No credentials found (clientID.txt / clientSecret.txt missing or empty).")
        return None

    try:
        credentials = f"{SOUNDCLOUD_CLIENT_ID}:{SOUNDCLOUD_CLIENT_SECRET}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        response = requests.post(
            "https://secure.soundcloud.com/oauth/token",
            headers={
                "Authorization": f"Basic {encoded_credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
            timeout=10,
        )

        if response.status_code == 200:
            payload = response.json()
            _cached_soundcloud_token = payload.get("access_token")
            _cached_token_expiry = time.time() + payload.get("expires_in", 3600)
            return _cached_soundcloud_token

        if response.status_code == 429:
            log(
                "  [SoundCloud] Rate limit reached (too many token requests). "
                "Try again later — the app now reuses the same token across scans "
                "to avoid this."
            )
            if on_rate_limited:
                on_rate_limited()
        else:
            log(f"  [SoundCloud] Authentication error: HTTP {response.status_code} - {response.text[:300]}")
        return None

    except Exception as error:
        log(f"  [SoundCloud] Error during authentication: {error}")
        return None


def search_cover_soundcloud(artist, title, token, log=print):
    if not token:
        return None

    try:
        response = requests.get(
            "https://api.soundcloud.com/tracks",
            headers={"Authorization": f"OAuth {token}"},
            params={"q": f"{artist} {title}", "limit": 1},
            timeout=10,
        )

        if response.status_code != 200:
            log(f"  [SoundCloud] Search failed: HTTP {response.status_code} - {response.text[:300]}")
            return None

        results = response.json()
        if not results:
            log(f"  [SoundCloud] No result at all for '{artist} - {title}'")
            return None

        result = results[0]
        # Same NFD/NFC issue as on the iTunes side - normalize before any
        # comparison or logging.
        track_title = unicodedata.normalize("NFC", result.get("title", ""))
        uploader_name = unicodedata.normalize("NFC", result.get("user", {}).get("username", ""))

        artist_ok = (
            artist_names_match(artist, track_title) or artist_names_match(artist, uploader_name)
        ) and title_words_overlap(title, track_title)

        swapped_ok = (
            artist_names_match(title, track_title) or artist_names_match(title, uploader_name)
        ) and title_words_overlap(artist, track_title)

        if not (artist_ok or swapped_ok):
            log(
                f"  [SoundCloud] Match rejected: expected '{artist} - {title}', "
                f"got track title '{track_title}' / uploader '{uploader_name}'"
            )
            return None

        cover_url = result.get("artwork_url")
        if not cover_url:
            return None

        cover_url_hd = cover_url.replace("-large", "-t500x500")
        image_response = requests.get(cover_url_hd, timeout=10)

        if image_response.status_code == 200:
            return image_response.content, uploader_name or track_title, track_title

        log(f"  [SoundCloud] Image download failed (HTTP {image_response.status_code}) for '{artist} - {title}'")
        return None

    except Exception as error:
        log(f"  [SoundCloud] Error while searching for cover: {error}")
        return None


# ============================================================================
# 11. FORMAT CONVERSION
# ============================================================================

def find_ffmpeg():
    """
    Looks for ffmpeg.exe next to the app first (bundled with the installer),
    falling back to the system PATH if it's not there.
    """
    bundled_path = os.path.join(app_base_dir(), "ffmpeg.exe")
    if os.path.exists(bundled_path):
        return bundled_path
    return "ffmpeg"  # relies on it being installed and in the system PATH


def convert_to_mp3(source_path):
    """
    Converts ANY audio file (wav, flac, aac, m4a, ogg, wma, aiff, opus...) to
    .mp3 at 320 kbps using FFmpeg, which reads the input format automatically -
    no per-format handling needed here. Removes the original file on success.
    """
    mp3_path = os.path.splitext(source_path)[0] + ".mp3"
    try:
        result = subprocess.run(
            [find_ffmpeg(), "-i", source_path, "-b:a", "320k", "-y", mp3_path],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            print(f"  FFmpeg error during conversion: {result.stderr[-300:]}")
            return None

        os.remove(source_path)
        return mp3_path

    except FileNotFoundError:
        print("  FFmpeg was not found. Check that it's installed and in the PATH.")
        return None
    except Exception as error:
        print(f"  Error during conversion: {error}")
        return None


# ============================================================================
# 12. TAG WRITING
# ============================================================================

def open_audio_file(file_path):
    if file_path.lower().endswith(".mp3"):
        audio = MP3(file_path)
    elif file_path.lower().endswith(".wav"):
        audio = WAVE(file_path)
    else:
        raise ValueError("Unsupported file format")

    if audio.tags is None:
        audio.add_tags()

    return audio


def save_audio(audio):
    if isinstance(audio, MP3):
        audio.save(v2_version=3)  # ID3v2.3 for better Windows compatibility
    else:
        audio.save()


def write_tags(file_path, artist, title, cover_image, force_remove_if_missing,
                update_title=True, update_artist=True, update_cover=True):
    """
    Writes the chosen tags:
    - update_title / update_artist: True to write, False to leave as-is
    - update_cover: True to apply the cover logic (replace/remove/keep),
      False to leave the cover untouched entirely
    """
    audio = open_audio_file(file_path)
    tags = audio.tags

    if update_title:
        tags.setall("TIT2", [TIT2(encoding=3, text=[title])])
    if update_artist:
        tags.setall("TPE1", [TPE1(encoding=3, text=[artist])])

    if DELETE_ALBUM_TAG:
        tags.delall("TALB")

    if update_cover:
        if cover_image:
            tags.delall("APIC")
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover_image))
        elif force_remove_if_missing:
            tags.delall("APIC")
        # otherwise: leave the existing cover untouched

    save_audio(audio)


def fix_title_artist(info, artist, title):
    """
    Fixes ONLY the title and artist of an ALREADY PROCESSED file (and renames it
    accordingly), without touching the cover. Updates info['final_path'].
    """
    relative_path = info.get("final_path") or info["file"]
    full_path = os.path.join(MUSIC_FOLDER, relative_path)

    if not os.path.exists(full_path):
        raise FileNotFoundError(f"File not found: {full_path}")

    write_tags(
        full_path, artist, title, cover_image=None, force_remove_if_missing=False,
        update_title=True, update_artist=True, update_cover=False,
    )

    folder_part = os.path.dirname(relative_path)
    extension = os.path.splitext(relative_path)[1]
    new_base_name = sanitize_filename(build_display_name(artist, title)) + extension
    new_name = os.path.join(folder_part, new_base_name) if folder_part else new_base_name
    new_full_path = os.path.join(MUSIC_FOLDER, new_name)

    if new_full_path != full_path:
        os.rename(full_path, new_full_path)

    info["final_path"] = new_name
    return True


# ============================================================================
# 13. PROCESSING (APPLY)
# ============================================================================

def process_files(plan, log=print, on_progress=None, on_file_processed=None, should_cancel=None):
    """
    Processes a list of already-scanned files (see scan_files()).
    Each item in the plan carries its own options:
    convert, update_title, update_artist, update_cover.
    on_file_processed(identifier, success) is called after each file,
    whether it was processed successfully or skipped/failed.
    should_cancel() is called before each file; if it returns True,
    processing stops cleanly (remaining files are left untouched).
    """
    if not plan:
        log("No file to process.")
        return

    total = len(plan)

    for index, info in enumerate(plan, start=1):
        if should_cancel and should_cancel():
            log("Processing cancelled.")
            return

        file_name = info["file"]
        identifier = file_name  # stable key to find the row again in the UI
        log(f"File: {file_name}")

        full_path = os.path.join(MUSIC_FOLDER, file_name)

        if not file_name.lower().endswith(".mp3") and info.get("convert"):
            source_extension = os.path.splitext(file_name)[1].lstrip(".").upper()
            log(f"  Converting .{source_extension.lower()} -> .mp3 (320 kbps)...")
            new_path = convert_to_mp3(full_path)

            if not new_path:
                log("  Conversion failed, file skipped.\n")
                info["processed"] = True
                if on_progress:
                    on_progress(index, total)
                if on_file_processed:
                    on_file_processed(identifier, False)
                continue

            full_path = new_path
            file_name = os.path.relpath(new_path, MUSIC_FOLDER)
            log(f"  Converted to: '{file_name}'")

        artist = info.get("artist_override") or info.get("detected_artist")
        title = info.get("title_override") or info.get("detected_title")

        if not title:
            log("  Missing title, file skipped.\n")
            info["processed"] = True
            if on_progress:
                on_progress(index, total)
            if on_file_processed:
                on_file_processed(identifier, False)
            continue

        log(f"  Artist: '{artist}' | Title: '{title}'")

        update_title = update_artist = update_cover = info.get("apply_changes", True)

        force_remove_if_missing = bool(detect_fuviclan_mention(file_name))

        cover_image = info.get("found_cover_image") if update_cover else None

        write_tags(
            full_path, artist, title, cover_image, force_remove_if_missing,
            update_title=update_title, update_artist=update_artist, update_cover=update_cover,
        )

        if update_title and update_artist:
            folder_part = os.path.dirname(file_name)
            extension = os.path.splitext(file_name)[1]
            new_base_name = sanitize_filename(build_display_name(artist, title)) + extension
            new_name = os.path.join(folder_part, new_base_name) if folder_part else new_base_name
            new_full_path = os.path.join(MUSIC_FOLDER, new_name)

            if new_full_path != full_path:
                os.rename(full_path, new_full_path)
                log(f"  File renamed: '{new_name}'")
                file_name = new_name

        info["final_path"] = file_name  # actual current path, useful for a later fix

        if update_cover and cover_image:
            log("  Tags updated (cover found and added).\n")
        elif update_cover and force_remove_if_missing:
            log("  Tags updated (no cover found -> removed).\n")
        else:
            log("  Tags updated.\n")

        info["processed"] = True
        if on_file_processed:
            on_file_processed(identifier, True)

        if on_progress:
            on_progress(index, total)

    log("Processing complete.")


def process_folder(log=print, on_progress=None):
    """Simple version: scans then processes the whole folder with default settings."""
    plan = scan_files(list_audio_files())
    process_files(plan, log=log, on_progress=on_progress)


def main():
    process_folder(log=print)


if __name__ == "__main__":
    main()
