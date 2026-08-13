"""
Organizes tags (Artist/Title/Cover) for audio files based on the filename,
and fetches a cover online from whichever of iTunes/Spotify/SoundCloud are
enabled (each independently, in that priority order). Any format other than
MP3 (WAV, FLAC, AAC, M4A, OGG, WMA, AIFF, OPUS...) is converted to MP3
(320 kbps) before tagging.

Expected filename format: "Artist - Title.ext"

Contents (in the order they appear below):
    1. Configuration & credentials       - path helpers (app_base_dir,
                                            user_config_dir); SoundCloud and
                                            Spotify credentials;
                                            USE_ITUNES/USE_SPOTIFY/
                                            USE_SOUNDCLOUD; the internet
                                            connectivity check; APP_VERSION,
                                            the update check, and
                                            downloading the installer;
                                            track reporting (Discord
                                            webhook); saved UI settings
                                            (theme...); the processing
                                            history log; MUSIC_FOLDER,
                                            SUPPORTED_EXTENSIONS,
                                            MENTIONS_TO_REMOVE
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
    11. Cover search - Spotify            - get_spotify_token, search_cover_spotify
    12. Format conversion                 - find_ffmpeg, convert_to_mp3
    13. Tag writing                       - open_audio_file, write_tags, fix_title_artist
    14. Processing (Apply)                - process_files, process_folder, main
"""

import os
import socket
import hashlib
import uuid
import struct
import shutil
import re
import sys
import time
import json
import base64
import subprocess
import unicodedata
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import requests
import keyring
import platformdirs
from mutagen import File as MutagenFile
from mutagen.mp3 import MP3
from mutagen.wave import WAVE
from mutagen.aiff import AIFF
from mutagen.id3 import TIT2, TPE1, APIC


def safe_print(text=""):
    """
    Default log() implementation for CLI usage (process_folder()/main()) -
    the GUI always passes its own logger instead. A Windows console using a
    legacy codepage (cp1252 etc.) raises UnicodeEncodeError for text
    containing characters outside that codepage (e.g. an emoji in a
    SoundCloud username/title), which would otherwise abort whatever was
    being logged - including mid-search, silently losing a match - instead
    of just... logging it. Falls back to replacing unencodable characters
    rather than crashing.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding))


# ============================================================================
# 1. CONFIGURATION & CREDENTIALS
# ============================================================================

# --- Path helpers ---

def app_base_dir():
    """
    Folder the app's own files (credentials, .music by default) should live next
    to. When packaged as a onefile .exe (PyInstaller), that's the folder
    containing the .exe itself - NOT the temporary extraction folder.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def user_config_dir():
    """
    A per-user folder that's always writable, regardless of where the app itself
    is installed (e.g. Program Files, which needs admin rights to write into).
    Resolves to %APPDATA%\\Track-Tidy on Windows, ~/Library/Application
    Support/Track-Tidy on macOS, ~/.config/Track-Tidy on Linux.
    """
    config_dir = platformdirs.user_config_dir("Track-Tidy", appauthor=False, roaming=True)
    os.makedirs(config_dir, exist_ok=True)
    return config_dir


# --- SoundCloud/Spotify credentials (via the OS's native credential store -
# Windows Credential Manager, macOS Keychain, Secret Service on Linux) ---

KEYRING_SERVICE = "Track-Tidy"

CLIENT_ID_KEY = "soundcloud_client_id"
CLIENT_SECRET_KEY = "soundcloud_client_secret"
SPOTIFY_CLIENT_ID_KEY = "spotify_client_id"
SPOTIFY_CLIENT_SECRET_KEY = "spotify_client_secret"

# Old plaintext file locations (pre-keyring) - read once at startup as a
# migration path, then deleted. Not used for anything else.
_LEGACY_CREDENTIAL_FILES = {
    CLIENT_ID_KEY: os.path.join(user_config_dir(), "clientID.txt"),
    CLIENT_SECRET_KEY: os.path.join(user_config_dir(), "clientSecret.txt"),
    SPOTIFY_CLIENT_ID_KEY: os.path.join(user_config_dir(), "spotifyClientID.txt"),
    SPOTIFY_CLIENT_SECRET_KEY: os.path.join(user_config_dir(), "spotifyClientSecret.txt"),
}


def write_credential(key, value):
    """Saves a credential via the OS's native credential store instead of
    a file on disk."""
    keyring.set_password(KEYRING_SERVICE, key, value)


def read_credential(key):
    """Returns a saved credential, or None if there isn't one. Migrates a
    legacy PLAINTEXT credential file (from before this was ever
    encrypted) into the keyring on first read, then removes the file.
    Does NOT migrate the DPAPI-encrypted files an older Windows-only
    version of this app wrote (that decryption code needed ctypes.windll,
    which doesn't exist cross-platform) - those are simply left in place,
    unused, and read_credential returns None for them like any other
    missing credential. In practice this means: upgrading from that
    specific older version asks you to re-enter SoundCloud/Spotify
    credentials once; upgrading from anything older (plaintext) or newer
    (already keyring-based) is seamless."""
    try:
        value = keyring.get_password(KEYRING_SERVICE, key)
    except Exception as error:
        print(f"  Could not read credential '{key}': {error}")
        value = None

    if not value:
        legacy_path = _LEGACY_CREDENTIAL_FILES.get(key)
        if legacy_path and os.path.exists(legacy_path):
            try:
                with open(legacy_path, "r", encoding="utf-8") as f:
                    legacy_value = f.read().strip()
                if legacy_value:
                    write_credential(key, legacy_value)
                    value = legacy_value
                os.remove(legacy_path)
            except Exception as error:
                print(f"  Could not migrate legacy credential file '{legacy_path}': {error}")

    return value.strip() if value else None


SOUNDCLOUD_CLIENT_ID = read_credential(CLIENT_ID_KEY)
SOUNDCLOUD_CLIENT_SECRET = read_credential(CLIENT_SECRET_KEY)
SPOTIFY_CLIENT_ID = read_credential(SPOTIFY_CLIENT_ID_KEY)
SPOTIFY_CLIENT_SECRET = read_credential(SPOTIFY_CLIENT_SECRET_KEY)


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


# --- Internet connectivity check ---

def check_internet_connection(timeout=2.5):
    """
    Quick, dependency-free connectivity check - opens a raw TCP connection
    to a well-known, always-up host (Google's public DNS) rather than doing
    a full HTTP request, so it stays fast and doesn't depend on any of the
    services this app actually talks to (iTunes/Spotify/SoundCloud/GitHub)
    being reachable specifically.
    """
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=timeout).close()
        return True
    except OSError:
        return False


# --- App version & update check ---

# Single source of truth for the app's version - shown in the GUI and used to
# check for updates. Bump this (and installer.iss's MyAppVersion) on release.
APP_VERSION = "0.13"
GITHUB_REPO = "k3rwan/track-tidy-releases"  # public, installer-only repo - the source repo is private


def parse_version(version_string):
    """
    Parses a version like "v1.2.3" or "1.2" into a tuple of ints, e.g.
    (1, 2, 3), so versions can be compared with plain tuple comparison.
    Non-numeric/missing parts become 0 rather than raising.
    """
    cleaned = (version_string or "").strip().lstrip("vV")
    parts = []
    for part in cleaned.split("."):
        match = re.match(r"\d+", part)
        parts.append(int(match.group()) if match else 0)
    return tuple(parts) or (0,)


def check_for_update(log=safe_print, timeout=5):
    """
    Checks GitHub for the latest release of GITHUB_REPO and compares it to
    APP_VERSION. Returns (is_newer, latest_version, release_url,
    installer_download_url, expected_sha256) - or (False, None, None, None,
    None) on any failure (offline, GitHub down, rate-limited...), since this
    check should never block or crash the app over a network hiccup.
    expected_sha256 is None whenever the release has no matching
    "<installer name>.sha256" asset (e.g. every release before this was
    added) - download_installer skips verification in that case rather than
    treating an old release as untrusted.
    """
    try:
        response = None
        for attempt in range(2):
            try:
                response = requests.get(
                    f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                    timeout=timeout,
                )
                break
            except requests.exceptions.RequestException:
                # GitHub's edge occasionally resets the very first connection
                # in a pool with no response at all - same flakiness observed
                # on the release-asset download below. One retry clears it.
                if attempt == 0:
                    log("  [Update check] Connection reset (likely transient), retrying...")
                    continue
                raise
        if response.status_code != 200:
            log(f"  [Update check] GitHub returned HTTP {response.status_code}")
            return False, None, None, None, None

        data = response.json()
        latest_tag = data.get("tag_name", "")
        release_url = data.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases/latest"

        installer_extension = ".dmg" if sys.platform == "darwin" else ".exe"
        assets = data.get("assets", [])
        installer_url = None
        installer_name = None
        for asset in assets:
            if asset.get("name", "").lower().endswith(installer_extension):
                installer_url = asset.get("browser_download_url")
                installer_name = asset.get("name", "")
                break

        expected_sha256 = None
        if installer_name:
            checksum_asset_name = (installer_name + ".sha256").lower()
            for asset in assets:
                if asset.get("name", "").lower() == checksum_asset_name:
                    expected_sha256 = _fetch_expected_sha256(asset.get("browser_download_url"), timeout, log)
                    break

        is_newer = parse_version(latest_tag) > parse_version(APP_VERSION)
        return is_newer, latest_tag, release_url, installer_url, expected_sha256

    except Exception as error:
        log(f"  [Update check] Failed: {error}")
        return False, None, None, None, None


def _fetch_expected_sha256(checksum_url, timeout, log):
    """Downloads a small "<installer>.sha256" asset and pulls out the hex
    digest - tolerates both a bare hash and the classic `sha256sum` output
    format ("<hash>  <filename>")."""
    try:
        response = requests.get(checksum_url, timeout=timeout)
        if response.status_code != 200:
            return None
        first_token = response.text.strip().split()[0].lower()
        return first_token if re.fullmatch(r"[0-9a-f]{64}", first_token) else None
    except Exception as error:
        log(f"  [Update check] Could not read checksum: {error}")
        return None


def compute_sha256(file_path, chunk_size=262144):
    digest = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_installer(url, dest_path, on_progress=None, timeout=30, expected_sha256=None, log=safe_print):
    """
    Streams the installer at `url` to `dest_path`, so the app can launch it
    directly instead of the user having to open a browser and download it
    manually. Calls on_progress(downloaded_bytes, total_bytes) after every
    chunk if given (total_bytes is 0 if the server didn't report a
    Content-Length). Returns True on success, False on any failure (never
    raises) - a partial download is removed rather than left behind.
    When expected_sha256 is given (see check_for_update), the downloaded
    file is hashed and compared before returning - a mismatch is treated
    the same as a failed download (file removed, returns False) rather
    than letting a tampered/corrupted installer through.

    A connection-level failure - whether right at the start (a plain
    ConnectionError) or partway through the transfer (ChunkedEncodingError,
    when the connection drops mid-stream - GitHub's release-asset redirect
    resets fairly often on a 70+ MB file, confirmed by hand, not specific
    to any one machine/network) - is retried a few times, with a short
    growing pause between attempts, before giving up.
    """
    max_retries = 3
    for attempt in range(max_retries + 1):
        try:
            response = requests.get(url, stream=True, timeout=timeout)
            if response.status_code != 200:
                return False

            total = int(response.headers.get("Content-Length", 0))
            downloaded = 0

            with open(dest_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=262144):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    if on_progress:
                        on_progress(downloaded, total)

            if expected_sha256 and compute_sha256(dest_path).lower() != expected_sha256.lower():
                log("  [Update] Downloaded installer failed checksum verification - discarding it.")
                os.remove(dest_path)
                return False

            return True

        except requests.exceptions.RequestException:
            try:
                if os.path.exists(dest_path):
                    os.remove(dest_path)
            except Exception:
                pass
            if attempt < max_retries:
                wait_seconds = attempt + 1
                log(f"  [Update] Download connection reset (likely transient), retrying in {wait_seconds}s...")
                time.sleep(wait_seconds)
                continue
            return False

        except Exception:
            try:
                if os.path.exists(dest_path):
                    os.remove(dest_path)
            except Exception:
                pass
            return False


# --- Reporting a track (user -> developer, via Discord) ---

# base64, not encryption - a Discord webhook URL is a bearer token with no
# other auth, and this app ships the source compiled into the .exe, so it
# can't be kept truly secret from someone determined to extract it. This
# just keeps it from showing up in a plain `strings` scan of the binary.
_DISCORD_REPORT_WEBHOOK_URL_B64 = (
    "SECRET_REMOVED"
    "SECRET_REMOVED"
    "SECRET_REMOVED"
)
DISCORD_REPORT_WEBHOOK_URL = base64.b64decode(_DISCORD_REPORT_WEBHOOK_URL_B64).decode("ascii")

# Client-side cooldown between track reports - if the webhook URL is ever
# extracted, this at least stops a single client from flooding the Discord
# channel via _report_track's normal call path.
REPORT_COOLDOWN_SECONDS = 15
_last_report_time = 0


def send_track_report(info, reporter_name=None, timeout=10):
    """
    Posts this track's info to a Discord webhook, so the developer gets a
    notification for tracks users flag as wrong/problematic (e.g. no cover
    found) - a lightweight way to collect real-world matching failures to
    fix later. Attaches the existing cover (as a thumbnail) and/or the
    online-suggested cover (as the main image) when available, so a missing/
    wrong cover is visible at a glance instead of just implied by text.
    Returns True on success, False on any failure (never raises - a failed
    report shouldn't disrupt the user). Also False if called again within
    REPORT_COOLDOWN_SECONDS of the last report (see the module comment
    above - the webhook URL can't be kept truly secret from this app's own
    binary, so this bounds how fast a single client can flood the channel
    if it's ever extracted).
    """
    global _last_report_time
    now = time.time()
    if now - _last_report_time < REPORT_COOLDOWN_SECONDS:
        return False
    _last_report_time = now

    fields = [
        {"name": "Reported by", "value": reporter_name or "(unknown)", "inline": False},
        {"name": "File", "value": info.get("file") or "(unknown)", "inline": False},
        {"name": "Current title", "value": info.get("current_title") or "(none)", "inline": True},
        {"name": "Current artist", "value": info.get("current_artist") or "(none)", "inline": True},
        {"name": "Suggested title", "value": info.get("detected_title") or "(none)", "inline": True},
        {"name": "Suggested artist", "value": info.get("detected_artist") or "(none)", "inline": True},
        {"name": "Online cover match", "value": info.get("cover_source") or "none", "inline": True},
        {"name": "Format", "value": info.get("format") or "?", "inline": True},
        {"name": "App version", "value": APP_VERSION, "inline": True},
    ]

    embed = {"title": "Track reported", "color": 0xE74C3C, "fields": fields}

    files = {}
    current_cover_bytes = info.get("current_cover_bytes") if info.get("has_cover") else None
    if current_cover_bytes:
        files["files[0]"] = ("current_cover.jpg", current_cover_bytes, "image/jpeg")
        embed["thumbnail"] = {"url": "attachment://current_cover.jpg"}

    found_cover_image = info.get("found_cover_image")
    if found_cover_image:
        files["files[1]"] = ("suggested_cover.jpg", found_cover_image, "image/jpeg")
        embed["image"] = {"url": "attachment://suggested_cover.jpg"}

    payload = {"embeds": [embed]}

    try:
        if files:
            response = requests.post(
                DISCORD_REPORT_WEBHOOK_URL, data={"payload_json": json.dumps(payload)}, files=files, timeout=timeout
            )
        else:
            response = requests.post(DISCORD_REPORT_WEBHOOK_URL, json=payload, timeout=timeout)
        return response.status_code in (200, 204)
    except Exception:
        return False


def send_new_install_notification(reporter_name=None, timeout=10):
    """
    Posts a one-time "new install" ping to the same Discord webhook as
    send_track_report, so the developer knows a new person/machine started
    using the app. Called once per Windows user account (see
    _check_new_install_notification_on_startup in interface.py, gated by a
    saved setting so it never fires twice on the same machine+account).
    Returns True on success, False on any failure (never raises).
    """
    embed = {
        "title": "New install",
        "color": 0x2ECC71,
        "fields": [
            {"name": "User", "value": reporter_name or "(unknown)", "inline": True},
            {"name": "App version", "value": APP_VERSION, "inline": True},
        ],
    }
    try:
        response = requests.post(DISCORD_REPORT_WEBHOOK_URL, json={"embeds": [embed]}, timeout=timeout)
        return response.status_code in (200, 204)
    except Exception:
        return False


# --- Saved UI settings (theme choice...) ---

SETTINGS_FILE = os.path.join(user_config_dir(), "settings.json")


def load_settings():
    """Returns the saved UI settings (e.g. theme choice) as a dict, or {} if none saved yet."""
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_setting(key, value):
    """Persists a single UI setting, keeping whatever else was already saved."""
    settings = load_settings()
    settings[key] = value
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception as error:
        print(f"  Could not save setting '{key}': {error}")


# --- Processing history log ---

HISTORY_FILE = os.path.join(user_config_dir(), "history.jsonl")


def log_history_entry(old_file, new_file, old_artist, old_title, new_artist, new_title,
                       cover_updated, converted, folder=None, old_cover_bytes=None):
    """
    Appends one line of JSON to HISTORY_FILE for a file that was actually
    processed (tags written and/or renamed), keeping a permanent record of
    what it used to be and what changed - independent of the music folder
    itself, which files get moved/deleted from over time.
    One file per line (JSON Lines) so appending never requires re-reading or
    re-writing the whole history.

    folder (the absolute path files were processed FROM) and old_cover_bytes
    (the cover as it was before this run, if any) are what make
    restore_history_entry() possible later - MUSIC_FOLDER itself isn't
    reliable for that since the user may since have scanned a different
    folder entirely.
    """
    entry = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "folder": folder,
        "old_file": old_file,
        "new_file": new_file,
        "old_artist": old_artist,
        "old_title": old_title,
        "new_artist": new_artist,
        "new_title": new_title,
        "cover_updated": cover_updated,
        "converted": converted,
        "old_cover_b64": base64.b64encode(old_cover_bytes).decode("ascii") if old_cover_bytes else None,
    }
    try:
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as error:
        print(f"  Could not write history entry: {error}")


def _find_file_by_name(folder, basename):
    """Walks folder looking for a file with the exact same name - a
    lightweight auto-locate attempt for restore_history_entry when the file
    isn't where it was originally processed (e.g. moved to a subfolder
    since). Bounded to the entry's own logged folder tree, not a full-drive
    search. Returns the first match's full path, or None."""
    if not folder or not os.path.isdir(folder):
        return None
    for root, _dirs, files in os.walk(folder):
        if basename in files:
            return os.path.join(root, basename)
    return None


def restore_history_entry(entry, log=safe_print, override_path=None):
    """
    Restores a file's artist/title/cover to what they were before a previous
    run changed them (a history.jsonl entry from load_history_entries()).

    Locates the file via the entry's own logged folder + its current
    (new_file) relative path - NOT the global MUSIC_FOLDER, since the user
    may have scanned a different folder since this entry was logged. If it's
    not there anymore, tries a bounded search of that same folder tree for a
    file with the same name (it may have just moved to a subfolder) before
    giving up. override_path (an absolute path) skips both of those and
    uses that location directly - for when the caller already asked the
    user to locate the file manually.

    The file itself keeps its current format/extension (a WAV->MP3
    conversion isn't reversible - the original file is gone), but is renamed
    to match the restored artist/title if both are known.

    Returns the file's new absolute path (unchanged if it wasn't renamed) -
    absolute rather than relative-to-folder, since override_path/the
    auto-locate search above can both put the file somewhere other than
    the originally logged folder. Raises FileNotFoundError if the file
    can't be located, or ValueError if this entry predates the "folder"
    field and can't be resolved at all.
    """
    if override_path:
        full_path = override_path
        folder = os.path.dirname(full_path)
        current_relative = os.path.basename(full_path)
    else:
        folder = entry.get("folder")
        if not folder:
            raise ValueError("This entry has no folder recorded (logged by an older version) - can't locate the file.")

        current_relative = entry.get("new_file")
        full_path = os.path.join(folder, current_relative)

        if not os.path.exists(full_path):
            found = _find_file_by_name(folder, os.path.basename(current_relative))
            if found:
                log(f"  Not at its logged location - found it at: '{found}'")
                full_path = found

    if not os.path.exists(full_path):
        raise FileNotFoundError(f"File not found: {full_path}")

    old_artist = entry.get("old_artist") or ""
    old_title = entry.get("old_title") or ""
    old_cover_b64 = entry.get("old_cover_b64")
    old_cover_bytes = base64.b64decode(old_cover_b64) if old_cover_b64 else None

    write_tags(
        full_path, old_artist, old_title, cover_image=old_cover_bytes,
        force_remove_if_missing=True,  # no old cover logged -> remove whatever cover is there now
        update_title=bool(old_title), update_artist=bool(old_artist), update_cover=True,
        log=log,
    )
    log(f"  Restored tags on: '{full_path}'")

    if old_artist and old_title:
        # Derived from full_path's actual current directory, not the
        # originally-logged one - matters when the file was found via
        # override_path or the auto-locate search above, since either can
        # put it somewhere other than "folder".
        actual_folder = os.path.dirname(full_path)
        extension = os.path.splitext(full_path)[1]
        new_base_name = sanitize_filename(build_display_name(old_artist, old_title)) + extension
        new_full_path = os.path.join(actual_folder, new_base_name)

        if new_full_path != full_path:
            os.rename(full_path, new_full_path)
            log(f"  Restored and renamed to: '{new_full_path}'")
            return new_full_path

    return full_path


def load_history_entries():
    """
    Returns every logged processing-history entry as a list of dicts, oldest
    first (empty list if HISTORY_FILE doesn't exist yet, or on any read
    error). A single malformed line (e.g. a partial write) is skipped
    rather than losing the rest of the history.
    """
    if not os.path.exists(HISTORY_FILE):
        return []

    entries = []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    continue
    except Exception as error:
        print(f"  Could not read history: {error}")
    return entries


def _history_entry_key(entry):
    """Entries logged from now on carry a real unique "id" (uuid4). Older
    entries (logged before that field existed) fall back to a composite key
    - timestamp alone isn't reliably unique on its own, since two files
    processed in the same run can log within the same microsecond."""
    entry_id = entry.get("id")
    if entry_id:
        return entry_id
    return (entry.get("timestamp"), entry.get("old_file"), entry.get("new_file"))


def delete_history_entries(entries_to_delete):
    """Removes specific entries from the processing history log, rewriting
    the file without them. Never touches the actual audio files - only
    the log."""
    if not os.path.exists(HISTORY_FILE):
        return
    keys_to_delete = {_history_entry_key(entry) for entry in entries_to_delete}
    remaining = [e for e in load_history_entries() if _history_entry_key(e) not in keys_to_delete]
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            for entry in remaining:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as error:
        print(f"  Could not delete history entries: {error}")


def mark_history_entries_restored(entries_to_mark):
    """Flags specific entries as "restored" (rewriting the file with that
    field set), so the history view can show they were later reverted -
    the log entry itself is otherwise left untouched. Does nothing if the
    history file doesn't exist."""
    if not os.path.exists(HISTORY_FILE):
        return
    keys_to_mark = {_history_entry_key(entry) for entry in entries_to_mark}
    updated = []
    for entry in load_history_entries():
        if _history_entry_key(entry) in keys_to_mark:
            entry = dict(entry, restored=True)
        updated.append(entry)
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            for entry in updated:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as error:
        print(f"  Could not mark history entries as restored: {error}")


# --- Runtime config (set by the UI at startup / per scan) ---

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

# Which cover sources are enabled (each set independently by the UI) - all
# three default to what always worked before Spotify existed (iTunes +
# SoundCloud on, Spotify off). Whichever are enabled are tried in a fixed
# priority order: iTunes, then Spotify, then SoundCloud - see
# _search_one_source() / scan_one_file().
USE_ITUNES = True
USE_SPOTIFY = False
USE_SOUNDCLOUD = True

# Whether non-MP3, non-WAV, non-AIFF files get converted to MP3 (320 kbps)
# automatically (set by the UI). WAV/AIFF are always tagged directly, on or
# off - they're the only non-MP3 formats mutagen can write ID3 tags/cover
# art to without converting first (see open_audio_file/write_tags) - so
# when this is off, only the other formats (FLAC, M4A, OGG, ...), which
# truly can't be tagged without converting, are skipped during scanning.
# Takes priority over AUTO_CONVERT_WAV_TO_AIFF below when both apply to a
# WAV file (see _resolve_conversion_target).
AUTO_CONVERT_MP3 = False

# Whether WAV files get converted to AIFF instead of being tagged as WAV
# directly (set by the UI, on by default). Purely about cover art
# compatibility, not sound quality (lossless PCM byte-order conversion,
# see convert_wav_to_aiff) or tag support (both are taggable directly via
# ID3) - some DJ software (confirmed: Rekordbox) doesn't read embedded
# artwork from WAV files at all, only from AIFF/MP3/etc. No effect on
# files that are already something other than WAV.
AUTO_CONVERT_WAV_TO_AIFF = True


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

def extract_audio_files(root_folder, log=safe_print):
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


def remove_empty_subfolders(root_folder, log=safe_print):
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

    Skips formats other than MP3/WAV/AIFF when AUTO_CONVERT_MP3 is off - those
    are the only formats mutagen can tag directly (see open_audio_file), so
    with auto-convert disabled there's nothing usable left to do with the
    rest. WAV/AIFF themselves are always included either way.
    """
    if not os.path.isdir(MUSIC_FOLDER):
        return []

    extensions = SUPPORTED_EXTENSIONS if AUTO_CONVERT_MP3 else (".mp3", ".wav", ".aiff")

    audio_files = []
    for current_folder, _, file_names in os.walk(MUSIC_FOLDER):
        for name in file_names:
            if name.lower().endswith(extensions):
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

def _search_one_source(source, artist, search_title, remix_qualified_title, spotify_token, soundcloud_token, log):
    """
    Tries a single cover source, using that provider's own established
    query strategy:
    - iTunes/Spotify: the plain (parens-stripped) title first, then a
      retry with the remix qualifier kept in the query if that misses and
      the two titles actually differ (e.g. a heavily-remixed song where
      the plain query ranks a different remix first).
    - SoundCloud: goes straight for the remix-qualified title, since
      remixes/edits live there more often than a plain search would find.
    Returns (match_result, source_label) - source_label is None when
    nothing matched.
    """
    if source == "itunes":
        match_result = search_cover_itunes(artist, search_title, log=log)
        if not match_result and remix_qualified_title != search_title:
            match_result = search_cover_itunes(artist, remix_qualified_title, log=log, allow_loose_remix_match=True)
        return match_result, ("iTunes" if match_result else None)

    if source == "spotify":
        match_result = search_cover_spotify(artist, search_title, spotify_token, log=log)
        if not match_result and remix_qualified_title != search_title:
            match_result = search_cover_spotify(artist, remix_qualified_title, spotify_token, log=log)
        return match_result, ("Spotify" if match_result else None)

    # source == "soundcloud"
    match_result = search_cover_soundcloud(artist, remix_qualified_title, soundcloud_token, log=log)
    return match_result, ("SoundCloud" if match_result else None)


def search_cover_manual(artist, title, soundcloud_token, spotify_token=None, log=safe_print):
    """
    Searches for a cover using the given artist/title directly, trying
    every enabled source in priority order and stopping at the first
    match - shared by scan_one_file() (filename/tag-derived artist/title)
    and the "fix Artist/Title and search again" flow after a scan finds no
    match at all (user-corrected artist/title).

    Returns (found_cover_image, cover_source, returned_artist,
    returned_title) - the last three are None if nothing matched.
    """
    if not artist or not title:
        return None, None, None, None

    search_title = strip_parentheses(title)
    has_parenthetical = search_title != title
    remix_qualified_title = strip_trailing_noise_words(title) if has_parenthetical else search_title

    for source, enabled in (
        ("itunes", USE_ITUNES),
        ("spotify", USE_SPOTIFY),
        ("soundcloud", USE_SOUNDCLOUD and not SOUNDCLOUD_RATE_LIMITED and not SOUNDCLOUD_UNAVAILABLE),
    ):
        if not enabled:
            continue
        match_result, cover_source = _search_one_source(
            source, artist, search_title, remix_qualified_title, spotify_token, soundcloud_token, log,
        )
        if match_result:
            found_cover_image, returned_artist, returned_title = match_result
            return found_cover_image, cover_source, returned_artist, returned_title

    return None, None, None, None


def search_cover_manual_with_tokens(artist, title, log=safe_print):
    """
    Same as search_cover_manual(), but also fetches the SoundCloud/Spotify
    tokens itself - for a single ad-hoc search (e.g. the "fix Artist/Title
    and search again" flow after a scan finds nothing) that doesn't go
    through scan_files()'s full per-run setup.
    """
    soundcloud_token = None
    if USE_SOUNDCLOUD and SOUNDCLOUD_CLIENT_ID and SOUNDCLOUD_CLIENT_SECRET:
        soundcloud_token = get_soundcloud_token(log=log)

    spotify_token = get_spotify_token(log=log) if USE_SPOTIFY else None

    return search_cover_manual(artist, title, soundcloud_token, spotify_token, log=log)


def _prepare_scan(file_name, log=safe_print, on_new_mention=None):
    """
    Local-only part of analyzing a file (no network): reads tags, resolves
    artist/title, and detects mentions. Returns a dict with everything
    needed to both run the cover search and build the final info dict -
    split out from scan_one_file() so scan_files() can prepare every file
    up front (cheap, sequential) and then search iTunes for all of them
    concurrently (see ITUNES_SCAN_MAX_WORKERS below).
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

    # Suggest every other parenthesized mention found in the CLEANED title
    # (i.e. what actually gets displayed), so already-handled mentions like
    # "By Fuvi Clan" don't also show up as a redundant separate suggestion.
    if on_new_mention and detected_title:
        for mention in detect_parenthetical_mentions(detected_title):
            on_new_mention(mention)

    search_title = remix_qualified_title = None
    if detected_artist and detected_title:
        search_title = strip_parentheses(detected_title)
        has_parenthetical = search_title != detected_title
        remix_qualified_title = strip_trailing_noise_words(detected_title) if has_parenthetical else search_title

    return {
        "file_name": file_name,
        "has_cover": has_cover,
        "current_artist": current_artist,
        "current_title": current_title,
        "current_cover_bytes": current_cover_bytes,
        "needs_conversion": needs_conversion,
        "detected_artist": detected_artist,
        "detected_title": detected_title,
        "tags_already_present": tags_already_present,
        "search_title": search_title,
        "remix_qualified_title": remix_qualified_title,
    }


def _finish_scan(prepared, match_result, cover_source, log=safe_print):
    """
    Builds the final info dict for a file, given the (match_result,
    cover_source) pair its cover search ended up with - shared by
    scan_one_file() and scan_files()'s parallel-iTunes path.
    """
    file_name = prepared["file_name"]
    detected_artist = prepared["detected_artist"]
    detected_title = prepared["detected_title"]
    found_cover_image = None

    if match_result:
        found_cover_image, returned_artist, returned_title = match_result

        # Only try to fix a swap on the FILENAME-derived guess - the file's
        # own existing tags are trusted as-is and never rewritten this way.
        if not prepared["tags_already_present"]:
            corrected_artist, corrected_title = fix_swapped_artist_title(
                detected_artist, detected_title, returned_artist, returned_title
            )
            if corrected_artist != detected_artist:
                log(
                    f"  Artist/title looked swapped -> corrected to "
                    f"'{corrected_artist} - {corrected_title}'"
                )
                detected_artist, detected_title = corrected_artist, corrected_title
    else:
        cover_source = None

    return {
        "file": file_name,
        "format": os.path.splitext(file_name)[1].lstrip(".").upper(),
        "detected_artist": detected_artist,
        "detected_title": detected_title,
        "current_artist": prepared["current_artist"],
        "current_title": prepared["current_title"],
        "has_cover": prepared["has_cover"],
        "mention_detected": contains_mention_to_remove(file_name),
        # WAV/AIFF can be tagged either way, so their default follows the
        # user's global choices (see _resolve_conversion_target) - other
        # non-MP3 formats have no such choice (can't be tagged without
        # converting at all), so they always default on.
        "convert": _resolve_conversion_target(file_name) is not None,
        # If the file's own tags are already complete, don't default to
        # overwriting them with a filename-derived guess that could be worse
        # (e.g. a truncated/garbled filename from some export tool).
        "apply_changes": bool(detected_title),
        "found_cover_image": found_cover_image,
        "cover_source": cover_source,
        "current_cover_bytes": prepared["current_cover_bytes"],
        "title_override": None,
        "artist_override": None,
        "processed": False,
        "final_path": None,
    }


def scan_one_file(file_name, soundcloud_token, spotify_token=None, log=safe_print, on_new_mention=None):
    """
    Analyzes a single file (path relative to MUSIC_FOLDER): current tags,
    info detected from the name, and online cover search.
    Returns the corresponding info dict.
    """
    prepared = _prepare_scan(file_name, log=log, on_new_mention=on_new_mention)

    match_result = None
    cover_source = None
    if prepared["detected_artist"] and prepared["search_title"]:
        found_cover_image, cover_source, returned_artist, returned_title = search_cover_manual(
            prepared["detected_artist"], prepared["detected_title"], soundcloud_token, spotify_token, log,
        )
        if found_cover_image:
            match_result = (found_cover_image, returned_artist, returned_title)

    return _finish_scan(prepared, match_result, cover_source, log)


SOUNDCLOUD_RATE_LIMITED = False  # set for the current run once a 429 is hit
SOUNDCLOUD_UNAVAILABLE = False  # set for the current run when no credentials are configured at all

# Bounded on purpose: iTunes' undocumented search endpoint already returns
# the occasional transient 403 under plain sequential use (see
# search_cover_itunes's own retry logic) - too much concurrency risks
# tripping that more often, not less.
ITUNES_SCAN_MAX_WORKERS = 4


def scan_files(file_list, on_file_scanned=None, log=safe_print, on_new_mention=None, on_rate_limited=None,
               should_cancel=None):
    """
    Scans ONLY the files in the given list (relative paths).
    Useful for an incremental scan (only reprocess new files).
    on_file_scanned(info) is called right after each file is analyzed,
    to allow a progressive display instead of waiting for the whole thing to finish.
    should_cancel() is checked before each file - if it returns True, the scan
    stops early and returns whatever was scanned so far.

    iTunes searches (if enabled) run concurrently across files, up to
    ITUNES_SCAN_MAX_WORKERS at once - iTunes needs no auth/shared token and
    every request is fully independent, unlike Spotify (shared token) and
    SoundCloud (shared rate-limit state), which stay sequential exactly as
    before.
    """
    global SOUNDCLOUD_RATE_LIMITED, SOUNDCLOUD_UNAVAILABLE
    SOUNDCLOUD_RATE_LIMITED = False
    SOUNDCLOUD_UNAVAILABLE = False

    if not file_list:
        return []

    if not USE_SOUNDCLOUD:
        # Disabled in Settings - don't even try to authenticate.
        log("  [SoundCloud] Disabled in Settings - skipping SoundCloud for this scan.")
        SOUNDCLOUD_UNAVAILABLE = True
        soundcloud_token = None
    elif not SOUNDCLOUD_CLIENT_ID or not SOUNDCLOUD_CLIENT_SECRET:
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

    # Only bother authenticating with Spotify if it's actually enabled for
    # this run - no point for a scan that'll never call it.
    spotify_token = get_spotify_token(log=log) if USE_SPOTIFY else None

    # Phase 1: prepare every file (local-only: tags, filename parsing) -
    # fast, so should_cancel is checked cheaply between each one.
    prepared_list = []
    for file_name in file_list:
        if should_cancel and should_cancel():
            log("  Scan cancelled.")
            return []
        prepared_list.append(_prepare_scan(file_name, log=log, on_new_mention=on_new_mention))

    # Phase 2: search iTunes for every file concurrently (bounded), if enabled.
    itunes_futures = {}
    executor = ThreadPoolExecutor(max_workers=ITUNES_SCAN_MAX_WORKERS) if USE_ITUNES else None
    if executor:
        for prepared in prepared_list:
            if prepared["detected_artist"] and prepared["search_title"]:
                itunes_futures[prepared["file_name"]] = executor.submit(
                    _search_one_source,
                    "itunes", prepared["detected_artist"], prepared["search_title"],
                    prepared["remix_qualified_title"], None, None, log,
                )

    # Phase 3: finish each file in its original order - reuse the iTunes
    # result from phase 2 if there is one, otherwise (or if it found
    # nothing) fall back to Spotify/SoundCloud sequentially, exactly as
    # before.
    results = []
    try:
        for prepared in prepared_list:
            if should_cancel and should_cancel():
                log("  Scan cancelled.")
                break

            file_name = prepared["file_name"]
            match_result = None
            cover_source = None

            future = itunes_futures.get(file_name)
            if future:
                match_result, cover_source = future.result()

            if not match_result and prepared["detected_artist"] and prepared["search_title"]:
                for source, enabled in (
                    ("spotify", USE_SPOTIFY),
                    ("soundcloud", USE_SOUNDCLOUD and not SOUNDCLOUD_RATE_LIMITED and not SOUNDCLOUD_UNAVAILABLE),
                ):
                    if not enabled:
                        continue
                    match_result, cover_source = _search_one_source(
                        source, prepared["detected_artist"], prepared["search_title"],
                        prepared["remix_qualified_title"], spotify_token, soundcloud_token, log,
                    )
                    if match_result:
                        break

            info = _finish_scan(prepared, match_result, cover_source, log)
            results.append(info)
            if on_file_scanned:
                on_file_scanned(info)
    finally:
        if executor:
            executor.shutdown(wait=False, cancel_futures=True)

    return results


# ============================================================================
# 8. COVER MATCH VALIDATION
# ============================================================================

def strip_generic_mix_suffix(text):
    """
    Removes a trailing GENERIC descriptor, in parentheses like "(Mixed)"/
    "(Extended Mix)" or dash-separated like "- Radio Edit", for comparison
    purposes only - a NAMED remix (e.g. "(DJ Name Remix)") is left untouched,
    since that's a real difference.
    """
    match = re.search(r"\s*\(([^)]*)\)\s*$", text)
    if match and match.group(1).strip().lower() in GENERIC_MIX_LABELS:
        return text[:match.start()].strip()
    match = re.search(r"\s+-\s+([^-]+)$", text)
    if match and match.group(1).strip().lower() in GENERIC_MIX_LABELS:
        return text[:match.start()].strip()
    return text


FEATURE_SUFFIX_RE = re.compile(r"\s*[\(\[](?:feat\.?|ft\.?|featuring)\s+([^)\]]*)[\)\]]\s*$", re.IGNORECASE)


def strip_feature_suffix(text):
    """
    Removes a trailing "(feat. X)" / "(ft. X)" / "[featuring X]" credit, for
    comparison purposes only - a store's listing often includes the featured
    artist in the title even when our own tags/filename don't, which
    shouldn't by itself count as a mismatch.
    """
    return FEATURE_SUFFIX_RE.sub("", text).strip()


def extract_feature_names(text):
    """
    Returns the artist name(s) inside a trailing "(feat. X)" / "(ft. X)" /
    "[featuring X]" credit (split the same way a multi-artist field would
    be), or an empty set if there isn't one. A store often credits the
    featured artist only in the title, not the artist field, while our own
    tags/filename list every artist together in the artist field - folding
    this into the returned artist set avoids treating that as a mismatch.
    """
    match = FEATURE_SUFFIX_RE.search(text)
    if not match:
        return set()
    return split_artist_names(match.group(1))


def strip_accents(text):
    """
    Folds accented characters to their base ASCII form for comparison
    purposes (e.g. "é" -> "e") - some taggers/download sources strip
    accents from filenames/tags for filesystem compatibility while a
    store's official listing keeps them (or vice versa), which would
    otherwise be treated as a completely different artist/title.
    """
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def exact_match(text_a, text_b):
    """Case/whitespace/accent-insensitive EXACT match (not the looser substring/word-based checks below)."""
    def normalize(text):
        return strip_accents(re.sub(r"\s+", " ", text.strip().lower()))
    return normalize(text_a) == normalize(text_b)


def strip_all_trailing_groups(text):
    """
    Repeatedly strips trailing "(...)"/"[...]" groups - e.g. a title with
    several stacked qualifiers like "Title (Subtitle) [feat. X] [Y Remix]" -
    returning (core_title, [group_1, group_2, ...]) with groups in the order
    they were stripped (rightmost/outermost first).
    """
    groups = []
    while True:
        match = re.search(r"\s*[\(\[]([^()\[\]]*)[\)\]]\s*$", text)
        if not match:
            break
        groups.append(match.group(1).strip())
        text = text[:match.start()]
    return text.strip(), groups


def loose_remix_match(expected_title, returned_title):
    """
    Fallback for a specific remix rejected by the strict exact-match check
    because the store's listing has extra bracket groups ours doesn't know
    about (e.g. a subtitle, or "feat. X" positioned before the remix
    bracket instead of at the very end, so strip_feature_suffix() can't
    reach it). Accepts it anyway if the core title matches and our specific
    remix qualifier is one of the store's bracket groups verbatim
    (case/whitespace-insensitive) - deliberately stricter than a generic
    fuzzy match, since this is only meant to recognize the SAME named
    remix, not just "some remix of the same song".
    """
    expected_core, expected_groups = strip_all_trailing_groups(expected_title)
    returned_core, returned_groups = strip_all_trailing_groups(returned_title)

    if not expected_groups or not exact_match(expected_core, returned_core):
        return False

    def normalize_group(text):
        return re.sub(r"\s+", " ", text.strip().lower())

    expected_set = {normalize_group(g) for g in expected_groups}
    returned_set = {normalize_group(g) for g in returned_groups}
    return bool(expected_set & returned_set)


def extract_feature_names_from_groups(groups):
    """
    Like extract_feature_names(), but scans a list of already-isolated
    bracket/paren group contents (e.g. from strip_all_trailing_groups())
    instead of requiring the "feat. X" credit to be at the very end of a
    string - a store can position it before other bracket groups.
    """
    names = set()
    for group in groups:
        match = re.match(r"(?:feat\.?|ft\.?|featuring)\s+(.*)", group.strip(), re.IGNORECASE)
        if match:
            names |= split_artist_names(match.group(1))
    return names


FORBIDDEN_FILENAME_CHARS_RE = re.compile(r'[\\/:*?"<>|_]')


def strip_sanitized_chars(text):
    """
    sanitize_filename() replaces every character forbidden in Windows
    filenames (\\/:*?"<>|) with '_' - so an artist whose real name has one
    of those (e.g. "BLOND:ISH") ends up as "BLOND_ISH" in the filename and
    often the tags too. Stripping all of those chars (including '_', since
    it's what they all collapse to) before comparing artist names avoids
    rejecting an otherwise-correct match over a single sanitized character.
    """
    return FORBIDDEN_FILENAME_CHARS_RE.sub("", text)


def split_artist_names(text):
    """Splits a multi-artist string on any common separator (,  &  x  X  vs  feat.  ft.  and)."""
    parts = re.split(r"\s*(?:,|&|/|\bx\b|\bvs\b|\bfeat\.?\b|\bft\.?\b|\band\b)\s*", text, flags=re.IGNORECASE)
    return {strip_accents(strip_sanitized_chars(p.strip().lower())) for p in parts if p.strip()}


def artist_sets_match(expected_artist, returned_artist, returned_title=""):
    """
    Exact match for a list of artists, ignoring order and separator style
    (e.g. "A, B, C" vs "C & A x B" are considered the same set of artists).
    If returned_title is given, a featured artist credited only there (e.g.
    "Title (feat. X)") is folded into the returned artist set too - some
    stores split primary vs. featured artist across the two fields, while
    our own tags/filename usually list everyone in the artist field.
    """
    returned_names = split_artist_names(returned_artist) | extract_feature_names(returned_title)
    return split_artist_names(expected_artist) == returned_names


def artist_names_match(expected_artist, returned_artist):
    """
    Loosely checks whether the artist returned by a search actually corresponds
    to the expected one, to reject unrelated tracks that only match by title
    (e.g. iTunes free-text search returning a same-titled song by another artist).
    """
    if not returned_artist:
        return False

    expected_lower = strip_accents(strip_sanitized_chars(expected_artist.lower()))
    returned_lower = strip_accents(strip_sanitized_chars(returned_artist.lower()))
    returned_compact = re.sub(r"\s+", "", returned_lower)

    # Split on common multi-artist separators (filenames often list several artists)
    fragments = re.split(r"[,&]| feat\.?| ft\.?| x ", expected_lower)

    for fragment in fragments:
        fragment = fragment.strip()
        if not fragment:
            continue
        if fragment in returned_lower or returned_lower in fragment:
            return True
        # Some platforms (e.g. SoundCloud usernames/handles) drop spaces
        # entirely - "Spicy Market" becomes "SpicyMarket" - so also compare
        # with whitespace stripped from both sides before giving up.
        fragment_compact = re.sub(r"\s+", "", fragment)
        if fragment_compact and (fragment_compact in returned_compact or returned_compact in fragment_compact):
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

def build_search_query(artist, title):
    """
    Builds a search term from artist+title, replacing punctuation (commas,
    parentheses/brackets) with spaces instead of leaving it in the query as
    literal characters - both iTunes' and Spotify's relevance ranking seem
    to penalize that punctuation. A comma-separated multi-artist string or
    a parenthesized remix name can bury the exact version we want under a
    heap of same-song alternates, even when it's genuinely in the results.
    Shared by search_cover_itunes() and search_cover_spotify().
    """
    combined = f"{artist} {title}"
    cleaned = re.sub(r"[,()\[\]]", " ", combined)
    return re.sub(r"\s+", " ", cleaned).strip()


def search_cover_itunes(artist, title, log=safe_print, max_retries=2, allow_loose_remix_match=False):
    """
    Retries on HTTP 429 (rate limited) with a short backoff before giving up.
    Unlike SoundCloud, iTunes needs no token to reuse - a plain retry is
    enough to ride out a short burst instead of silently returning no cover
    for a track that would otherwise have matched.

    Checks up to 10 candidates, not just the top one - iTunes' relevance
    ranking doesn't always put the exact version we want first (e.g. a song
    with a dozen different official remixes can return a different remix as
    the #1 result, even though the one we're looking for is also there).

    allow_loose_remix_match: when the strict checks above reject every
    candidate, also accepts one whose title matches via loose_remix_match()
    (same core title + our exact remix qualifier present, tolerating extra
    bracket groups like a subtitle) - meant to be turned on only for a
    remix-qualified retry, not the default search, since it's a narrower
    but still real risk of a false positive.
    """
    try:
        for attempt in range(max_retries + 1):
            response = requests.get(
                "https://itunes.apple.com/search",
                # Without an explicit country, the API defaults to the US store,
                # where a lot of French content (esp. explicit-tagged rap) simply
                # isn't licensed and returns zero results even though it's on the
                # French store.
                params={"term": build_search_query(artist, title), "entity": "song", "limit": 10, "country": "FR"},
                timeout=10,
            )

            # 403 is usually a transient rate-limit/bot-detection block too
            # (not a real "access denied") - a plain retry alone, with no
            # extra wait, has been observed to succeed immediately after.
            if response.status_code == 429 and attempt < max_retries:
                wait_seconds = 2 * (attempt + 1)
                log(f"  [iTunes] Rate limited, retrying in {wait_seconds}s...")
                time.sleep(wait_seconds)
                continue

            if response.status_code == 403 and attempt < max_retries:
                log("  [iTunes] Got HTTP 403 (likely transient), retrying...")
                continue

            break

        if response.status_code != 200:
            log(f"  [iTunes] Search failed: HTTP {response.status_code} - {response.text[:300]}")
            return None

        results = response.json().get("results", [])
        if not results:
            log(f"  [iTunes] No result at all for '{artist} - {title}'")
            return None

        title_normalized = strip_feature_suffix(strip_generic_mix_suffix(title))

        for result in results:
            # iTunes sometimes returns accented text in NFD form (e.g. "a" + a
            # combining accent, instead of the precomposed "à"), which looks
            # identical when printed but breaks both string comparison AND
            # printing on a Windows console (cp1252 can't encode combining
            # accents on their own). Normalize to NFC to match how tags/filenames
            # are represented.
            returned_artist = unicodedata.normalize("NFC", result.get("artistName", ""))
            returned_title = unicodedata.normalize("NFC", result.get("trackName", ""))
            returned_title_normalized = strip_feature_suffix(strip_generic_mix_suffix(returned_title))

            artist_ok = artist_sets_match(artist, returned_artist, returned_title) and exact_match(title_normalized, returned_title_normalized)
            swapped_ok = exact_match(title, returned_artist) and artist_sets_match(artist, returned_title_normalized)

            loose_ok = False
            if not (artist_ok or swapped_ok) and allow_loose_remix_match and loose_remix_match(title, returned_title):
                _, returned_groups = strip_all_trailing_groups(returned_title)
                returned_artist_set = split_artist_names(returned_artist) | extract_feature_names_from_groups(returned_groups)
                loose_ok = split_artist_names(artist) <= returned_artist_set

            if not (artist_ok or swapped_ok or loose_ok):
                continue

            cover_url = result.get("artworkUrl100")
            if not cover_url:
                log(f"  [iTunes] Match found for '{artist} - {title}' but it has no artwork URL.")
                continue

            cover_url_hd = cover_url.replace("100x100", "600x600")
            image_response = requests.get(cover_url_hd, timeout=10)

            if image_response.status_code == 200:
                return image_response.content, returned_artist, returned_title

            log(f"  [iTunes] Image download failed (HTTP {image_response.status_code}) for '{artist} - {title}'")

        top_result = results[0]
        log(
            f"  [iTunes] Match rejected (not an exact match among {len(results)} candidate(s)): "
            f"expected '{artist} - {title}', got '{unicodedata.normalize('NFC', top_result.get('artistName', ''))} - "
            f"{unicodedata.normalize('NFC', top_result.get('trackName', ''))}'"
        )
        return None

    except Exception as error:
        log(f"  [iTunes] Error while searching for cover: {error}")
        return None


# ============================================================================
# 10. COVER SEARCH - SOUNDCLOUD
# ============================================================================

_cached_soundcloud_token = None
_cached_token_expiry = 0  # Unix timestamp


def invalidate_soundcloud_token():
    """Forces the next get_soundcloud_token() call to authenticate again
    instead of reusing the cached token - e.g. after the credentials change."""
    global _cached_soundcloud_token, _cached_token_expiry
    _cached_soundcloud_token = None
    _cached_token_expiry = 0


def get_soundcloud_token(log=safe_print, on_rate_limited=None):
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


def search_cover_soundcloud(artist, title, token, log=safe_print):
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
# 11. COVER SEARCH - SPOTIFY
# ============================================================================

_cached_spotify_token = None
_cached_spotify_token_expiry = 0  # Unix timestamp


def invalidate_spotify_token():
    """Forces the next get_spotify_token() call to authenticate again
    instead of reusing the cached token - e.g. after the credentials change."""
    global _cached_spotify_token, _cached_spotify_token_expiry
    _cached_spotify_token = None
    _cached_spotify_token_expiry = 0


def get_spotify_token(log=safe_print):
    global _cached_spotify_token, _cached_spotify_token_expiry

    # Reuse the cached token if it's still valid (with a 60s safety margin)
    if _cached_spotify_token and time.time() < _cached_spotify_token_expiry - 60:
        return _cached_spotify_token

    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        log("  [Spotify] No credentials found (spotifyClientID.txt / spotifyClientSecret.txt missing or empty).")
        return None

    try:
        credentials = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        response = requests.post(
            "https://accounts.spotify.com/api/token",
            headers={
                "Authorization": f"Basic {encoded_credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
            timeout=10,
        )

        if response.status_code == 200:
            payload = response.json()
            _cached_spotify_token = payload.get("access_token")
            _cached_spotify_token_expiry = time.time() + payload.get("expires_in", 3600)
            return _cached_spotify_token

        log(f"  [Spotify] Authentication error: HTTP {response.status_code} - {response.text[:300]}")
        return None

    except Exception as error:
        log(f"  [Spotify] Error during authentication: {error}")
        return None


def search_cover_spotify(artist, title, token, log=safe_print):
    """
    Checks up to 10 candidates (not just the top one), mirroring
    search_cover_itunes() - Spotify's search ranking can also bury the
    exact version we want under alternates for a heavily-remixed song.
    """
    if not token:
        return None

    try:
        response = requests.get(
            "https://api.spotify.com/v1/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": build_search_query(artist, title), "type": "track", "limit": 10},
            timeout=10,
        )

        if response.status_code != 200:
            log(f"  [Spotify] Search failed: HTTP {response.status_code} - {response.text[:300]}")
            return None

        results = response.json().get("tracks", {}).get("items", [])
        if not results:
            log(f"  [Spotify] No result at all for '{artist} - {title}'")
            return None

        title_normalized = strip_feature_suffix(strip_generic_mix_suffix(title))

        for result in results:
            returned_artist = unicodedata.normalize(
                "NFC", ", ".join(a.get("name", "") for a in result.get("artists", []))
            )
            returned_title = unicodedata.normalize("NFC", result.get("name", ""))
            returned_title_normalized = strip_feature_suffix(strip_generic_mix_suffix(returned_title))

            artist_ok = (
                artist_sets_match(artist, returned_artist, returned_title)
                and exact_match(title_normalized, returned_title_normalized)
            )
            swapped_ok = exact_match(title, returned_artist) and artist_sets_match(artist, returned_title_normalized)

            if not (artist_ok or swapped_ok):
                continue

            images = result.get("album", {}).get("images", [])
            if not images:
                log(f"  [Spotify] Match found for '{artist} - {title}' but it has no artwork.")
                continue

            # Spotify lists images largest-first already; the first one is
            # typically 640x640, more than enough for an embedded cover.
            cover_url = images[0].get("url")
            image_response = requests.get(cover_url, timeout=10)

            if image_response.status_code == 200:
                return image_response.content, returned_artist, returned_title

            log(f"  [Spotify] Image download failed (HTTP {image_response.status_code}) for '{artist} - {title}'")

        top_result = results[0]
        top_artist = unicodedata.normalize("NFC", ", ".join(a.get("name", "") for a in top_result.get("artists", [])))
        top_title = unicodedata.normalize("NFC", top_result.get("name", ""))
        log(
            f"  [Spotify] Match rejected (not an exact match among {len(results)} candidate(s)): "
            f"expected '{artist} - {title}', got '{top_artist} - {top_title}'"
        )
        return None

    except Exception as error:
        log(f"  [Spotify] Error while searching for cover: {error}")
        return None


# ============================================================================
# 12. FORMAT CONVERSION
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


def convert_wav_to_aiff(source_path):
    """
    Converts a WAV file to AIFF using FFmpeg - purely a lossless PCM
    byte-order swap (little-endian -> big-endian), not a re-encode, so
    there's no quality loss. Exists only for cover-art compatibility with
    software that doesn't read embedded artwork from WAV (confirmed:
    Rekordbox) but does from AIFF. Removes the original file on success.
    """
    aiff_path = os.path.splitext(source_path)[0] + ".aiff"
    try:
        result = subprocess.run(
            [find_ffmpeg(), "-i", source_path, "-y", aiff_path],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            print(f"  FFmpeg error during conversion: {result.stderr[-300:]}")
            return None

        os.remove(source_path)
        return aiff_path

    except FileNotFoundError:
        print("  FFmpeg was not found. Check that it's installed and in the PATH.")
        return None
    except Exception as error:
        print(f"  Error during conversion: {error}")
        return None


def _resolve_conversion_target(file_name):
    """
    Decides what a non-MP3 file should be converted to, given the current
    settings - shared by _finish_scan() (the per-row "convert" default) and
    process_files() (which converter to actually run). Returns "mp3",
    "aiff", or None (stays in its current format, tagged directly - only
    possible for WAV/AIFF, the two formats open_audio_file/write_tags
    support without converting first).

    AUTO_CONVERT_MP3 always wins when it's on, for any format, including
    WAV - it's the broader, more deliberate setting. AUTO_CONVERT_WAV_TO_AIFF
    only ever applies to WAV specifically, and only when AUTO_CONVERT_MP3
    is off.
    """
    if file_name.lower().endswith(".mp3"):
        return None
    if AUTO_CONVERT_MP3:
        return "mp3"
    if file_name.lower().endswith(".wav") and AUTO_CONVERT_WAV_TO_AIFF:
        return "aiff"
    return None


# ============================================================================
# 13. TAG WRITING
# ============================================================================

def open_audio_file(file_path):
    if file_path.lower().endswith(".mp3"):
        audio = MP3(file_path)
    elif file_path.lower().endswith(".wav"):
        audio = WAVE(file_path)
    elif file_path.lower().endswith((".aiff", ".aif")):
        audio = AIFF(file_path)
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


def effective_cover_bytes(info):
    """
    Mirrors write_tags()'s cover logic to predict what the cover WILL be after
    Apply, for UI previews (thumbnail, zoom dialog) - so they never show "no
    cover" for a row that will actually keep its existing one untouched.
    """
    if not info.get("apply_changes", True):
        return info.get("current_cover_bytes")
    if info.get("found_cover_image"):
        return info["found_cover_image"]
    if detect_fuviclan_mention(info.get("file", "")):
        return None
    return info.get("current_cover_bytes")


def _write_wav_riff_info(file_path, artist, title, update_artist, update_title, log=safe_print):
    """
    Writes title/artist into a WAV's RIFF "LIST INFO" chunk (INAM/IART) via
    FFmpeg, alongside the ID3 tags write_tags() already writes - ID3-in-WAV
    isn't read by Windows Explorer or several DJ tools, which only look at
    RIFF INFO for WAV metadata (see read_current_info's own RIFF INFO
    fallback for the read side of this).

    Best-effort: mutagen has no RIFF INFO support at all, so this shells out
    to the bundled FFmpeg instead of hand-rolling raw chunk-editing binary
    code. Silently does nothing if FFmpeg isn't available - the ID3 tags
    write_tags() writes right after this still succeed either way.

    Must run BEFORE the ID3 write, never after: FFmpeg remuxes the whole
    file and drops any existing "id3 " chunk it doesn't understand, which
    would silently wipe out title/artist/cover again if this ran second.
    """
    temp_path = file_path + ".riffinfo_tmp.wav"
    command = [find_ffmpeg(), "-y", "-nostdin", "-i", file_path]
    if update_title:
        command += ["-metadata", f"title={title}"]
    if update_artist:
        command += ["-metadata", f"artist={artist}"]
    command += ["-c", "copy", temp_path]

    try:
        result = subprocess.run(
            command, capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            log(f"  Could not write WAV RIFF INFO tags: {result.stderr[-300:]}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return
        os.replace(temp_path, file_path)
    except FileNotFoundError:
        log("  FFmpeg was not found - WAV files will only get ID3 tags, "
            "not the RIFF INFO tags Explorer/some DJ tools read instead.")
    except Exception as error:
        log(f"  Could not write WAV RIFF INFO tags: {error}")
        if os.path.exists(temp_path):
            os.remove(temp_path)


def write_tags(file_path, artist, title, cover_image, force_remove_if_missing,
                update_title=True, update_artist=True, update_cover=True, log=safe_print):
    """
    Writes the chosen tags:
    - update_title / update_artist: True to write, False to leave as-is
    - update_cover: True to apply the cover logic (replace/remove/keep),
      False to leave the cover untouched entirely
    """
    if file_path.lower().endswith(".wav") and (update_title or update_artist):
        _write_wav_riff_info(file_path, artist, title, update_artist, update_title, log=log)

    audio = open_audio_file(file_path)
    tags = audio.tags

    if update_title:
        tags.setall("TIT2", [TIT2(encoding=3, text=[title])])
    if update_artist:
        tags.setall("TPE1", [TPE1(encoding=3, text=[artist])])

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
# 14. PROCESSING (APPLY)
# ============================================================================

def process_files(plan, log=safe_print, on_progress=None, on_file_processed=None, should_cancel=None):
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
        converted_this_file = False

        target_format = _resolve_conversion_target(file_name) if info.get("convert") else None
        if target_format:
            source_extension = os.path.splitext(file_name)[1].lstrip(".").upper()
            if target_format == "mp3":
                log(f"  Converting .{source_extension.lower()} -> .mp3 (320 kbps)...")
                new_path = convert_to_mp3(full_path)
            else:
                log(f"  Converting .{source_extension.lower()} -> .aiff...")
                new_path = convert_wav_to_aiff(full_path)

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
            converted_this_file = True
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
            log=log,
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

        # Only log a history entry when tags actually changed (apply_changes
        # was True) - an unchecked row still reaches this point (e.g. it
        # just needed converting), but old==new for it, so logging it would
        # just clutter the history with entries there's nothing to restore.
        if update_title:
            log_history_entry(
                old_file=info["file"],
                new_file=file_name,
                old_artist=info.get("current_artist"),
                old_title=info.get("current_title"),
                new_artist=artist,
                new_title=title,
                cover_updated=bool(update_cover and (cover_image or force_remove_if_missing)),
                converted=converted_this_file,
                folder=os.path.abspath(MUSIC_FOLDER) if MUSIC_FOLDER else None,
                old_cover_bytes=info.get("current_cover_bytes") if info.get("has_cover") else None,
            )

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


def process_folder(log=safe_print, on_progress=None):
    """Simple version: scans then processes the whole folder with default settings."""
    plan = scan_files(list_audio_files())
    process_files(plan, log=log, on_progress=on_progress)


def main():
    process_folder(log=safe_print)


if __name__ == "__main__":
    main()
