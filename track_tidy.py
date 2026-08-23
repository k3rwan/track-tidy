"""
Organizes tags (Artist/Title/Cover) for audio files based on the filename,
and fetches a cover online from whichever of iTunes/SoundCloud are enabled
(each independently, in that priority order). Any format other than MP3
(WAV, FLAC, AAC, M4A, OGG, WMA, AIFF, OPUS...) is converted to MP3
(320 kbps) before tagging.

Expected filename format: "Artist - Title.ext"

Contents (in the order they appear below):
    1. Configuration & credentials       - path helpers (app_base_dir,
                                            user_config_dir); SoundCloud
                                            credentials; USE_ITUNES/
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
    7. Scanning (read-only)               - scan_files
    8. Cover match validation             - artist_names_match, title_words_overlap,
                                            fix_swapped_artist_title
    9. Cover search - iTunes              - search_cover_itunes
    10. Cover search - SoundCloud         - get_soundcloud_token, search_cover_soundcloud
    11. Format conversion                 - find_ffmpeg, convert_to_mp3
    12. Tag writing                       - open_audio_file, write_tags, fix_title_artist
    13. Processing (Apply)                - process_files, process_folder, main
"""

import os
import io
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
import threading
import unicodedata
from datetime import datetime, timezone
import requests
import keyring
import platformdirs
import acoustid
from PIL import Image
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


# --- SoundCloud credentials (via the OS's native credential store -
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
    specific older version asks you to re-enter SoundCloud credentials
    once; upgrading from anything older (plaintext) or newer (already
    keyring-based) is seamless."""
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


def load_default_credentials():
    """
    The shared default app credentials (SoundCloud, Spotify, Discord
    webhook, AcoustID) used to be embedded directly in this file (base64,
    then AES-256-GCM, then - for AcoustID specifically, since it was never
    treated as sensitive - a bare literal) - fine while the compiled
    binary was the only thing
    anyone could get, since extracting them needed actual reverse
    engineering. Now that this source is public, an embedded value is
    worthless the moment it's committed - no amount of encryption helps
    when the decrypting code sits right next to it in the same public
    repo. Real secrets now live ONLY in "default_credentials.json"
    (gitignored, never committed) next to the app - present on Kevin's
    own build machine and bundled into the installer he ships (see
    installer.iss/build_mac.sh), but simply absent from this repo and
    from anyone else's build. Building from source without that file
    still works - the app just has no shared defaults, same as if a user
    hasn't configured their own credentials.
    """
    path = os.path.join(app_base_dir(), "default_credentials.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as error:
        print(f"  Could not read default_credentials.json: {error}")
        return {}


_default_credentials = load_default_credentials()

# Shared default app credentials so most users don't have to register
# their own SoundCloud app - Kevin's own, used as a fallback whenever a
# user hasn't saved their own credentials in Settings. Unlike
# ACOUSTID_API_KEY, distributing an app's client secret like this is
# against SoundCloud's own developer terms - accepted risk (confirmed
# with Kevin): if it's ever extracted and abused, SoundCloud could
# suspend the app credentials entirely, breaking this for every user at
# once, not just the abuser. Watch for SoundCloud auth suddenly failing
# for multiple users if that ever happens.
SOUNDCLOUD_DEFAULT_CLIENT_ID = _default_credentials.get("soundcloud_client_id", "")
SOUNDCLOUD_DEFAULT_CLIENT_SECRET = _default_credentials.get("soundcloud_client_secret", "")

SOUNDCLOUD_CLIENT_ID = read_credential(CLIENT_ID_KEY) or SOUNDCLOUD_DEFAULT_CLIENT_ID
SOUNDCLOUD_CLIENT_SECRET = read_credential(CLIENT_SECRET_KEY) or SOUNDCLOUD_DEFAULT_CLIENT_SECRET

# Same reasoning/risk as SOUNDCLOUD_DEFAULT_CLIENT_ID/_SECRET above, this
# time against Spotify's developer terms - accepted risk (confirmed with
# Kevin). Spotify is only ever tried as an absolute last resort (after
# iTunes, SoundCloud, AND the AcoustID-corrected retry of both have all
# come up empty - see the end of scan_files()), not a normal priority
# source, so this should get hit rarely.
SPOTIFY_DEFAULT_CLIENT_ID = _default_credentials.get("spotify_client_id", "")
SPOTIFY_DEFAULT_CLIENT_SECRET = _default_credentials.get("spotify_client_secret", "")

SPOTIFY_CLIENT_ID = read_credential(SPOTIFY_CLIENT_ID_KEY) or SPOTIFY_DEFAULT_CLIENT_ID
SPOTIFY_CLIENT_SECRET = read_credential(SPOTIFY_CLIENT_SECRET_KEY) or SPOTIFY_DEFAULT_CLIENT_SECRET

# AcoustID API keys are meant to be per-APPLICATION, one key shared by every
# user of that app - unlike SoundCloud, which needs each user's own app
# credentials, so this one was never something every user has to
# configure. It also carries no real abuse risk beyond the free-tier rate
# limit being shared (unlike SOUNDCLOUD/SPOTIFY_DEFAULT_CLIENT_SECRET
# above, leaking it doesn't violate any ToS or risk a suspension) - but it
# still doesn't need to sit in the public source as a bare literal when
# the same default_credentials.json mechanism already exists for exactly
# this. Building from source without that file just means no AcoustID
# fallback, same as an unconfigured user.
ACOUSTID_API_KEY = _default_credentials.get("acoustid_api_key", "")


def load_id_txt_credentials(filename="id.txt"):
    """
    Convenience file for quick offline testing: "id.txt" next to the app,
    with the Client ID on line 1 and the Client Secret on line 2.
    Takes priority over the normal saved credentials when present.
    """
    path = os.path.join(app_base_dir(), filename)
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines()]
        client_id = lines[0] if len(lines) > 0 and lines[0] else None
        client_secret = lines[1] if len(lines) > 1 and lines[1] else None
        return client_id, client_secret
    except Exception as error:
        print(f"  Could not read {filename}: {error}")
        return None, None


_id_txt_client_id, _id_txt_client_secret = load_id_txt_credentials()
if _id_txt_client_id and _id_txt_client_secret:
    SOUNDCLOUD_CLIENT_ID = _id_txt_client_id
    SOUNDCLOUD_CLIENT_SECRET = _id_txt_client_secret

# Second dev-only convenience pair ("id_2.txt", same format as id.txt) - a
# separate SoundCloud app registration used ONLY to ride out SoundCloud's
# token rate limit (50/12h per app) while testing locally, by retrying with
# a different app's credentials right after a 429 instead of waiting out the
# cooldown. Never embedded/shipped - both files are gitignored, and normal
# users never have either one, so SOUNDCLOUD_FALLBACK_CLIENT_ID/_SECRET stay
# None for everyone but whoever drops these files in next to the app.
SOUNDCLOUD_FALLBACK_CLIENT_ID, SOUNDCLOUD_FALLBACK_CLIENT_SECRET = load_id_txt_credentials("id_2.txt")


# --- Internet connectivity check ---

def check_internet_connection(timeout=2.5):
    """
    Quick, dependency-free connectivity check - opens a raw TCP connection
    to a well-known, always-up host (Google's public DNS) rather than doing
    a full HTTP request, so it stays fast and doesn't depend on any of the
    services this app actually talks to (iTunes/SoundCloud/GitHub) being
    reachable specifically.
    """
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=timeout).close()
        return True
    except OSError:
        return False


def check_restrictive_firewall(timeout=5):
    """
    Detects a firewall/network policy that blocks iTunes' and/or Spotify's
    own domains specifically while general internet access still works -
    the scenario suspected behind a real report where SoundCloud kept
    finding (unreliable) covers while iTunes and Spotify, which both
    actually had the correct release, seemingly never got a chance. Only
    meaningful once check_internet_connection() has already confirmed
    basic connectivity - call that first and skip this otherwise (a
    blocked domain and "no internet at all" would look identical here).

    Returns a list of blocked source names ("iTunes"/"Spotify") - empty if
    both are reachable. Never raises.
    """
    blocked = []
    for name, url in (("iTunes", "https://itunes.apple.com"), ("Spotify", "https://accounts.spotify.com")):
        try:
            requests.head(url, timeout=timeout)
        except requests.exceptions.RequestException:
            blocked.append(name)
    return blocked


# --- App version & update check ---

# Single source of truth for the app's version - shown in the GUI and used to
# check for updates. Bump this (and installer.iss's MyAppVersion) on release.
APP_VERSION = "0.25"
GITHUB_REPO = "k3rwan/track-tidy"  # public since 2026-08-21 - releases and source now live together


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

# A Discord webhook URL is a bearer token with no other auth - see
# load_default_credentials()'s docstring for why this comes from a local,
# gitignored file instead of being embedded here now that the source is
# public. send_track_report/send_new_install_notification/
# send_scan_complete_notification all no-op harmlessly (see their own
# early-return on a falsy URL) when that file isn't present.
DISCORD_REPORT_WEBHOOK_URL = _default_credentials.get("discord_webhook_url", "")

# Same "not embedded in public source" reasoning as the webhook URL above,
# but a bot token is more sensitive still (read access to the whole
# channel's history, not just permission to post) - see
# count_unique_discord_users(). The channel ID itself isn't sensitive (just
# a numeric identifier, useless without the token), so it's a plain
# constant rather than another default_credentials.json entry.
DISCORD_BOT_TOKEN = _default_credentials.get("discord_bot_token", "")
DISCORD_LOG_CHANNEL_ID = "1536761049410306201"

MAX_DISCORD_HISTORY_PAGES = 50

# "User" values in the channel history that are test/CI artifacts, not a
# real person who ever ran the app - excluded from the unique-user count
# specifically (unlike DISCORD_NOTIFICATION_EXCLUDED_USERS, which gates
# whether a notification gets SENT at all, this only affects what already-
# sent history counts as "a real user"). "runner" is GitHub Actions' own
# username on every CI test run; "test-install-verification" was an
# explicit manual test string, not an install by anyone.
DISCORD_UNIQUE_USER_COUNT_EXCLUDED = {"runner", "test-install-verification"}

# Where count_unique_discord_users() remembers how far it's already read -
# see its own docstring for why this only meaningfully helps repeat calls
# on THE SAME machine (mainly Kevin's own dev/testing use), not the
# population of real users overall (each of whom calls this only a
# handful of times in their install's whole lifetime).
DISCORD_USER_COUNT_CACHE_SETTINGS_KEY = "discord_user_count_cache"


def count_unique_discord_users(log=safe_print):
    """
    Returns the set of every distinct "User" embed-field value ever posted
    in the Discord report channel (lowercased) - every notification
    function in this module (send_track_report,
    send_new_install_notification, send_scan_complete_notification,
    send_no_cover_report, send_rate_limit_report) includes that field, so
    this naturally counts a unique person the first time ANY of them fired
    for that person, not just "New install" specifically.

    Incremental: remembers the newest message ID it's already accounted
    for (see DISCORD_USER_COUNT_CACHE_SETTINGS_KEY) and only walks
    (paginated, 100 at a time, newest first) as far back as that ID before
    stopping, instead of re-reading the entire channel every time - a full
    read only happens the first time this is ever called (empty cache).
    Bounded to MAX_DISCORD_HISTORY_PAGES pages either way, so a runaway
    pagination loop can't hang a "New install" notification or hammer
    Discord's API.

    Returns None on any failure (no bot token configured, network error,
    Discord API error/rate limit) rather than an empty set, so a caller can
    tell "couldn't determine this" apart from "genuinely nobody yet" and
    skip showing a count instead of showing a wrong one - the cache is only
    updated on a fully successful read, never on a partial/failed one.
    """
    if not DISCORD_BOT_TOKEN or not DISCORD_LOG_CHANNEL_ID:
        return None

    cache = load_settings().get(DISCORD_USER_COUNT_CACHE_SETTINGS_KEY) or {}
    cached_last_id = cache.get("last_message_id")
    usernames = set(cache.get("usernames") or [])

    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    before = None
    newest_id_seen = None

    try:
        for _page in range(MAX_DISCORD_HISTORY_PAGES):
            params = {"limit": 100}
            if before:
                params["before"] = before

            response = requests.get(
                f"https://discord.com/api/v10/channels/{DISCORD_LOG_CHANNEL_ID}/messages",
                headers=headers, params=params, timeout=10,
            )
            if response.status_code != 200:
                log(f"  [Discord] Could not read channel history: HTTP {response.status_code}")
                return None

            messages = response.json()
            if not messages:
                break

            if newest_id_seen is None:
                newest_id_seen = messages[0].get("id")

            reached_cache_boundary = False
            for message in messages:
                message_id = message.get("id")
                if cached_last_id and message_id and int(message_id) <= int(cached_last_id):
                    reached_cache_boundary = True
                    break
                for embed in message.get("embeds", []):
                    for field in embed.get("fields", []):
                        if field.get("name") == "User":
                            value = (field.get("value") or "").strip().lower()
                            if value and value != "(unknown)" and value not in DISCORD_UNIQUE_USER_COUNT_EXCLUDED:
                                usernames.add(value)

            if reached_cache_boundary:
                break

            before = messages[-1].get("id")
            if len(messages) < 100 or not before:
                break

        if newest_id_seen:
            save_setting(DISCORD_USER_COUNT_CACHE_SETTINGS_KEY, {
                "last_message_id": newest_id_seen,
                "usernames": sorted(usernames),
            })

        return usernames

    except Exception as error:
        log(f"  [Discord] Error while reading channel history: {error}")
        return None


def send_track_report(info, reporter_name=None, timeout=10):
    """
    Posts this track's info to a Discord webhook, so the developer gets a
    notification for tracks users flag as wrong/problematic (e.g. no cover
    found) - a lightweight way to collect real-world matching failures to
    fix later. Attaches the existing cover (as a thumbnail) and/or the
    online-suggested cover (as the main image) when available, so a missing/
    wrong cover is visible at a glance instead of just implied by text.

    Returns (True, None) on success, (False, reason) on any failure (never
    raises - a failed report shouldn't disrupt the user) - reason is one of
    "http_error" (Discord itself rejected the request - webhook revoked,
    Discord-side rate limit...) or "network_error" (couldn't even reach
    Discord - a real connectivity problem). Kept distinct so the UI doesn't
    blame "no internet connection" for what's actually a Discord-side issue.
    """
    if not DISCORD_REPORT_WEBHOOK_URL:
        return False, "network_error"

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

    # allowed_mentions: [] - field values above come straight from the
    # user's own filenames/tags, which could contain "@everyone" or a role
    # mention (e.g. a bootleg downloaded elsewhere, not named by the user
    # themselves) - this stops Discord from ever treating that as a real
    # ping, regardless of whether embeds actually parse mentions.
    payload = {"embeds": [embed], "allowed_mentions": {"parse": []}}

    try:
        if files:
            response = requests.post(
                DISCORD_REPORT_WEBHOOK_URL, data={"payload_json": json.dumps(payload)}, files=files, timeout=timeout
            )
        else:
            response = requests.post(DISCORD_REPORT_WEBHOOK_URL, json=payload, timeout=timeout)
        if response.status_code in (200, 204):
            return True, None
        return False, "http_error"
    except Exception:
        return False, "network_error"


# Windows usernames that never trigger the automatic "new install"/"scan
# complete" Discord pings below. Case-insensitive. Doesn't apply to
# send_track_report() - that one's an explicit user action ("Report this
# track"), not automatic telemetry.
#
# Empty for a real (frozen/installed) build - Kevin's own account gets
# notified like any other user there, by his own request. Running from
# source (not frozen - i.e. `python interface.py` during development)
# still excludes "kevin" though, so day-to-day dev testing doesn't spam
# the channel the way it used to before that request.
DISCORD_NOTIFICATION_EXCLUDED_USERS = set() if getattr(sys, "frozen", False) else {"kevin"}


def _is_discord_notification_excluded(reporter_name):
    return (reporter_name or "").strip().lower() in DISCORD_NOTIFICATION_EXCLUDED_USERS


def send_no_cover_report(no_cover_infos, total, reporter_name=None, timeout=10):
    """
    Posts an automatic report to Discord when a scan finishes with an
    unusually high no-cover-match rate (see NO_COVER_REPORT_THRESHOLD in
    interface.py) - attaches a .txt file listing every no-cover track's
    filename and current/detected tags, the same info send_track_report()
    sends for a single manually-reported track, so the developer can look
    into a whole batch of matching failures at once instead of waiting on
    the user to report each one individually via "Report track...".

    Returns True on success, False on any failure (never raises) - also
    False without posting anything for an excluded account (see
    DISCORD_NOTIFICATION_EXCLUDED_USERS) or an empty list.
    """
    if _is_discord_notification_excluded(reporter_name) or not DISCORD_REPORT_WEBHOOK_URL:
        return False
    if not no_cover_infos:
        return False

    lines = []
    for info in no_cover_infos:
        lines.append(f"File: {info.get('file') or '(unknown)'}")
        lines.append(
            f"  Current: {info.get('current_artist') or '(none)'} - {info.get('current_title') or '(none)'}"
        )
        lines.append(
            f"  Detected: {info.get('detected_artist') or '(none)'} - {info.get('detected_title') or '(none)'}"
        )
        lines.append(f"  Format: {info.get('format') or '?'}")
        lines.append("")
    content = "\n".join(lines).encode("utf-8")

    embed = {
        "title": "Several tracks with no cover match",
        "color": 0xE74C3C,
        "fields": [
            {"name": "User", "value": reporter_name or "(unknown)", "inline": True},
            {"name": "No cover match", "value": f"{len(no_cover_infos)} of {total}", "inline": True},
            {"name": "App version", "value": APP_VERSION, "inline": True},
        ],
    }
    payload = {"embeds": [embed], "allowed_mentions": {"parse": []}}
    files = {"files[0]": ("no_cover_tracks.txt", content, "text/plain")}

    try:
        response = requests.post(
            DISCORD_REPORT_WEBHOOK_URL, data={"payload_json": json.dumps(payload)}, files=files, timeout=timeout
        )
        return response.status_code in (200, 204)
    except Exception:
        return False


def send_new_install_notification(reporter_name=None, previous_version=None, timeout=10):
    """
    Posts a "new install" (or, when previous_version is given, "app
    updated") ping to the same Discord webhook as send_track_report, so
    the developer knows a new person/machine started using the app, or an
    existing one updated to a newer version. Called at most once per
    version per Windows user account (see _notify_new_install_on_startup
    in interface.py, gated by a saved "last notified version" setting -
    only updated after a successful send, so a failed attempt is retried
    on the next launch instead of being silently given up on forever).
    A genuinely new install also gets a running "Unique users" count (see
    count_unique_discord_users) - not shown on an "app updated" ping, since
    that's the same person, not a new one.
    Returns True on success, False on any failure (never raises) - also
    False without posting anything for an excluded account (see
    DISCORD_NOTIFICATION_EXCLUDED_USERS).
    """
    if _is_discord_notification_excluded(reporter_name) or not DISCORD_REPORT_WEBHOOK_URL:
        return False
    fields = [{"name": "User", "value": reporter_name or "(unknown)", "inline": True}]
    if previous_version:
        fields.append({"name": "Previous version", "value": previous_version, "inline": True})
    else:
        # Unique-user count is only meaningful for a genuinely NEW install,
        # not an existing user updating - and only shown when it could
        # actually be determined (see count_unique_discord_users), so a
        # missing/misconfigured bot token just quietly omits the field
        # instead of showing a wrong number.
        unique_users = count_unique_discord_users()
        if unique_users is not None:
            total = len(unique_users | ({reporter_name.strip().lower()} if reporter_name else set()))
            fields.append({"name": "Unique users", "value": str(total), "inline": True})
    fields.append({"name": "App version", "value": APP_VERSION, "inline": True})
    embed = {
        "title": "App updated" if previous_version else "New install",
        "color": 0x2ECC71,
        "fields": fields,
    }
    try:
        response = requests.post(
            DISCORD_REPORT_WEBHOOK_URL,
            json={"embeds": [embed], "allowed_mentions": {"parse": []}},
            timeout=timeout,
        )
        return response.status_code in (200, 204)
    except Exception:
        return False


def send_scan_complete_notification(
    reporter_name=None, number_new=0, number_removed=0, total=0, number_no_cover=0,
    number_rate_limited_sources=0, auth_error_sources=None, cancelled=False, timeout=10,
):
    """
    Posts a scan-complete ping to the same Discord webhook as
    send_new_install_notification/send_track_report, so the developer
    knows when a user actually uses the app day to day, not just installs
    it. Called once per finished scan, including a scan the user cancelled
    partway through - cancelled=True just relabels the embed so it's
    still visible when someone's actively using the app even if they
    didn't let a scan run to completion (see _finalize_scan in
    interface.py). Returns True on success, False on any failure (never
    raises) - also False without posting anything for an excluded account
    (see DISCORD_NOTIFICATION_EXCLUDED_USERS).
    """
    if _is_discord_notification_excluded(reporter_name) or not DISCORD_REPORT_WEBHOOK_URL:
        return False
    embed = {
        "title": "Scan cancelled" if cancelled else "Scan complete",
        "color": 0xE67E22 if cancelled else 0x3498DB,
        "fields": [
            {"name": "User", "value": reporter_name or "(unknown)", "inline": True},
            {"name": "New files", "value": str(number_new), "inline": True},
            {"name": "Removed files", "value": str(number_removed), "inline": True},
            {"name": "Total files", "value": str(total), "inline": True},
            {
                "name": "No cover match",
                "value": f"{number_no_cover} ({number_no_cover / total:.0%})" if total else str(number_no_cover),
                "inline": True,
            },
            {"name": "Rate-limited sources", "value": str(number_rate_limited_sources), "inline": True},
            {"name": "Auth errors", "value": ", ".join(auth_error_sources) if auth_error_sources else "None", "inline": True},
            {"name": "App version", "value": APP_VERSION, "inline": True},
        ],
    }
    try:
        response = requests.post(
            DISCORD_REPORT_WEBHOOK_URL,
            json={"embeds": [embed], "allowed_mentions": {"parse": []}},
            timeout=timeout,
        )
        return response.status_code in (200, 204)
    except Exception:
        return False


def send_rate_limit_report(source, reporter_name=None, timeout=10):
    """
    Posts a ping to Discord the moment a cover source (iTunes, Spotify,
    SoundCloud, or AcoustID) gets rate-limited during a scan, so the
    developer sees it as it happens instead of only as a count buried in
    the "Scan complete" embed - see the *_rate_limited handlers in
    interface.py's _start_message_loop, each already gated to fire at
    most once per scan per source.

    Returns True on success, False on any failure (never raises) - also
    False without posting anything for an excluded account (see
    DISCORD_NOTIFICATION_EXCLUDED_USERS).
    """
    if _is_discord_notification_excluded(reporter_name) or not DISCORD_REPORT_WEBHOOK_URL:
        return False
    embed = {
        "title": "Rate limit reached",
        "color": 0xE67E22,
        "fields": [
            {"name": "User", "value": reporter_name or "(unknown)", "inline": True},
            {"name": "Source", "value": source, "inline": True},
            {"name": "App version", "value": APP_VERSION, "inline": True},
        ],
    }
    try:
        response = requests.post(
            DISCORD_REPORT_WEBHOOK_URL,
            json={"embeds": [embed], "allowed_mentions": {"parse": []}},
            timeout=timeout,
        )
        return response.status_code in (200, 204)
    except Exception:
        return False


def send_extraction_report(
    reporter_name=None, moved_count=0, removed_count=0, cancelled=False, error=None, timeout=10,
):
    """
    Posts a ping to Discord once the Extractor tab's "flatten a folder"
    action finishes - whether it ran to completion, was cancelled partway
    through (see interface.py's _run_extraction/extract_cancel_requested),
    or failed outright - so the developer knows the feature is actually
    being used, the same visibility send_scan_complete_notification gives
    for a normal scan. cancelled and error are mutually exclusive in
    practice (a cancellation stops cleanly, doesn't raise), but both are
    accepted independently rather than assuming that.

    Returns True on success, False on any failure (never raises) - also
    False without posting anything for an excluded account (see
    DISCORD_NOTIFICATION_EXCLUDED_USERS).
    """
    if _is_discord_notification_excluded(reporter_name) or not DISCORD_REPORT_WEBHOOK_URL:
        return False
    if error:
        title, color = "Extraction failed", 0xE74C3C
    elif cancelled:
        title, color = "Extraction cancelled", 0xE67E22
    else:
        title, color = "Extraction complete", 0x2ECC71
    fields = [
        {"name": "User", "value": reporter_name or "(unknown)", "inline": True},
        {"name": "Files moved", "value": str(moved_count), "inline": True},
        {"name": "Empty folders removed", "value": str(removed_count), "inline": True},
    ]
    if error:
        fields.append({"name": "Error", "value": str(error)[:1000], "inline": False})
    fields.append({"name": "App version", "value": APP_VERSION, "inline": True})
    embed = {"title": title, "color": color, "fields": fields}
    try:
        response = requests.post(
            DISCORD_REPORT_WEBHOOK_URL,
            json={"embeds": [embed], "allowed_mentions": {"parse": []}},
            timeout=timeout,
        )
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


# Single source of truth for what "defaults" means - shared by the Settings
# tab's manual "Reset all settings to default" button and the automatic
# reset every update triggers (see check_and_apply_version_reset()).
DEFAULT_SETTINGS = {
    "theme": "auto",
    "auto_convert_mp3": False,
    "auto_convert_wav_to_aiff": True,
    "fix_track_file_name": True,
    "use_spotify": False,
    "show_log_section": False,
}


def check_and_apply_version_reset():
    """Wipes all saved settings back to defaults the first time the app runs
    after an update (i.e. APP_VERSION differs from the version it last ran
    as) - a fresh version starts with a clean slate rather than carrying
    forward whatever the previous version happened to save. A first-ever
    launch (no recorded version yet) is not a "change" and doesn't reset
    anything - there's nothing to reset."""
    last_run_version = load_settings().get("last_run_version")
    if last_run_version is not None and last_run_version != APP_VERSION:
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(dict(DEFAULT_SETTINGS), f, indent=2)
        except Exception as error:
            print(f"  Could not reset settings for the new version: {error}")
    save_setting("last_run_version", APP_VERSION)


# --- General activity log ---

# Plain-text, human-readable record of every user-facing action (settings
# changes, Apply/Restore runs - old filename/artist/title -> new, reports
# sent, updates installed...) - kept separate from the live per-file scan
# log shown in the "Log" panel, which is UI-only and never written to disk.
# Meant to be found and read by hand (e.g. when troubleshooting a support
# request), not parsed back by the app.
#
# Must survive forever, including across an update: log_action() only ever
# APPENDS to it (never opened in "w"/truncating mode), and neither
# check_and_apply_version_reset() nor "Reset all settings to default" touch
# it - both only ever rewrite SETTINGS_FILE. Keep it that way: nothing in
# this codebase may delete or truncate ACTION_LOG_FILE.
ACTION_LOG_FILE = os.path.join(user_config_dir(), "activity_log.txt")


def log_action(message):
    """Appends one timestamped line to ACTION_LOG_FILE. Never deletes or
    truncates it - see the module-level comment above ACTION_LOG_FILE."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(ACTION_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception as error:
        print(f"  Could not write to the activity log: {error}")


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

    log_action(
        f"Applied: '{old_file}' -> '{new_file}' | "
        f"Artist: '{old_artist or ''}' -> '{new_artist or ''}' | "
        f"Title: '{old_title or ''}' -> '{new_title or ''}' | "
        f"Cover updated: {cover_updated} | Converted: {converted}"
    )


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

    A WAV->AIFF conversion (see convert_wav_to_aiff) is reverted back to WAV
    too, since that's a lossless byte-order swap with nothing actually lost -
    unlike a conversion to MP3 (a real re-encode), which isn't reversible,
    so the file just keeps its current format/extension in that case. The
    file is then renamed back to its logged "old_file" name (falling back to
    reconstructing "Artist - Title" from old_artist/old_title only for an
    entry logged before "old_file" existed).

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

    # Undo a WAV->AIFF conversion from the run being restored, before
    # restoring tags - identified by comparing the file's format when this
    # entry was logged (old_file's extension) against what it is now, not
    # just the "converted" flag (which is also set for a same-run MP3
    # conversion, which ISN'T reversible - see convert_aiff_to_wav's
    # docstring).
    old_extension = os.path.splitext(entry.get("old_file") or "")[1].lower()
    current_extension = os.path.splitext(full_path)[1].lower()
    if old_extension == ".wav" and current_extension == ".aiff":
        reverted_path = convert_aiff_to_wav(full_path)
        if reverted_path:
            log(f"  Reverted the WAV->AIFF conversion: '{reverted_path}'")
            full_path = reverted_path
        else:
            log("  Could not revert the WAV->AIFF conversion - tags/filename will still be restored on the AIFF.")

    old_artist = entry.get("old_artist") or ""
    old_title = entry.get("old_title") or ""
    old_cover_b64 = entry.get("old_cover_b64")
    old_cover_bytes = base64.b64decode(old_cover_b64) if old_cover_b64 else None

    write_tags(
        full_path, old_artist, old_title, cover_image=old_cover_bytes,
        force_remove_if_missing=True,  # no old cover logged -> remove whatever cover is there now
        # Always True, same reasoning as force_remove_if_missing above: an
        # empty old_artist/old_title means the file had no tag at all
        # before, so restoring must actively clear it (see write_tags'
        # delall branch), not skip touching it and leave Apply's tags in
        # place - a real user-reported bug (fixed 2026-08-22).
        update_title=True, update_artist=True, update_cover=True,
        log=log,
    )
    log(f"  Restored tags on: '{full_path}'")

    # Derived from full_path's actual current directory, not the
    # originally-logged one - matters when the file was found via
    # override_path or the auto-locate search above, since either can put
    # it somewhere other than "folder".
    actual_folder = os.path.dirname(full_path)
    # full_path's OWN current extension, not old_file's - they only differ
    # if the WAV->AIFF revert above was attempted but failed, in which case
    # the file is still actually AIFF and naming it "....wav" would lie
    # about its real format.
    current_extension = os.path.splitext(full_path)[1]

    old_file = entry.get("old_file")
    if old_file:
        # The exact original filename this entry logged, rather than
        # reconstructed as "Artist - Title" from old_artist/old_title -
        # covers a file that had no tags at all before Apply (old_artist/
        # old_title then both empty, so there was nothing to reconstruct
        # from and this rename used to be skipped entirely - a real
        # user-reported bug: "restore" left renamed files renamed) as well
        # as a file whose original name never followed that convention in
        # the first place.
        old_base_name = os.path.splitext(os.path.basename(old_file))[0]
        new_base_name = sanitize_filename(old_base_name) + current_extension
    elif old_artist or old_title:
        # Older history entries logged before "old_file" existed - fall
        # back to the previous reconstruction.
        new_base_name = sanitize_filename(build_display_name(old_artist, old_title)) + current_extension
    else:
        new_base_name = None

    if new_base_name:
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

# Which cover sources are enabled - whichever are enabled are tried in a
# fixed priority order: iTunes, then Spotify, then SoundCloud - see
# _search_one_source() / search_cover_manual(). iTunes and Spotify are
# both curated commercial catalogs; SoundCloud is community-uploaded and
# far more prone to a wrong match (an unrelated repost, a fan edit...) -
# real report: SoundCloud "succeeded" with a non-official image (a radio
# freestyle photo) before Spotify ever got a chance to offer the track's
# actual official cover. SoundCloud stays useful as the final fallback
# for remixes/edits that live there and nowhere else.
# iTunes/SoundCloud are always on - fixed, not a user-facing toggle (kept
# as named constants rather than inlining True, since the rest of the
# codebase still reads them as "is this source active"). USE_SPOTIFY is
# the one exception: set by the UI (off by default) - Spotify's own
# real-world rate limit (see SPOTIFY_SEARCH_RATE_LIMIT_COOLDOWN_SECONDS)
# has repeatedly made it fail before ever contributing a cover, with
# nothing actionable for the user beyond waiting on Spotify's own Extended
# Quota Mode approval - a toggle lets it be skipped entirely instead of
# wasting a request on every scan until then.
USE_ITUNES = True
USE_SPOTIFY = False
USE_SOUNDCLOUD = True

# Last-resort audio-content identification (AcoustID/Chromaprint) for a file
# whose filename/tags are too mangled for the normal text-based search to
# even attempt, or that search comes up empty - never tried otherwise, since
# it needs to read/analyze the actual audio (much slower than a text search)
# and would just waste API calls on files the fast path already handles
# fine. Always on (see the comment above USE_ITUNES) - a no-op only if
# ACOUSTID_API_KEY is somehow unavailable.
USE_ACOUSTID_FALLBACK = True

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

# Whether a processed file gets renamed to "Artist - Title.ext" at Apply
# time (set by the UI, on by default). Tags are always written regardless
# of this setting - it only controls the FILENAME itself, for a user who'd
# rather keep their own existing file naming untouched.
FIX_TRACK_FILE_NAME = True


# ============================================================================
# 2. FILENAME & TITLE CLEANING
# ============================================================================

# A trailing "-v6"/"- v6" OR a bare "v2" preceded only by whitespace (no
# dash) - real report: "Retrograde (MIAMO Edit) v2" wasn't caught by the
# dash-only pattern, so the literal "v2" ended up in the search query and
# broke the search itself (not just the match check) even though the
# cover was found instantly once "v2" was removed. \b before "v" (via the
# whitespace/dash requirement) keeps this from ever touching a real word
# that happens to end in "v" + digits with no separator (e.g. "Motiv2").
VERSION_SUFFIX_RE = re.compile(r"(?:\s*-\s*|\s+)v\d+\s*$", re.IGNORECASE)

# DJ-pool export convention: "Title - <Camelot key> - <BPM>" (e.g.
# "Juno - 4A - 122") - the key is always 1-2 digits + A/B, the BPM 2-3
# digits, both tacked on by the crate/pool software, never part of the
# real title.
DJ_POOL_KEY_BPM_SUFFIX_RE = re.compile(r"\s*-\s*\d{1,2}[AB]\s*-\s*\d{2,3}\s*$", re.IGNORECASE)

# Windows' own "(1)"/"(2)" suffix, appended when a file was copied into a
# folder that already had one with the same name - never part of the real
# title either.
DUPLICATE_FILE_MARKER_RE = re.compile(r"\s*\(\d+\)\s*$")

# A DJ's own bare mix-number marker (e.g. "... (Nabler Edit) M1") - no
# dash, just a trailing space before it, so kept case-SENSITIVE (unlike
# the other patterns above) to stay narrow: a real title is far less
# likely to end in an uppercase "M" + digit(s) than in some lowercase
# word that happens to fit the same shape. Real report verified live
# against the actual SoundCloud upload: its real title has no "M1" at
# all, confirming it's the filer's own addition, not part of the release.
MIX_NUMBER_SUFFIX_RE = re.compile(r"\s+M\d{1,2}\s*$")

# "KLICKAUD" watermark, tacked on (with a leading underscore or space) at
# the very end of the filename by that download source - unlike
# MENTIONS_TO_REMOVE (a user-populated list for one-off/per-user cases),
# this recurs often enough across different users' files to be worth its
# own always-on rule, same idea as FUVICLAN_PATTERN further down.
KLICKAUD_WATERMARK_RE = re.compile(r"[_\s]*klickaud\s*$", re.IGNORECASE)

# "Extended Mix" normalization: fix any casing to the one proper-cased form,
# and "[Extended Mix]" (square brackets, as some sources tag it) to
# "(Extended Mix)" to match this app's own parenthesized qualifier
# convention (see build_display_name / GENERIC_MIX_KEYWORDS).
EXTENDED_MIX_BRACKETS_RE = re.compile(r"\[\s*extended mix\s*\]", re.IGNORECASE)
EXTENDED_MIX_CASING_RE = re.compile(r"extended mix", re.IGNORECASE)


def clean_title(text):
    text = EXTENDED_MIX_BRACKETS_RE.sub("(Extended Mix)", text)
    text = EXTENDED_MIX_CASING_RE.sub("Extended Mix", text)

    if KLICKAUD_WATERMARK_RE.search(text):
        text = KLICKAUD_WATERMARK_RE.sub("", text)
        # KLICKAUD's own filenames consistently use underscores in place of
        # spaces throughout, not just right before the watermark - unlike
        # the general lowercase-only heuristic in parse_filename() (kept
        # deliberately narrow there, to avoid mangling a stylized artist
        # name that uses an underscore on purpose), this source is
        # identifiable enough to trust unconditionally, regardless of case.
        text = re.sub(r"\s{2,}", " ", text.replace("_", " ")).strip()

    for mention in MENTIONS_TO_REMOVE:
        text = re.sub(re.escape(mention), "", text, flags=re.IGNORECASE)
        text = re.sub(r"\(\s+", "(", text)   # trim leftover space right after "("
        text = re.sub(r"\s+\)", ")", text)   # trim leftover space right before ")"
        text = re.sub(r"\s{2,}", " ", text)  # collapse any remaining double spaces

    # Strip trailing junk tacked on by something other than the actual
    # release (a producer/DJ's own "-v6" working-version marker or bare
    # "M1" mix-number marker, a DJ-pool export's "- <key> - <BPM>",
    # Windows' own "(1)" duplicate-file marker) - repeated until nothing
    # more changes, since these can stack in any order (e.g. "... - 4A -
    # 122 (1)" has the duplicate marker AFTER the key/BPM suffix). No real
    # song title plausibly ends with any of these, so safe to strip
    # unconditionally rather than only for comparisons.
    while True:
        stripped = VERSION_SUFFIX_RE.sub("", text)
        stripped = DJ_POOL_KEY_BPM_SUFFIX_RE.sub("", stripped)
        stripped = DUPLICATE_FILE_MARKER_RE.sub("", stripped)
        stripped = MIX_NUMBER_SUFFIX_RE.sub("", stripped)
        if stripped == text:
            break
        text = stripped

    # "X Extended Remix" -> "X Remix": on request, a droppable modifier
    # rather than a distinct version - written to the title itself now,
    # not just used for cover-search comparison (see
    # strip_generic_qualifier_modifiers, defined further down but callable
    # here since Python resolves this at call time, not definition order).
    # Only applied inside a NAMED qualifier group (is_named_remix_qualifier)
    # - a purely generic bare "(Extended Mix)" is a real, complete label on
    # its own and must be left alone; blindly stripping "Extended" from
    # ANY group would otherwise mangle it into the nonsensical "(Mix)".
    def _strip_extended_in_named_group(match):
        content = match.group(1)
        if is_named_remix_qualifier(content):
            return f"({strip_generic_qualifier_modifiers(content)})"
        return match.group(0)

    text = re.sub(r"\(([^()]*)\)", _strip_extended_in_named_group, text)

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


# An "unreleased" track (private edit/bootleg/promo not out anywhere yet)
# has no official listing to match against - a filename-derived guess for
# one is more likely to be noise than a real fix, so it shouldn't default
# to being auto-applied like a normal match would (see "apply_changes"
# below). \b keeps this from firing on an unrelated word that merely
# contains "unreleased" as a substring.
UNRELEASED_MARKER_RE = re.compile(r"\bunreleased\b", re.IGNORECASE)


def contains_unreleased_marker(file_name):
    return bool(UNRELEASED_MARKER_RE.search(os.path.basename(file_name)))


# Pattern for names like: "Title Artist, Remix (Remixer) Extended"
# -> becomes artist="Artist", title="Title (Remixer Remix)"
REMIX_WITH_COMMA_PATTERN = re.compile(
    r"^(?P<title>.+)\s+(?P<artist>\S+),\s*Remix\s*\((?P<remixer>[^)]+)\)\s*(?:Extended)?\s*$",
    re.IGNORECASE,
)


GENERIC_MIX_LABELS = {
    "extended mix", "extended edit", "extended",
    "radio edit", "radio mix", "club mix", "original mix", "instrumental mix",
    "mixed", "mix", "remix",
    "extended rework", "rework",
}


def is_named_remix_qualifier(content):
    """
    True for a specific/named remix, edit, mix, or bootleg credit (e.g.
    "Royale BR Bootleg", "Raphael Palacci Remix", "One For The Sunrise
    Mix") - one that names a particular person/version, as opposed to a
    purely generic, interchangeable label like "Extended Mix" or "Radio
    Edit" (see GENERIC_MIX_LABELS - those exact compound phrases are still
    excluded below, "mix" alone isn't enough to make something generic).

    "mix" is a real keyword here, same as "remix"/"edit"/"bootleg"/
    "reboot" - real report: "Isaac Notes (One For The Sunrise Mix)" wasn't
    recognized as named at all (the check used to only look for "remix"/
    "edit"/"reboot"/"bootleg", missing bare "mix" entirely, unlike
    DASH_MIX_KEYWORDS just above which already includes it), so the
    qualifier was silently dropped from EVERY search attempt instead of
    being tried - not even a "loose" mismatch, nothing to search with at
    all beyond the bare title.
    """
    lowered = content.lower().strip()
    has_named_keyword = any(keyword in lowered for keyword in ("remix", "edit", "mix", "reboot", "bootleg", "rework"))
    return has_named_keyword and lowered not in GENERIC_MIX_LABELS


def is_generic_mix_qualifier(content):
    return content.lower().strip() in GENERIC_MIX_LABELS


def find_named_qualifier_groups(title):
    """
    Returns every parenthesized remix/edit/bootleg credit in `title` that
    NAMES a specific person/version (e.g. "Royale BR Bootleg", "Raphael
    Palacci Remix") - more than just the bare mix-type keyword on its own.
    A lone "(Remix)" doesn't count as named (too ambiguous - could be
    anyone's, unlike a credit with an actual name attached), same as a
    purely generic label like "(Extended Mix)".
    """
    named_groups = []
    for group in re.findall(r"\(([^)]*)\)", title):
        lowered = group.lower().strip()
        if lowered in GENERIC_MIX_LABELS:
            continue
        words = lowered.split()
        has_keyword = any(keyword in words for keyword in ("remix", "edit", "mix", "reboot", "bootleg", "rework"))
        if has_keyword and len(words) > 1:
            named_groups.append(group.strip())
    return named_groups


def title_has_named_qualifier(title):
    """True if `title` contains at least one named qualifier - see find_named_qualifier_groups()."""
    return bool(find_named_qualifier_groups(title))


def named_qualifier_name_words(title):
    """
    Returns the significant "name" words (length >= 3, the mix-type
    keyword itself excluded) from title's named qualifier(s), e.g. "Royale
    BR Bootleg" -> {"royale"} ("br" is too short, "bootleg" is the keyword
    itself, not part of the name). Empty set if there's no named qualifier.
    Used to verify a candidate cover-search result actually credits the
    SPECIFIC named remix/bootleg being looked for, not just the base song.
    """
    keyword_words = {"remix", "edit", "reboot", "bootleg", "rework"}
    words = set()
    for group in find_named_qualifier_groups(title):
        for word in re.findall(r"\w+", group.lower()):
            if len(word) >= 3 and word not in keyword_words:
                words.add(word)
    return words


def remove_redundant_generic_mix(text):
    """
    If the text has a specific named remix/edit credit like "(Raphael Palacci
    Remix)" immediately followed by a generic descriptor like "(Extended Mix)",
    the generic one is redundant (the named remix already implies it) and
    gets removed automatically.
    """
    def replacement(match):
        first, second = match.group(1), match.group(2)
        if is_named_remix_qualifier(first) and is_generic_mix_qualifier(second):
            return f"({first})"
        return match.group(0)

    return re.sub(r"\(([^)]*)\)\s*\(([^)]*)\)", replacement, text)


def balance_parentheses(text):
    """Adds missing closing parentheses at the end, if some were left unclosed."""
    missing = text.count("(") - text.count(")")
    return text + (")" * missing) if missing > 0 else text


def strip_slash_credit(text):
    """
    Some stores credit the original artist inline in the title via a
    "/ Name" segment instead of the artist field or a bracketed remix
    qualifier - e.g. iTunes' own "Patadas de Ahogado / LATIN MAFIA
    (Rework)" for Hugel's official rework of a Latin Mafia & Humbe
    original (our filename never uses this convention - the same track
    was named "Patadas de Ahogado (Extended Rework)"). Left alone, the
    inserted "/ LATIN MAFIA" stays part of the core title and makes even
    the exact right result fail exact_match()/loose_remix_match() against
    every one of our own filename conventions. Only the FIRST "/ ..."
    segment is stripped (up to the next bracket or the end of the
    string) - a title genuinely built around more than one slash is rare
    enough not to guess about.
    """
    return re.sub(r"\s*/\s*[^/()\[\]]+?(?=\s*[\(\[]|$)", "", text, count=1).strip()


DASH_MIX_KEYWORDS = ("remix", "edit", "mix", "bootleg", "reboot", "rework")


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
    artist_part = artist_part.strip()
    if artist_part.startswith("(") and artist_part.endswith(")"):
        # An already-parenthesized last part is a remix/bootleg suffix tag,
        # never a real artist name (e.g. "Chris Brown - Gimme That Remix
        # ft. Lil' Wayne - (Royale BR Bootleg)") - bail out and let the
        # standard "Artist - Title" case + reformat_trailing_dash_mix
        # handle it instead, rather than misreading it as the artist.
        return None
    if any(keyword in mix_part.lower() for keyword in DASH_MIX_KEYWORDS):
        return artist_part, f"{title_part.strip()} ({mix_part.strip()})"
    return None


# A bare domain-looking token, e.g. "SpotiDownloader.com", "YTMP3.cc" - no
# spaces, ending in a short dot-suffix. Matches the branding prefix various
# "download this Spotify/YouTube track" tools stamp onto their output
# filenames.
DOWNLOADER_SITE_PREFIX_RE = re.compile(r"^\S+\.\w{2,4}$")


def try_split_downloader_site_prefix(text):
    """
    Detects the "Site.com - Title - Artist" pattern some track-downloader
    tools stamp onto their output filenames (exactly two dashes, where the
    FIRST part looks like a bare website domain, never a real artist
    name), e.g. "SpotiDownloader.com - Imagination - Samm" ->
    artist="Samm", title="Imagination". Note this convention puts the
    TITLE before the ARTIST, the reverse of the usual "Artist - Title".
    Returns (artist, title), or None if the pattern doesn't match.
    """
    parts = re.split(r"\s+-\s+", text)
    if len(parts) != 3:
        return None

    site_part, title_part, artist_part = parts
    if not DOWNLOADER_SITE_PREFIX_RE.match(site_part.strip()):
        return None
    return artist_part.strip(), title_part.strip()


def reformat_trailing_dash_mix(text):
    """
    If text ends with " - <mix descriptor>" (e.g. "Related - Original Mix"),
    converts it to "Related (Original Mix)". Returns the text unchanged if no
    such pattern is found.

    The mix-descriptor group only excludes a REAL dash separator (" - ",
    space on both sides) rather than every hyphen character - a plain
    [^-]+ used to also reject a descriptor containing a hyphenated NAME
    with no surrounding spaces (e.g. "Jean-Marc & Samson Remix"), silently
    leaving the whole string unconverted instead of just excluding an
    actual earlier dash-separated segment.
    """
    match = re.match(r"^(.+?)\s+-\s+((?:(?!\s-\s).)+)$", text)
    if not match:
        return text

    before, after = match.group(1).strip(), match.group(2).strip()
    if any(keyword in after.lower() for keyword in DASH_MIX_KEYWORDS):
        # Already parenthesized (e.g. "... - (Royale BR Bootleg)") -> just
        # attach it, don't wrap it in a second layer of parens.
        already_wrapped = after.startswith("(") and after.endswith(")")
        return f"{before} {after}" if already_wrapped else f"{before} ({after})"
    return text


BARE_FEATURE_RE = re.compile(
    r"\s+(?:feat\.?|ft\.?|featuring)\s+([^()\[\]]+?)(?=\s*[\(\[]|\s*$)",
    re.IGNORECASE,
)


def _move_bare_feature_credit_to_artist(artist, title):
    """
    Moves a bare (unparenthesized) "ft. X" / "feat. X" / "featuring X"
    credit out of the title and appends it to the artist instead, e.g.
    "Gimme That Remix ft. Lil' Wayne" (artist "Chris Brown") becomes
    title "Gimme That Remix", artist "Chris Brown ft. Lil' Wayne" -
    filenames commonly place it right after the song title even though
    it describes the artist. Only the BARE form: a credit already wrapped
    in its own "(feat. X)"/"[ft. X]" is left as part of the title as-is -
    that's a separate, deliberate case some stores also include in the
    title, handled for matching purposes by strip_feature_suffix() /
    extract_feature_names() instead of being rewritten here.
    """
    match = BARE_FEATURE_RE.search(title)
    if not match:
        return artist, title

    feature_names = match.group(1).strip()
    new_title = (title[:match.start()] + title[match.end():]).strip()
    new_title = re.sub(r"\s{2,}", " ", new_title)
    new_artist = f"{artist} ft. {feature_names}" if artist else feature_names
    return new_artist, new_title


def parse_filename(file_name):
    base_name = os.path.basename(file_name)
    name_no_ext = os.path.splitext(base_name)[0]
    # Normalize en dash (–) and em dash (—) to a plain hyphen before any
    # splitting below - some sources (e.g. Vinyl On Demand releases) use
    # them instead of "-" as the Artist/Title separator, and every split
    # pattern in this function only recognizes a literal ASCII hyphen.
    name_no_ext = name_no_ext.replace("–", "-").replace("—", "-")
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

    artist = title = None

    # Special case: "Title Artist, Remix (Remixer) Extended"
    remix_match = REMIX_WITH_COMMA_PATTERN.match(name_no_ext)
    if remix_match:
        raw_title = remix_match.group("title").strip()
        artist = remix_match.group("artist").strip()
        remixer = remix_match.group("remixer").strip()
        title = f"{raw_title} ({remixer} Remix)"

    # Special case: "Site.com - Title - Artist" (a downloader tool's branding
    # prefix) - checked before the mix-artist case below since it's the more
    # specific/certain signal of the two.
    if artist is None:
        site_title_artist = try_split_downloader_site_prefix(name_no_ext)
        if site_title_artist:
            artist, title = site_title_artist

    # Special case: "Title - Mix Info - Artist" (three dash-separated parts)
    if artist is None:
        title_mix_artist = try_split_title_mix_artist(name_no_ext)
        if title_mix_artist:
            artist, title = title_mix_artist

    # Standard case: "Artist - Title"
    # Requires a REAL " - " (space on both sides), like reformat_trailing_
    # dash_mix()/resolve_artist_title() already do - a bare hyphen with no
    # surrounding spaces is almost always part of a name (e.g. "Jean-Marc"),
    # not an Artist/Title separator. Real report: "Marshall Jefferson,
    # Samson, Maesic, Jean-Marc, Salomé Das - Life Is Simple (Extended
    # Remix)" - the old \s*-\s* matched "Jean-Marc"'s own hyphen (the
    # FIRST one in the string, since the match is non-greedy) instead of
    # the real separator before the title, splitting the artist list itself
    # in half and mangling everything after it.
    if artist is None:
        match = re.match(r"^(.+?)\s+-\s+(.+)$", name_no_ext)
        if match:
            artist = match.group(1).strip()
            title = reformat_trailing_dash_mix(match.group(2).strip())

    # Fallback: a hyphen with whitespace right AFTER it but none before
    # (e.g. "AdrianRoman- Oblique Strategies" - the shape a KLICKAUD-style
    # "Artist-_Title" filename is left in once clean_title() above turns
    # its underscores into spaces). Still narrow enough to not reopen the
    # "Jean-Marc" bug the standard case above was narrowed to avoid: an
    # actual hyphenated name is never itself followed by whitespace
    # ("Jean- Marc" isn't a real name), so this only fires on filenames
    # the standard space-both-sides case already ruled out.
    if artist is None:
        match = re.match(r"^(.+?)-\s+(.+)$", name_no_ext)
        if match:
            artist = match.group(1).strip()
            title = reformat_trailing_dash_mix(match.group(2).strip())

    if artist is None:
        return None, None

    return _move_bare_feature_credit_to_artist(artist, title)


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
    filename_artist, filename_title = detected_artist, detected_title

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
    # just happens to contain a dash (e.g. "Some Song - Reprise"). Requires a
    # real " - " (space on both sides), like reformat_trailing_dash_mix()
    # just below - a bare "-" with no leading space is a version/take suffix
    # glued onto the title (e.g. "Title (X Remix)-v6"), not an artist/title
    # separator; real report: "Chango" (the artist) happened to also appear
    # inside a "(YASMINA, Chango Remix)" credit that IS the actual title,
    # which a looser dash match mistook for "artist - title" and mangled
    # into artist="Title (YASMINA, Chango Remix)" / title="v6".
    title_looks_combined = False
    if current_title and current_artist:
        combined_match = re.match(r"^(.+?)\s+-\s+(.+)$", current_title)
        if combined_match:
            candidate_artist = combined_match.group(1).strip()
            candidate_artist = re.sub(r"^\d{1,3}\s*[.\-]\s*", "", candidate_artist).strip()
            candidate_title = clean_title(remove_redundant_generic_mix(combined_match.group(2).strip()))
            # Also accepts the split when the FILENAME's own parsed artist
            # matches candidate_artist exactly, not just when it's a
            # substring of the existing artist tag - covers a file whose
            # existing artist tag is itself wrong (e.g. a record label
            # used as the artist, like "keinemusik" instead of "&ME"), so
            # the substring check against that wrong tag would otherwise
            # never pass even though the filename independently agrees
            # with the split (real report: "&ME - L.I.F.E.mp3" tagged
            # with artist "keinemusik", title "&ME - L.I.F.E").
            filename_agrees = bool(filename_artist) and filename_artist.strip().lower() == candidate_artist.lower()
            if current_artist.strip().lower() in candidate_artist.lower() or filename_agrees:
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
        # A file's tags sometimes split a NAMED remix credit (e.g. "(Clement
        # Chapelle Remix)") by moving the remixer into the artist field and
        # collapsing the title down to a bare generic "(Remix)" - losing the
        # remixer's name the filename itself still spells out in full. Since
        # that's strictly less information than the filename has, prefer the
        # filename's own artist/title in that specific case rather than
        # trusting the (technically "already present") tags as usual.
        remixer_lost_in_tags = False
        if filename_title and title_has_named_qualifier(filename_title) and not title_has_named_qualifier(current_title):
            for group in find_named_qualifier_groups(filename_title):
                remixer_name = re.sub(r"\b(?:remix|edit|reboot|bootleg)\b", "", group, flags=re.IGNORECASE)
                remixer_name = re.sub(r"\s+", " ", remixer_name).strip()
                if remixer_name and remixer_name.lower() in current_artist.lower():
                    remixer_lost_in_tags = True
                    break

        if remixer_lost_in_tags:
            detected_artist = filename_artist
            detected_title = clean_title(filename_title)
        else:
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

def extract_audio_files(root_folder, log=safe_print, should_cancel=None):
    """
    Recursively finds audio files (mp3, wav, flac, aac, m4a, ogg, wma, aiff,
    alac, opus...) sitting inside subfolders of root_folder and moves them
    directly into root_folder (flattening the structure). Files already
    directly in root_folder are left untouched. Returns the number of files
    actually moved.

    should_cancel() is checked once per subfolder (not per file - os.walk's
    own per-folder granularity is responsive enough without adding overhead
    to every single move) - if it returns True, stops early and returns
    whatever was moved so far, same "cancel between units of work, not
    mid-write" approach as scan_files()'s should_cancel.
    """
    moved_count = 0
    root_abspath = os.path.abspath(root_folder)

    for current_folder, _dirs, files in os.walk(root_folder):
        if should_cancel and should_cancel():
            log("  Extraction cancelled.")
            break

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


def remove_empty_subfolders(root_folder, log=safe_print, should_cancel=None):
    """Removes now-empty subfolders left behind after extraction. Returns
    how many were removed. should_cancel() is checked once per subfolder,
    same as extract_audio_files()."""
    removed_count = 0
    root_abspath = os.path.abspath(root_folder)

    for current_folder, _dirs, _files in os.walk(root_folder, topdown=False):
        if should_cancel and should_cancel():
            log("  Empty-folder cleanup cancelled.")
            break

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


def compute_search_titles(title):
    """
    Derives (search_title, remix_qualified_title) from a track's title, for
    the cover search only - never written to the file's own tags.

    remix_qualified_title keeps a parenthetical qualifier attached ONLY when
    it's a genuinely NAMED remix/edit/bootleg credit (see
    title_has_named_qualifier) - otherwise it's identical to search_title.
    A purely generic label like "(Original Mix)"/"(Extended Mix)" adds no
    real specificity, and used to be kept anyway: SoundCloud's looser
    title_words_overlap() check (search_cover_soundcloud) then treated
    "mix"/"original" as meaningfully shared words against a COMPLETELY
    different song that happened to carry the same generic label,
    producing a confident but wrong cover match.
    """
    search_title = strip_parentheses(title)
    if title_has_named_qualifier(title):
        return search_title, strip_trailing_noise_words(title)
    return search_title, search_title


FUVICLAN_PATTERN = re.compile(r"by\s*fuvi\s*clan", re.IGNORECASE)


def detect_fuviclan_mention(file_name):
    """
    Looks for a "By Fuvi Clan" (any spacing/casing) mention in the raw filename.
    Returns the exact matched substring if found, or None.
    """
    base_name = os.path.basename(file_name)
    match = FUVICLAN_PATTERN.search(base_name)
    return match.group(0) if match else None


# Perceptual hashes (dHash, 8x8) of known "placeholder" covers - a
# downloaded file sometimes already has one of these baked in even when
# its filename doesn't mention the source that added it (unlike the
# FUVICLAN_PATTERN case above, which relies on the filename saying so).
# Also checked against a freshly found candidate cover from iTunes/
# SoundCloud (see is_banned_cover_image's call sites) - a generic
# "aesthetic"/branding photo a repost account reuses across every one of
# its uploads can otherwise pass the artist/title text match despite
# having nothing to do with the actual release.
BANNED_COVER_HASHES = (
    # 5 variants of the same "Fuvi Clan" watermark artwork on a different
    # background - kept as separate entries since the backgrounds differ
    # enough that a single average hash wouldn't recognize all 5.
    0x80848E8E8E06B6FE,
    0xB8962B4D170F8C41,
    0x8024D4B2E8F1D31C,
    0xCCAA4D4D960F8A8E,
    0x0962B4D170F8EE9,
    # Generic "girl in a pink coat" lifestyle photo, reused by a SoundCloud
    # "aesthetic playlist" repost account across unrelated tracks.
    0x30B5A7A4C6891113,
    # "Deep House District" channel logo/branding, reused by a SoundCloud
    # repost account across unrelated tracks - real report: "Amine Edge &
    # DANCE - Halfway Crooks" picked this up instead of the track's own
    # cover.
    0x46878E4C0F0F8C0F,
)

BANNED_COVER_HASH_THRESHOLD = 6  # Hamming distance - see is_banned_cover_image()


def _cover_dhash(image_bytes, hash_size=8):
    """
    Computes a difference hash (dHash) of an image: resizes down to a tiny
    grayscale grid and encodes, bit by bit, whether each pixel is brighter
    than its right neighbor. Small enough a fingerprint that a JPEG
    re-compression or resize of the SAME image (e.g. re-hosted by a
    different download source) still lands only a few bits away, while an
    unrelated image lands dozens of bits away - see
    BANNED_COVER_HASH_THRESHOLD. Returns None if the bytes aren't a valid
    image.
    """
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("L").resize((hash_size + 1, hash_size), Image.LANCZOS)
    except Exception:
        return None
    pixels = list(image.getdata())
    bits = 0
    for row in range(hash_size):
        row_start = row * (hash_size + 1)
        for col in range(hash_size):
            bits <<= 1
            if pixels[row_start + col] > pixels[row_start + col + 1]:
                bits |= 1
    return bits


def is_banned_cover_image(cover_bytes):
    """
    Whether cover_bytes visually matches one of BANNED_COVER_HASHES closely
    enough to be considered the same placeholder image, not a coincidence.
    """
    if not cover_bytes:
        return False
    cover_hash = _cover_dhash(cover_bytes)
    if cover_hash is None:
        return False
    return any(
        bin(cover_hash ^ banned_hash).count("1") <= BANNED_COVER_HASH_THRESHOLD
        for banned_hash in BANNED_COVER_HASHES
    )


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

def _search_one_source(
    source, artist, search_title, remix_qualified_title, soundcloud_token, spotify_token, log,
    on_itunes_rate_limited=None, on_spotify_rate_limited=None,
):
    """
    Tries a single cover source, using that provider's own established
    query strategy:
    - iTunes/Spotify: both curated commercial catalogs, so they share the
      same strategy - the plain (parens-stripped) title first, then a
      retry with the remix qualifier kept in the query if that misses and
      the two titles actually differ (e.g. a heavily-remixed song where
      the plain query ranks a different remix first) - UNLESS the
      qualifier names a specific remix/edit/bootleg (see
      title_has_named_qualifier), in which case the qualified title is
      tried FIRST and alone: a plain-title fallback risks matching a
      different, unrelated official release that just shares the base
      title (e.g. an official "(Remix)" single, when what's wanted is
      someone's unofficial bootleg of it) - better to fall through to
      SoundCloud, where unofficial reworks actually tend to live, than
      silently substitute the wrong version's artwork.
    - SoundCloud: goes straight for the remix-qualified title, since
      remixes/edits live there more often than a plain search would find.
    Returns (match_result, source_label) - source_label is None when
    nothing matched.
    """
    has_named_qualifier = remix_qualified_title != search_title and title_has_named_qualifier(remix_qualified_title)

    if source in ("itunes", "spotify"):
        search_function = (
            lambda a, t, log, allow_loose_remix_match=False: search_cover_itunes(
                a, t, log=log, allow_loose_remix_match=allow_loose_remix_match, on_rate_limited=on_itunes_rate_limited,
            )
        ) if source == "itunes" else (
            lambda a, t, log, allow_loose_remix_match=False: search_cover_spotify(
                a, t, spotify_token, log=log, allow_loose_remix_match=allow_loose_remix_match,
                on_rate_limited=on_spotify_rate_limited,
            )
        )
        label = "iTunes" if source == "itunes" else "Spotify"
        if has_named_qualifier:
            match_result = search_function(artist, remix_qualified_title, log=log, allow_loose_remix_match=True)
            return match_result, (label if match_result else None)
        # allow_loose_remix_match=True even here (no named qualifier of
        # our own to retry with) - loose_remix_match() itself now also
        # accepts a store's title once ANY of its own extra trailing
        # groups are stripped away, as long as the base core title and
        # artist still match exactly, when OUR side has no groups to
        # compare against in the first place (see its docstring) - real
        # report: "Crystal Waters - Gypsy Woman (Extended Mix)" vs. the
        # store's actual "Gypsy Woman (She's Homeless) (La Da Dee La Da
        # Da) [Basement Boy Strip To The Bone Mix]" - three extra groups
        # our simple "(Extended Mix)" (a purely generic qualifier, so
        # search_title/remix_qualified_title are identical here) had no
        # way to account for.
        match_result = search_function(artist, search_title, log=log, allow_loose_remix_match=True)
        if not match_result and remix_qualified_title != search_title:
            match_result = search_function(artist, remix_qualified_title, log=log, allow_loose_remix_match=True)
        return match_result, (label if match_result else None)

    # source == "soundcloud"
    match_result = search_cover_soundcloud(artist, remix_qualified_title, soundcloud_token, log=log)
    return match_result, ("SoundCloud" if match_result else None)


def search_cover_manual(
    artist, title, soundcloud_token, log=safe_print, spotify_token=None,
    on_itunes_rate_limited=None, on_spotify_rate_limited=None,
):
    """
    Searches for a cover using the given artist/title directly, trying
    every enabled source in priority order (iTunes, then Spotify, then
    SoundCloud) and stopping at the first match - used by the "fix Artist/
    Title and search again" flow after a scan finds no match at all
    (user-corrected artist/title), and by "Rescan".

    Spotify comes before SoundCloud despite USE_SPOTIFY's name/history as
    a "last resort" (see CLAUDE.md) - both iTunes and Spotify are curated
    commercial catalogs, while SoundCloud is community-uploaded and far
    more prone to a wrong match (an unrelated repost, a fan edit...) - a
    real report showed SoundCloud "succeeding" with a non-official image
    before Spotify ever got a chance to offer the actual correct one.
    Still opt-in via spotify_token (None to skip it entirely), same as
    soundcloud_token.

    Returns (found_cover_image, cover_source, returned_artist,
    returned_title) - the last three are None if nothing matched.
    """
    if not artist or not title:
        return None, None, None, None

    search_title, remix_qualified_title = compute_search_titles(title)

    for source, enabled in (
        ("itunes", USE_ITUNES),
        ("spotify", USE_SPOTIFY and bool(spotify_token)),
        ("soundcloud", USE_SOUNDCLOUD and not SOUNDCLOUD_RATE_LIMITED and not SOUNDCLOUD_UNAVAILABLE),
    ):
        if not enabled:
            continue
        match_result, cover_source = _search_one_source(
            source, artist, search_title, remix_qualified_title, soundcloud_token, spotify_token, log,
            on_itunes_rate_limited=on_itunes_rate_limited, on_spotify_rate_limited=on_spotify_rate_limited,
        )
        if match_result:
            found_cover_image, returned_artist, returned_title = match_result
            return found_cover_image, cover_source, returned_artist, returned_title

    return None, None, None, None


def search_cover_manual_with_tokens(
    artist, title, log=safe_print, on_auth_error=None,
    on_itunes_rate_limited=None, on_spotify_rate_limited=None,
):
    """
    Same as search_cover_manual(), but also fetches the SoundCloud/Spotify
    tokens itself - for a single ad-hoc search (e.g. the "fix Artist/Title
    and search again" flow after a scan finds nothing) that doesn't go
    through scan_files()'s full per-run setup.
    """
    soundcloud_token = None
    if USE_SOUNDCLOUD and SOUNDCLOUD_CLIENT_ID and SOUNDCLOUD_CLIENT_SECRET:
        soundcloud_token = get_soundcloud_token(log=log, on_auth_error=on_auth_error)

    spotify_token = None
    if USE_SPOTIFY and SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
        spotify_token = get_spotify_token(log=log, on_auth_error=on_auth_error, on_rate_limited=on_spotify_rate_limited)

    return search_cover_manual(
        artist, title, soundcloud_token, log=log, spotify_token=spotify_token,
        on_itunes_rate_limited=on_itunes_rate_limited, on_spotify_rate_limited=on_spotify_rate_limited,
    )


def _build_history_lookup():
    """
    Set of (absolute folder, new_file) pairs from every logged processing-
    history entry (history.jsonl) - computed once per scan/precheck rather
    than re-reading the whole file from disk once per track. Feeds
    _is_already_applied()'s authoritative check.
    """
    lookup = set()
    for entry in load_history_entries():
        folder = entry.get("folder")
        new_file = entry.get("new_file")
        if folder and new_file:
            lookup.add((os.path.abspath(folder), new_file))
    return lookup


def _is_already_applied(file_name, history_lookup):
    """
    Whether Track Tidy has actually applied this exact file before, per
    the processing history log (history.jsonl, see _build_history_lookup)
    - the authoritative signal, replacing an earlier guess based on the
    file's current has_cover/tags state. That heuristic had real, repeated
    false positives: a file downloaded pre-tagged from elsewhere
    (Beatport, a DJ pool...) can have a real cover and complete tags
    without ever having been searched/verified by this app at all -
    including, in one real report, a track whose existing "complete-
    looking" cover turned out to be a banned/generic placeholder. A
    history entry means this exact file, at this exact location, actually
    went through Apply before - nothing to infer.

    Shared by _prepare_scan (per-file, during a scan already underway)
    and find_already_applied_files (a precheck run BEFORE a scan starts,
    so interface.py can ask the user whether to bother rescanning these
    at all) so the two can never drift apart on what counts as "already
    applied".
    """
    return (os.path.abspath(MUSIC_FOLDER), file_name) in history_lookup


def find_already_applied_files(file_list):
    """
    Precheck: returns the subset of file_list Track Tidy has actually
    applied before (see _is_already_applied) - meant to run BEFORE a scan
    starts, so the caller can ask the user whether to rescan those too or
    skip them entirely. Now purely a history.jsonl lookup, no per-file
    tag reads needed at all.
    """
    history_lookup = _build_history_lookup()
    return [file_name for file_name in file_list if _is_already_applied(file_name, history_lookup)]


# --- Scan history ---

# Plain-text, append-only record of every file that has ever completed a
# scan (whether or not Apply was subsequently run on it) - distinct from
# HISTORY_FILE above, which only knows about files that were actually
# processed. Lets a later scan of the same folder tell "genuinely new file"
# apart from "already scanned before, just never applied" so the user can
# be offered to scan only the new ones. Must survive forever, same as
# ACTION_LOG_FILE/HISTORY_FILE - never deleted or truncated by this app,
# including across an update.
SCAN_HISTORY_FILE = os.path.join(user_config_dir(), "scan_history.txt")


def mark_file_scanned(file_name):
    """Appends one 'folder<TAB>file_name' line to SCAN_HISTORY_FILE."""
    try:
        with open(SCAN_HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(f"{os.path.abspath(MUSIC_FOLDER)}\t{file_name}\n")
    except Exception as error:
        print(f"  Could not write scan history entry: {error}")


def _build_scan_history_lookup():
    """Set of (absolute folder, file_name) pairs ever scanned before -
    computed once per precheck rather than re-reading the file per file."""
    lookup = set()
    if not os.path.exists(SCAN_HISTORY_FILE):
        return lookup
    try:
        with open(SCAN_HISTORY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                folder, _, file_name = line.rstrip("\n").partition("\t")
                if folder and file_name:
                    lookup.add((folder, file_name))
    except Exception:
        pass
    return lookup


def find_already_scanned_files(file_list):
    """Returns the subset of file_list that has already been scanned before
    in MUSIC_FOLDER, per SCAN_HISTORY_FILE - meant to run before a scan
    starts so the caller can offer to scan only the genuinely new files."""
    lookup = _build_scan_history_lookup()
    folder = os.path.abspath(MUSIC_FOLDER)
    return [file_name for file_name in file_list if (folder, file_name) in lookup]


def _prepare_scan(file_name, log=safe_print, on_new_mention=None, history_lookup=None):
    """
    Local-only part of analyzing a file (no network): reads tags, resolves
    artist/title, and detects mentions. Returns a dict with everything
    needed to both run the cover search and build the final info dict -
    split out so scan_files() can prepare every file up front (cheap)
    before the online search phase. history_lookup: see _build_history_
    lookup - built fresh here if not given (e.g. a caller other than
    scan_files, which always passes its own already-loaded one).
    """
    if history_lookup is None:
        history_lookup = _build_history_lookup()

    full_path = os.path.join(MUSIC_FOLDER, file_name)

    # Detect a "By Fuvi Clan" mention and report it as a SUGGESTION only.
    # It won't affect this file's title unless the user promotes it to "To remove".
    fuviclan_mention = detect_fuviclan_mention(file_name)
    if fuviclan_mention and on_new_mention:
        on_new_mention(fuviclan_mention)

    has_cover, current_artist, current_title, current_cover_bytes = read_current_info(full_path)
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
    if detected_title:
        search_title, remix_qualified_title = compute_search_titles(detected_title)
    if not detected_artist:
        # scan_files() skips iTunes/Spotify entirely without an artist to
        # search with - artist_sets_match() treats a blank expected artist
        # as trivially matching anything (an empty set is a subset of any
        # set), so searching those with no artist risks a confidently wrong
        # match. SoundCloud is still tried by title alone (see has_search_
        # title in scan_files) - its own match validation
        # (search_cover_soundcloud's artist_ok/swapped_ok/remixer_upload_ok)
        # never trivially passes on a blank artist, so it's safe. Real
        # report: "KIDS MGMT (KASSIN Remix) Final.wav" - no " - " separator
        # for parse_filename to split on, so this was skipping SoundCloud
        # too and losing a cover ("MGMT - KIDS (KASSIN Remix)") that a
        # direct title-only SoundCloud search finds without trouble.
        log(
            "  No artist could be determined from the filename or tags - skipping "
            "iTunes/Spotify search (correct the Artist/Title and search again for those)."
        )

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
        "acoustid_identified": False,  # set to True in place by _try_acoustid_correction, if it runs
        # See _is_already_applied - there's nothing a fresh online search
        # could improve on a file that's already fully tagged, so
        # scan_files() skips iTunes/Spotify/SoundCloud/AcoustID entirely
        # for it instead of re-spending quota re-confirming what's already
        # there (this is what "double scan" means in this codebase -
        # rescanning a folder that still has already-applied files sitting
        # in it).
        "already_applied": _is_already_applied(file_name, history_lookup),
    }


def _try_acoustid_correction(prepared, log=safe_print, on_rate_limited=None):
    """
    Last resort for a file the text-based search couldn't do anything with
    (nothing to search - an unparseable filename with no usable tags
    either - or a search that came up empty): identifies it from the
    actual audio content via AcoustID instead. On a confident match,
    updates `prepared`'s detected_artist/detected_title/search_title/
    remix_qualified_title in place - mirroring _prepare_scan's own
    computation of the latter two - so the corrected values also end up in
    the final scan result and can be searched again, not just used here.
    Returns True if a correction was applied, False otherwise (disabled,
    unavailable, or no confident match - the caller's original prepared
    values are left untouched).
    """
    if not USE_ACOUSTID_FALLBACK:
        return False

    full_path = os.path.join(MUSIC_FOLDER, prepared["file_name"])
    identified = identify_via_acoustid(full_path, log=log, on_rate_limited=on_rate_limited)
    if not identified:
        return False

    artist, title = identified
    prepared["detected_artist"] = artist
    prepared["detected_title"] = title
    prepared["search_title"], prepared["remix_qualified_title"] = compute_search_titles(title)
    # Marks this result as NOT filename/tag-derived - the UI mustn't
    # re-run resolve_artist_title() on it later (e.g. to pick up a "to
    # remove" mentions-list change mid-scan, see _add_scan_row), since
    # that would silently throw away the AcoustID identification and go
    # right back to the original unusable filename/tags.
    prepared["acoustid_identified"] = True
    return True


def _finish_scan(prepared, match_result, cover_source, log=safe_print):
    """
    Builds the final info dict for a file, given the (match_result,
    cover_source) pair its cover search ended up with.
    """
    file_name = prepared["file_name"]
    detected_artist = prepared["detected_artist"]
    detected_title = prepared["detected_title"]
    found_cover_image = None

    if match_result:
        found_cover_image, returned_artist, returned_title = match_result

        # Only try to fix a swap on the FILENAME-derived guess - the file's
        # own existing tags are trusted as-is and never rewritten this way,
        # and an AcoustID identification is already a confident, verified
        # result, not a raw guess to second-guess against the search result.
        if not prepared["tags_already_present"] and not prepared.get("acoustid_identified"):
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
        # WAV can be tagged either way, so its default follows the user's
        # global choices (see _resolve_conversion_target) - other non-MP3
        # formats have no such choice (can't be tagged without converting
        # at all), so they always default on. AIFF is the one exception:
        # it's already the taggable, lossless format WAV_TO_AIFF converts
        # WAV *into* - even with "Convert everything to MP3" on, a file
        # that's already AIFF shouldn't default to being downgraded to
        # lossy MP3 just because it happens to not be MP3 already. The
        # user can still check it manually if they really want that.
        "convert": (
            False
            if file_name.lower().endswith(".aiff")
            else _resolve_conversion_target(file_name) is not None
        ),
        # If the file's own tags are already complete, don't default to
        # overwriting them with a filename-derived guess that could be worse
        # (e.g. a truncated/garbled filename from some export tool).
        # An already_applied row starts unchecked on top of that - it has
        # nothing to gain from Apply rewriting the exact same tags/cover it
        # already has, unlike a plain "tags already present" row that just
        # never got a filename-derived guess to second-guess. A filename
        # flagged "unreleased" starts unchecked too - see
        # contains_unreleased_marker.
        # Exception: if detected_artist/detected_title actually differ from
        # what's currently tagged (e.g. a clean_title() normalization added
        # later, like "extended mix" -> "Extended Mix", now improves on a
        # tag written by an older version of the app), there IS something to
        # gain - stays checked even though already_applied, so the fix isn't
        # silently invisible on a file that was tagged before that rule
        # existed.
        "apply_changes": (
            bool(detected_title)
            and not contains_unreleased_marker(file_name)
            and (
                not prepared.get("already_applied", False)
                or detected_title != prepared["current_title"]
                or detected_artist != prepared["current_artist"]
            )
        ),
        "found_cover_image": found_cover_image,
        "cover_source": cover_source,
        "current_cover_bytes": prepared["current_cover_bytes"],
        "title_override": None,
        "artist_override": None,
        "processed": False,
        "final_path": None,
        # So the UI knows NOT to re-derive detected_artist/detected_title
        # from the filename/tags later (see interface.py's _add_scan_row) -
        # that would throw away a confident AcoustID identification and
        # fall right back to the original unusable filename/tags.
        "acoustid_identified": prepared.get("acoustid_identified", False),
        # See _prepare_scan's "already_applied" - marks a row that already
        # had a cover and complete tags before this scan even started
        # (search skipped entirely), so interface.py can show it
        # differently from a row this scan actually just processed.
        "already_applied": prepared.get("already_applied", False),
    }


SOUNDCLOUD_RATE_LIMITED = False  # set for the current run once a 429 is hit
SOUNDCLOUD_UNAVAILABLE = False  # set for the current run when no credentials are configured at all

# Same idea as SOUNDCLOUD_RATE_LIMITED above - set for the current scan
# once a search 429 is hit, checked before every subsequent Spotify
# search attempt, cleared again at the start of the next scan. Real
# report: a 100-track scan hit the search endpoint's own timed cooldown
# (_spotify_search_cooldown, 60s) re-expiring and re-tripping a FRESH 429
# over and over throughout the whole scan - the log's own comment already
# claimed "for the rest of this scan", but the cooldown alone only ever
# enforced 60s, not that. A scan this size takes far longer than 60s
# (iTunes/AcoustID alone easily push each file's turn past a minute), so
# the timed cooldown kept expiring mid-scan and letting a doomed retry
# through - two more wasted requests (main + title-only) every time,
# for zero successful matches once the real quota is genuinely
# exhausted. This flag makes the search actually stop for the rest of
# the scan on the first hit, like SoundCloud already does; the timed
# cooldown itself is untouched (still relevant to a call made outside
# scan_files, e.g. search_cover_manual's single "Fix no cover" retry).
SPOTIFY_SEARCH_RATE_LIMITED = False

def scan_files(file_list, on_file_scanned=None, log=safe_print, on_new_mention=None, on_rate_limited=None,
               should_cancel=None, on_auth_error=None, on_itunes_rate_limited=None, on_spotify_rate_limited=None,
               on_acoustid_rate_limited=None):
    """
    Scans ONLY the files in the given list (relative paths).
    Useful for an incremental scan (only reprocess new files).
    on_file_scanned(info) is called right after each file is analyzed,
    to allow a progressive display instead of waiting for the whole thing to finish.
    should_cancel() is checked before each file - if it returns True, the scan
    stops early and returns whatever was scanned so far.
    on_auth_error(source, message) is called if SoundCloud credentials are
    configured but wrong/expired (distinct from simply missing) - easy to
    miss buried in the log since it doesn't stop the scan.

    Every source is searched strictly sequentially, one file at a time -
    iTunes previously ran concurrently across files (up to 2 at once), but
    that's gone (per request): fewer requests in flight at once means a
    lower burst rate against every source's real rate limit, on top of
    the per-request pacing each one already gets (_itunes_throttle() and
    friends).
    """
    global SOUNDCLOUD_RATE_LIMITED, SOUNDCLOUD_UNAVAILABLE, SPOTIFY_SEARCH_RATE_LIMITED
    SOUNDCLOUD_RATE_LIMITED = False
    SOUNDCLOUD_UNAVAILABLE = False
    SPOTIFY_SEARCH_RATE_LIMITED = False

    if not file_list:
        return []

    # Phase 1: prepare every file (local-only: tags, filename parsing) -
    # fast, so should_cancel is checked cheaply between each one. Also
    # decides right away which files Track Tidy has actually already
    # applied before, per history.jsonl (see _prepare_scan's
    # "already_applied") - those need no online search at all, so this is
    # computed BEFORE authenticating with SoundCloud/Spotify below, to
    # skip that too when nothing in this batch actually needs it (e.g.
    # rescanning a folder that's already fully tagged from a previous
    # Apply). history_lookup is loaded once here rather than once per
    # file inside _prepare_scan.
    history_lookup = _build_history_lookup()
    prepared_list = []
    for file_name in file_list:
        if should_cancel and should_cancel():
            log("  Scan cancelled.")
            return []
        prepared_list.append(
            _prepare_scan(file_name, log=log, on_new_mention=on_new_mention, history_lookup=history_lookup)
        )

    needs_search = any(not prepared["already_applied"] for prepared in prepared_list)

    if not USE_SOUNDCLOUD:
        # Disabled in Settings - don't even try to authenticate.
        log("  [SoundCloud] Disabled in Settings - skipping SoundCloud for this scan.")
        SOUNDCLOUD_UNAVAILABLE = True
        soundcloud_token = None
    elif not needs_search:
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

        soundcloud_token = get_soundcloud_token(log=log, on_rate_limited=_mark_rate_limited, on_auth_error=on_auth_error)

    spotify_token = None
    if not needs_search:
        pass
    elif USE_SPOTIFY and SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
        spotify_token = get_spotify_token(log=log, on_auth_error=on_auth_error, on_rate_limited=on_spotify_rate_limited)
    elif USE_SPOTIFY:
        log("  [Spotify] No credentials configured - skipping Spotify for this scan.")

    # Phase 2: finish each file in its original order, strictly
    # sequentially - iTunes first, then (if it found nothing) Spotify,
    # then SoundCloud (see search_cover_manual for why Spotify - a
    # curated commercial catalog like iTunes - now comes before
    # SoundCloud - community-uploaded, far more prone to a wrong match).
    results = []
    for prepared in prepared_list:
        if should_cancel and should_cancel():
            log("  Scan cancelled.")
            break

        file_name = prepared["file_name"]

        if prepared["already_applied"]:
            log(f"  '{file_name}' already has a cover and tags - skipping online search.")
            info = _finish_scan(prepared, None, None, log)
            results.append(info)
            if on_file_scanned:
                on_file_scanned(info)
            continue

        match_result = None
        cover_source = None
        # A blank artist (e.g. a filename with no "Artist - Title"
        # separator to detect one from) no longer blocks iTunes/Spotify -
        # they're tried with just the title, same as SoundCloud already
        # did. artist_sets_match() treats an empty expected-artist set as
        # a match against anything, so validation for these effectively
        # falls back to requiring an EXACT title match alone - a real risk
        # of a false positive on a generic title, accepted as a tradeoff
        # for not missing an otherwise-findable track that just has no
        # artist to search with.
        has_query = bool(prepared["search_title"])
        has_search_title = has_query

        if USE_ITUNES and has_query:
            match_result, cover_source = _search_one_source(
                "itunes", prepared["detected_artist"], prepared["search_title"],
                prepared["remix_qualified_title"], None, None, log,
                on_itunes_rate_limited=on_itunes_rate_limited,
            )

        if (
            not match_result and has_query and USE_SPOTIFY and spotify_token
            and not SPOTIFY_SEARCH_RATE_LIMITED
        ):
            match_result, cover_source = _search_one_source(
                "spotify", prepared["detected_artist"], prepared["search_title"],
                prepared["remix_qualified_title"], None, spotify_token, log,
                on_spotify_rate_limited=on_spotify_rate_limited,
            )

        if (
            not match_result and has_search_title
            and USE_SOUNDCLOUD and not SOUNDCLOUD_RATE_LIMITED and not SOUNDCLOUD_UNAVAILABLE
        ):
            match_result, cover_source = _search_one_source(
                "soundcloud", prepared["detected_artist"], prepared["search_title"],
                prepared["remix_qualified_title"], soundcloud_token, None, log,
            )

        if not match_result and _try_acoustid_correction(prepared, log, on_rate_limited=on_acoustid_rate_limited):
            for source, enabled in (
                ("itunes", USE_ITUNES),
                ("spotify", USE_SPOTIFY and bool(spotify_token) and not SPOTIFY_SEARCH_RATE_LIMITED),
                ("soundcloud", USE_SOUNDCLOUD and not SOUNDCLOUD_RATE_LIMITED and not SOUNDCLOUD_UNAVAILABLE),
            ):
                if not enabled:
                    continue
                match_result, cover_source = _search_one_source(
                    source, prepared["detected_artist"], prepared["search_title"],
                    prepared["remix_qualified_title"], soundcloud_token, spotify_token, log,
                    on_itunes_rate_limited=on_itunes_rate_limited, on_spotify_rate_limited=on_spotify_rate_limited,
                )
                if match_result:
                    break

        info = _finish_scan(prepared, match_result, cover_source, log)
        results.append(info)
        if on_file_scanned:
            on_file_scanned(info)

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


GENERIC_QUALIFIER_MODIFIER_RE = re.compile(
    r"\b(?:extended|radio)\s+(?=(?:remix|edit|mix|bootleg|reboot|rework)\b)", re.IGNORECASE
)

# A short (<=6 char) parenthetical directly before the mix keyword - a
# country/region disambiguator a store tacks onto the remixer's own name
# (e.g. "Panna (BR) Remix", same "(BR)"/"(UK)" pattern split_artist_names()
# already strips from a plain artist field - see there for why) rather
# than a real part of the remix's name. Bounded short so an actual
# multi-word subtitle in parens isn't mistaken for one.
GROUP_DISAMBIGUATOR_RE = re.compile(
    r"\s*\([^()]{1,6}\)\s+(?=(?:remix|edit|mix|bootleg|reboot|rework)\b)", re.IGNORECASE
)


def strip_generic_qualifier_modifiers(text):
    """
    Drops a generic modifier word ("Extended"/"Radio") immediately before a
    remix/edit/mix keyword WITHIN a named qualifier (e.g. "Samm Extended
    Remix" -> "Samm Remix") - comparison purposes only, never written to a
    file's own tags. Unlike strip_generic_mix_suffix() (which only strips a
    qualifier that's PURELY generic, e.g. a bare "(Extended Mix)"), this
    reaches inside an otherwise-named one.

    "X Extended Remix" and "X Remix" commonly refer to the same underlying
    remix (just a longer mix length, often not even released as a
    separately credited version) - real report: our title "iLanga (Samm
    Extended Remix)" vs. Spotify's own "iLanga - Samm Remix" for the
    identical release. Scoped to only the word directly modifying the mix
    keyword, so it can't touch an actual name (e.g. "Extended" isn't
    dropped from "DJ Extended Vibes Remix" - it isn't immediately before
    "remix" there).

    Also drops a short parenthetical disambiguator directly before the
    keyword (see GROUP_DISAMBIGUATOR_RE) - e.g. "Panna (BR) Remix" ->
    "Panna Remix", real report: "Malandra Jr. - Pam Pam (Panna Extended
    Remix)" vs. Spotify's own "Pam Pam (Panna (BR) Remix)" for the
    identical release.
    """
    text = GENERIC_QUALIFIER_MODIFIER_RE.sub("", text)
    text = GROUP_DISAMBIGUATOR_RE.sub(" ", text)
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
    """
    Case/whitespace/accent-insensitive EXACT match (not the looser
    substring/word-based checks below). Also treats "Pt.III" and "Pt. III"
    (a space right after an abbreviating period) as the same - a store's
    listing and a filename/tag frequently disagree on that one space alone
    (e.g. "Pt. III" vs "Pt.III", "Vol. 2" vs "Vol.2"), which would otherwise
    reject an exact release over pure punctuation. Same treatment for a
    space right after a comma (e.g. "1,2,3,4" vs "1, 2, 3, 4") - real
    report: a numbered-title track rejected purely over that one space per
    comma. Trailing "!"/"?" are dropped too - real report: our title "What"
    vs. Spotify's own "WHAT!" - a store stylizing a title with emphasis
    punctuation our own filename/tags never bothered to include is a
    stylistic difference, not a different song. Also folds typographic
    ("smart") quotes to their plain ASCII equivalents - a store's listing
    commonly uses the curly apostrophe/quote (U+2019/U+2018/U+201C/U+201D)
    while a filename or ID3 tag almost always has the plain "'"/'"' -
    real report: "Love's A Game" (ours) vs. "Love’s A Game" (iTunes' own)
    rejected purely over that one character despite being visually
    identical, on a title with no other qualifier to blame.
    """
    QUOTE_TRANSLATION = str.maketrans("’‘“”", "''\"\"")

    def normalize(text):
        text = re.sub(r"\s+", " ", text.strip().lower())
        text = re.sub(r"\.\s+", ".", text)
        text = re.sub(r",\s+", ",", text)
        text = re.sub(r"[!?]+$", "", text).strip()
        text = text.translate(QUOTE_TRANSLATION)
        return strip_accents(text)
    return normalize(text_a) == normalize(text_b)


def strip_all_trailing_groups(text):
    """
    Repeatedly strips trailing "(...)"/"[...]" groups - e.g. a title with
    several stacked qualifiers like "Title (Subtitle) [feat. X] [Y Remix]" -
    returning (core_title, [group_1, group_2, ...]) with groups in the order
    they were stripped (rightmost/outermost first).

    Tolerates ONE level of nesting inside a group (e.g. "(Panna (BR)
    Remix)", a remixer credit with its own parenthetical disambiguator
    baked in) - real report: without this, the whole group failed to
    match at all (its content isn't parenthesis-free), silently leaving
    the ENTIRE trailing text un-stripped instead of extracting it as one
    group with nested content.
    """
    groups = []
    while True:
        match = re.search(r"\s*[\(\[]((?:[^()\[\]]|\([^()]*\)|\[[^\[\]]*\])*)[\)\]]\s*$", text)
        if not match:
            break
        groups.append(match.group(1).strip())
        text = text[:match.start()]
    return text.strip(), groups


def _qualifier_name_set(qualifier_text):
    """
    The set of person/artist names credited in a remix/edit qualifier (e.g.
    "Jean-Marc & Samson Remix" -> {"jean-marc", "samson"}) - strips the mix
    keyword itself first (remix/edit/mix/bootleg/reboot), then splits what's
    left the same way any other multi-artist field is split (ignoring
    separator style - "X, Y Remix" and "X & Y Remix" credit the same people).
    """
    stripped = re.sub(r"\b(?:remix|edit|mix|bootleg|reboot|rework)\b", "", qualifier_text, flags=re.IGNORECASE)
    return split_artist_names(stripped)


def _qualifier_names_already_expected(qualifier_text, expected_artist):
    """
    Whether every name mentioned in a remix/edit qualifier (e.g. "Jean-Marc
    & Samson Remix") is already among the expected artist credits for this
    track. False if nothing's left to check (a purely generic qualifier
    like "(Extended Remix)" would otherwise vacuously "pass" - an empty
    qualifier name set is never treated as a match).
    """
    qualifier_names = _qualifier_name_set(qualifier_text)
    if not qualifier_names:
        return False
    return qualifier_names <= split_artist_names(expected_artist)


def loose_remix_match(expected_title, returned_title, expected_artist=None):
    """
    Fallback for a specific remix rejected by the strict exact-match check
    because the store's listing has extra bracket groups ours doesn't know
    about (e.g. a subtitle, or "feat. X" positioned before the remix
    bracket instead of at the very end, so strip_feature_suffix() can't
    reach it). Accepts it anyway if the core title matches and EITHER:
    - our specific remix qualifier is one of the store's bracket groups
      verbatim (case/whitespace-insensitive) - deliberately stricter than a
      generic fuzzy match, since this is only meant to recognize the SAME
      named remix, not just "some remix of the same song"; OR
    - expected_artist is given, and one of the store's bracket groups names
      a remixer/editor who's already among our own expected artist credits
      (see _qualifier_names_already_expected) - covers a store crediting
      the remix by the actual remixer's name (e.g. "Jean-Marc & Samson
      Remix") where our own filename only had a generic qualifier (e.g.
      "Extended Remix") for the same release, real report: "Marshall
      Jefferson, Samson, Maesic, Jean-Marc, Salomé Das - Life Is Simple
      (Extended Remix)" vs. Spotify's own "Life Is Simple (Move Your Body)
      [...] [Jean-Marc & Samson Remix]" - Jean-Marc and Samson are both
      already in our own artist credit, just not called out by name in
      OUR qualifier; OR
    - a qualifier on each side names the exact same set of people, just
      with a different separator style (see _qualifier_name_set - "X, Y
      Remix" and "X & Y Remix" credit the same two remixers) - real
      report: "Bedouin - Better Than This (Dorian Craft, Baron Remix)"
      vs. iTunes's own "Better Than This (Dorian Craft & Baron Remix)".

    A title with NO groups of its own (expected_groups empty - e.g. a
    bare "Gypsy Woman" once a purely generic "(Extended Mix)" qualifier
    is stripped for search, nothing specific left to compare against) is
    accepted outright once the core matches - there's nothing left for
    the store's own extra groups (a subtitle, a hook, a specifically-
    named official remix credit) to conflict with. Real report: "Crystal
    Waters - Gypsy Woman (Extended Mix)" vs. the store's actual "Gypsy
    Woman (She's Homeless) (La Da Dee La Da Da) [Basement Boy Strip To
    The Bone Mix]" - three extra groups a plain "(Extended Mix)" simply
    has no specific information to accept or reject.
    """
    expected_core, expected_groups = strip_all_trailing_groups(expected_title)
    returned_core, returned_groups = strip_all_trailing_groups(returned_title)

    if not exact_match(expected_core, returned_core):
        return False

    if not expected_groups:
        return True

    def normalize_group(text):
        text = strip_generic_qualifier_modifiers(text)
        return re.sub(r"\s+", " ", text.strip().lower())

    expected_set = {normalize_group(g) for g in expected_groups}
    returned_set = {normalize_group(g) for g in returned_groups}
    if expected_set & returned_set:
        return True

    for expected_group in expected_groups:
        expected_names = _qualifier_name_set(expected_group)
        if not expected_names:
            continue
        for returned_group in returned_groups:
            if expected_names == _qualifier_name_set(returned_group):
                return True

    if expected_artist:
        for group in returned_groups:
            if is_named_remix_qualifier(group) and _qualifier_names_already_expected(group, expected_artist):
                return True

    return False


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
    """Splits a multi-artist string on any common separator (,  &  x  X  vs  feat.  ft.  and).

    "feat"/"ft" use \\b...\\b instead of a trailing \\b after the optional
    "."- a \\b right after a "." that's followed by whitespace is never a
    real word boundary (neither side is a word character), so \\bfeat\\.?\\b
    silently fails to consume the period, leaving a stray ". name" fragment
    behind instead of splitting cleanly (e.g. "Alonzo Feat. Tiakola" ->
    {"alonzo", ". tiakola"} instead of {"alonzo", "tiakola"}) - found via a
    real AcoustID-identified artist string ("Alonzo Feat. Tiakola") being
    rejected as "not the same artist" as a store's own "Alonzo, Tiakola".
    """
    parts = re.split(r"\s*(?:,|&|/|\bx\b|\bvs\b|\bfeat\b\.?|\bft\b\.?|\band\b)\s*", text, flags=re.IGNORECASE)
    names = set()
    for part in parts:
        # Strip a trailing parenthetical disambiguator (e.g. "SOMMERS (UK)"
        # -> "SOMMERS") - some stores add these to tell apart two different
        # artists who happen to share an exact name on their platform; our
        # own filenames/tags never include it, so comparing with it left in
        # would reject an otherwise-exact match. Real report: "Moeaike,
        # SOMMERS" (filename) vs. Spotify's own "Moeaike, SOMMERS (UK)".
        part = re.sub(r"\s*\([^()]*\)\s*$", "", part)
        normalized = strip_accents(strip_sanitized_chars(part.strip().lower()))
        # A trailing period on an abbreviation-style name (e.g. "D.O.D.")
        # is sometimes dropped by a store ("D.O.D") - real report:
        # "Cesar De Melero, D.O.D." (filename) vs. iTunes's own "Cesar de
        # Melero & D.O.D", rejected purely over that one trailing period.
        # significant_words() also can't bridge this via the fuzzy
        # fallback below: an abbreviation like "d.o.d" splits into single-
        # character words, all below its 3-char significance threshold.
        normalized = normalized.rstrip(".")
        if not normalized:
            continue
        # A band's own name is sometimes credited with a leading "The" and
        # sometimes without (e.g. "The Black Eyed Peas" vs. iTunes's own
        # "Black Eyed Peas") - real report: that mismatch alone rejected an
        # otherwise-correct iTunes match, falling through to a much less
        # reliable SoundCloud search that then matched an unrelated upload.
        names.add(re.sub(r"^the\s+", "", normalized))
    return names


def _artist_name_words_subset(name_a, name_b):
    """
    Looser, per-NAME fallback for a single pair of artist names that don't
    match verbatim - whether name_a's significant (>=3 char) words are
    (mostly) contained in name_b's. Bridges an aliasing/embellishment
    difference where one side adds something the other doesn't credit at
    all - real report: a filename credited "Dave Lee ZR" (a DJ's own
    handle) for a track Spotify officially credits "Dave 'Love' Lee" -
    same person, but neither string is a substring of the other, and a
    plain word-set comparison of the FULL multi-artist strings never gets
    to compare these two names against each other in isolation.

    Requires at least 2 shared significant words, all of them from the
    shorter name (not just one) - a single shared word (e.g. matching
    "Alex Turner" to an unrelated "Alex Baker" on "alex" alone) isn't
    enough evidence these are the same artist.
    """
    words_a = significant_words(name_a)
    words_b = significant_words(name_b)
    if len(words_a) < 2 or len(words_b) < 2:
        return False
    shorter, longer = (words_a, words_b) if len(words_a) <= len(words_b) else (words_b, words_a)
    return shorter <= longer


def artist_sets_match(expected_artist, returned_artist, returned_title=""):
    """
    Matches a list of artists, ignoring order and separator style (e.g.
    "A, B, C" vs "C & A x B" are considered the same set of artists). If
    returned_title is given, a featured artist credited only there (e.g.
    "Title (feat. X)") is folded into the returned artist set too - some
    stores split primary vs. featured artist across the two fields, while
    our own tags/filename usually list everyone in the artist field.

    Accepts either set being a SUBSET of the other rather than requiring an
    exact match - a collective/label name (e.g. "Keinemusik") is sometimes
    listed as one of the artists alongside its own individual members, while
    a specific release's own credit only lists the members (or vice versa).
    Rejecting that as a mismatch let a legitimate single lose out to an
    unrelated compilation whose artist field happened to list every name
    verbatim, including the collective's.

    Falls back to a looser per-name word-overlap comparison
    (_artist_name_words_subset) when the strict, verbatim version finds no
    match at all - catches an aliasing difference on an individual artist
    name (handle vs. official credit) that a whole-string comparison can
    never bridge. Only tried as a fallback, and only once no name in
    either side matches verbatim, to keep the common case exactly as
    strict as before.
    """
    returned_names = split_artist_names(returned_artist) | extract_feature_names(returned_title)
    expected_names = split_artist_names(expected_artist)

    if expected_names <= returned_names or returned_names <= expected_names:
        return True

    return (
        all(any(_artist_name_words_subset(e, r) for r in returned_names) for e in expected_names)
        or all(any(_artist_name_words_subset(r, e) for e in expected_names) for r in returned_names)
    )


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


def significant_words(text):
    """Lowercased word set, ignoring short (<3 char) filler words like "a"/"ft"/"the"."""
    return {w for w in re.findall(r"\w+", text.lower()) if len(w) >= 3}


def title_words_overlap(expected_title, returned_title):
    """
    Checks that the returned title shares at least one meaningful word with the
    expected one, to reject a DIFFERENT song by the same (correct) artist
    (e.g. matching "Saint Laurent" when looking for "Je La Connais").
    """
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

class _SourceCooldown:
    """
    Tracks "skip this source until timestamp X" for a rate-limited API
    endpoint - shared by every source that can hit a 429/quota-exceeded
    error mid-scan and needs to stop retrying it for the rest of THIS scan
    instead of hammering it again, one wasted request at a time, on every
    remaining file (iTunes, Spotify's token AND search endpoints,
    SoundCloud's token endpoint, AcoustID). These were five separately
    hand-copied instances of the exact same "module-level 'until'
    timestamp + check-before-request + set-on-429" pattern, one added at a
    time as each source's own rate-limit handling was built - consolidated
    here instead of leaving a sixth near-copy for the next one.
    """

    def __init__(self):
        self.until = 0

    def active(self):
        return time.time() < self.until

    def trigger(self, cooldown_seconds):
        self.until = time.time() + cooldown_seconds


class _SourceThrottle:
    """
    Enforces a minimum interval between consecutive requests to one API
    endpoint - shared by every source with its own proactive per-request
    pacing (iTunes, Spotify, SoundCloud, AcoustID). These were four
    separately hand-copied instances of the exact same "lock + last-
    request timestamp + sleep the difference" pattern, one added at a time
    as each source's own "rate limited too often" report came in -
    consolidated here instead of leaving a fifth near-copy for the next
    one. Callable directly (like the plain functions it replaces) so every
    call site stays unchanged.
    """

    def __init__(self, min_interval_seconds):
        self.min_interval_seconds = min_interval_seconds
        self.lock = threading.Lock()
        self.last_request_time = 0.0

    def __call__(self):
        with self.lock:
            wait_seconds = self.last_request_time + self.min_interval_seconds - time.time()
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            self.last_request_time = time.time()


def build_search_query(artist, title):
    """
    Builds a search term from artist+title, replacing punctuation (commas,
    parentheses/brackets) with spaces instead of leaving it in the query as
    literal characters - iTunes' relevance ranking seems to penalize that
    punctuation. A comma-separated multi-artist string or a parenthesized
    remix name can bury the exact version we want under a heap of
    same-song alternates, even when it's genuinely in the results.

    A short (<=6 char) parenthesized group - almost always an artist
    disambiguator (e.g. "Trace (UZ)", "Murphy's Law (UK)", same kind
    split_artist_names() already strips before COMPARING artist names,
    see its own docstring) - is dropped ENTIRELY rather than just having
    its parens turned to spaces, unlike everything else here. Real
    report: "Trace (UZ) - G.L.A.M" returned zero relevant iTunes results
    at all - the literal "UZ" search term fuzzy-matched to unrelated
    artists ("Lil Uzi Vert", "U2") and derailed ranking completely, even
    though "Trace G.L.A.M" alone finds the exact right track as the #1
    result. A real remix/edit qualifier (kept, just de-punctuated below)
    is essentially never this short - "(Mix)"/"(Edit)" are the rare
    exceptions, and losing a single generic word like that from the
    query costs nothing.
    """
    combined = re.sub(r"\([^()]{1,6}\)", " ", f"{artist} {title}")
    cleaned = re.sub(r"[,()\[\]]", " ", combined)
    return re.sub(r"\s+", " ", cleaned).strip()


# Shared across every iTunes call within a scan - a large batch (dozens of
# files) can trip a real HTTP 429 despite each individual call's own
# retry/backoff, since a plain sequential run (plus later AcoustID-
# triggered re-searches) still retries independently with no awareness
# that iTunes is already telling everyone to back off. Once that happens,
# every further iTunes call in this same scan is skipped outright for a
# cooldown period instead of piling on more doomed requests - they fall
# through to SoundCloud/AcoustID immediately, same as if iTunes just
# wasn't enabled for those files.
ITUNES_RATE_LIMIT_COOLDOWN_SECONDS = 30
_itunes_cooldown = _SourceCooldown()

# Paces every iTunes request to stay under iTunes' real, undocumented
# per-IP limit instead of just reacting after the fact with the 30s
# cooldown above - each track can trigger more than one search (normal +
# remix retry + AcoustID-corrected retry), which could burn through the
# limit within the first several files of a scan, tripping
# ITUNES_RATE_LIMIT_COOLDOWN and losing iTunes for everything after. 1.5s
# apart keeps every request under ~40/min, which real scans no longer trip.
ITUNES_MIN_REQUEST_INTERVAL_SECONDS = 1.5
_itunes_throttle = _SourceThrottle(ITUNES_MIN_REQUEST_INTERVAL_SECONDS)


# iTunes' fixed "various artists" credit on a compilation's collection, in
# whichever storefront locale search_cover_itunes() queries (hardcoded to
# "FR" below) - not a per-compilation translation, so this is safe to match
# literally rather than needing a keyword search.
ITUNES_VARIOUS_ARTISTS_CREDITS = ("Multi-interprètes",)

# A "(DJ Mix)" collection is a radio show/mixtape recording (e.g. "Experts
# Only Radio 043 (DJ Mix)", "Pat Lok: Parallel Motion (DJ Mix)") that
# happens to also list one of its individual tracks as its own searchable
# result - its "cover" is the SHOW's own artwork, not the track's real
# single/album cover. iTunes appends this exact marker in English
# regardless of storefront locale (confirmed against the FR store), same
# as ITUNES_VARIOUS_ARTISTS_CREDITS above. Sometimes bracketed instead of
# parenthesized (e.g. "Solardo at [UNVRS]: Aug 14, 2025 [DJ Mix] [DJ
# Mix]", real report: "Amine Edge & DANCE - Halfway Crooks" picked up
# that set's cover instead of its own release, since a plain "(dj mix)"
# substring check doesn't match the "[DJ Mix]" bracketed form) - matched
# with either bracket style below rather than a literal substring.
ITUNES_DJ_MIX_COLLECTION_MARKER_RE = re.compile(r"[\(\[]dj mix[\)\]]")


def search_cover_itunes(artist, title, log=safe_print, max_retries=2, allow_loose_remix_match=False, on_rate_limited=None):
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
    if _itunes_cooldown.active():
        log("  [iTunes] Still rate limited from earlier in this scan - skipping.")
        return None

    try:
        for attempt in range(max_retries + 1):
            _itunes_throttle()
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

        if response.status_code == 429:
            _itunes_cooldown.trigger(ITUNES_RATE_LIMIT_COOLDOWN_SECONDS)
            log(
                f"  [iTunes] Still rate limited after {max_retries} retries - pausing iTunes for "
                f"{ITUNES_RATE_LIMIT_COOLDOWN_SECONDS}s for the rest of this scan."
            )
            if on_rate_limited:
                on_rate_limited()
            return None

        if response.status_code != 200:
            log(f"  [iTunes] Search failed: HTTP {response.status_code} - {response.text[:300]}")
            return None

        results = response.json().get("results", [])
        if not results:
            log(f"  [iTunes] No result at all for '{artist} - {title}'")
            return None

        title_normalized = strip_feature_suffix(strip_generic_qualifier_modifiers(strip_generic_mix_suffix(title)))

        for result in results:
            # iTunes sometimes returns accented text in NFD form (e.g. "a" + a
            # combining accent, instead of the precomposed "à"), which looks
            # identical when printed but breaks both string comparison AND
            # printing on a Windows console (cp1252 can't encode combining
            # accents on their own). Normalize to NFC to match how tags/filenames
            # are represented.
            returned_artist = unicodedata.normalize("NFC", result.get("artistName", ""))
            returned_title = unicodedata.normalize("NFC", result.get("trackName", ""))
            # A store crediting a named remix as "Title - X Remix" (dash)
            # instead of our own "Title (X Remix)" convention would
            # otherwise never exact_match, even for the exact right track -
            # reformat_trailing_dash_mix() is the same normalization
            # resolve_artist_title() already applies to OUR OWN file's
            # title for this exact reason. strip_generic_qualifier_modifiers()
            # further drops a droppable "Extended"/"Radio" modifier on both
            # sides (see its docstring). strip_slash_credit() drops an
            # inline "/ Name" original-artist credit some stores insert
            # right into the title (see its own docstring) - applied only
            # to the TITLE comparison, not to returned_title itself, since
            # the raw text is still useful as an artist-match signal (a
            # credited name showing up anywhere in the title) and for
            # logging.
            returned_title_for_title_match = strip_slash_credit(returned_title)
            returned_title_normalized = strip_feature_suffix(
                strip_generic_qualifier_modifiers(
                    strip_generic_mix_suffix(reformat_trailing_dash_mix(returned_title_for_title_match))
                )
            )

            artist_ok = artist_sets_match(artist, returned_artist, returned_title) and exact_match(title_normalized, returned_title_normalized)
            swapped_ok = exact_match(title, returned_artist) and artist_sets_match(artist, returned_title_normalized)

            loose_ok = False
            if (
                not (artist_ok or swapped_ok) and allow_loose_remix_match
                and loose_remix_match(title, returned_title_for_title_match, expected_artist=artist)
            ):
                _, returned_groups = strip_all_trailing_groups(returned_title_for_title_match)
                returned_artist_set = split_artist_names(returned_artist) | extract_feature_names_from_groups(returned_groups)
                loose_ok = split_artist_names(artist) <= returned_artist_set

            if not (artist_ok or swapped_ok or loose_ok):
                continue

            if result.get("collectionArtistName") in ITUNES_VARIOUS_ARTISTS_CREDITS:
                log(
                    f"  [iTunes] Match found for '{artist} - {title}' but it's only on a "
                    f"various-artists compilation ('{result.get('collectionName')}') - skipping."
                )
                continue

            collection_name = result.get("collectionName") or ""
            if ITUNES_DJ_MIX_COLLECTION_MARKER_RE.search(collection_name.lower()):
                # Real report: "Roxe - You Do Change (Extended Mix)" -
                # picked up the artwork of "Experts Only Radio 043 (DJ
                # Mix)" (a John Summit radio show that happens to include
                # this track) instead of the track's own "You Do Change -
                # Single" release, which was also in the results but
                # ranked lower.
                log(
                    f"  [iTunes] Match found for '{artist} - {title}' but it's only on a DJ mix/radio "
                    f"show recording ('{collection_name}'), not the track's own release - skipping."
                )
                continue

            cover_url = result.get("artworkUrl100")
            if not cover_url:
                log(f"  [iTunes] Match found for '{artist} - {title}' but it has no artwork URL.")
                continue

            cover_url_hd = cover_url.replace("100x100", "600x600")
            image_response = requests.get(cover_url_hd, timeout=10)

            if image_response.status_code == 200:
                if is_banned_cover_image(image_response.content):
                    log(f"  [iTunes] Match found for '{artist} - {title}' but its cover is a known placeholder - skipping.")
                    continue
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
    save_setting("soundcloud_token", None)
    save_setting("soundcloud_token_expiry", 0)


# Unlike iTunes/Spotify, a 429 from SoundCloud's token endpoint carries no
# Retry-After header or any other signal of how long to wait (checked
# directly against the live endpoint while it was rate limited - nothing
# useful in the response headers or body beyond {"error":
# "rate_limit_exceeded"}) - this cooldown is a self-imposed estimate, not a
# documented number, chosen longer than ITUNES_RATE_LIMIT_COOLDOWN_SECONDS
# since an OAuth client_credentials endpoint is typically budgeted more
# coarsely (e.g. per-hour) than a plain search API. Without it, every new
# scan/health-check would immediately retry the token endpoint and could
# keep extending the block instead of letting it clear.
SOUNDCLOUD_TOKEN_RATE_LIMIT_COOLDOWN_SECONDS = 300
_soundcloud_token_cooldown = _SourceCooldown()


def get_soundcloud_token(log=safe_print, on_rate_limited=None, on_auth_error=None):
    global _cached_soundcloud_token, _cached_token_expiry

    # Reuse the cached token if it's still valid (with a 60s safety margin)
    if _cached_soundcloud_token and time.time() < _cached_token_expiry - 60:
        return _cached_soundcloud_token

    # The in-memory cache above is lost on every app restart, which used to
    # mean a brand new token (and a bite out of SoundCloud's tight 50/12h
    # per-app, 30/hour per-IP token quota - see SOUNDCLOUD_TOKEN_RATE_LIMIT_COOLDOWN_SECONDS
    # above) was requested every time the app launched, even if the previous
    # token (usually valid ~1h) hadn't actually expired yet. Persisting it to
    # settings.json lets a fresh process pick up where the last one left off.
    persisted_settings = load_settings()
    persisted_token = persisted_settings.get("soundcloud_token")
    persisted_expiry = persisted_settings.get("soundcloud_token_expiry", 0)
    if persisted_token and time.time() < persisted_expiry - 60:
        _cached_soundcloud_token = persisted_token
        _cached_token_expiry = persisted_expiry
        return _cached_soundcloud_token

    if _soundcloud_token_cooldown.active():
        log("  [SoundCloud] Still rate limited from earlier - skipping the token request.")
        return None

    if not SOUNDCLOUD_CLIENT_ID or not SOUNDCLOUD_CLIENT_SECRET:
        log("  [SoundCloud] No credentials found (clientID.txt / clientSecret.txt missing or empty).")
        return None

    response = _request_soundcloud_token(SOUNDCLOUD_CLIENT_ID, SOUNDCLOUD_CLIENT_SECRET, log)
    if response is None:
        return None

    if response.status_code == 429 and SOUNDCLOUD_FALLBACK_CLIENT_ID and SOUNDCLOUD_FALLBACK_CLIENT_SECRET:
        # Dev-only convenience (see id_2.txt above) - a separate app's
        # credentials, so it has its own, independent 50/12h token budget.
        log("  [SoundCloud] Primary app rate limited - retrying with id_2.txt's fallback credentials (dev only)...")
        response = _request_soundcloud_token(SOUNDCLOUD_FALLBACK_CLIENT_ID, SOUNDCLOUD_FALLBACK_CLIENT_SECRET, log)
        if response is None:
            return None

    if response.status_code == 200:
        payload = response.json()
        _cached_soundcloud_token = payload.get("access_token")
        _cached_token_expiry = time.time() + payload.get("expires_in", 3600)
        save_setting("soundcloud_token", _cached_soundcloud_token)
        save_setting("soundcloud_token_expiry", _cached_token_expiry)
        return _cached_soundcloud_token

    if response.status_code == 429:
        _soundcloud_token_cooldown.trigger(SOUNDCLOUD_TOKEN_RATE_LIMIT_COOLDOWN_SECONDS)
        log(
            "  [SoundCloud] Rate limit reached (too many token requests) - pausing "
            f"SoundCloud for {SOUNDCLOUD_TOKEN_RATE_LIMIT_COOLDOWN_SECONDS}s."
        )
        if on_rate_limited:
            on_rate_limited()
    else:
        message = f"HTTP {response.status_code} - {response.text[:300]}"
        log(f"  [SoundCloud] Authentication error: {message}")
        # Distinct from "no credentials" above - these ARE configured,
        # they're just wrong/expired (e.g. a revoked client). Easy to
        # miss buried in the log since it doesn't stop the scan.
        if on_auth_error:
            on_auth_error("SoundCloud", message)
    return None


def _request_soundcloud_token(client_id, client_secret, log):
    """Raw client_credentials token request for one SoundCloud app - returns
    the `requests.Response`, or None if the request itself errored out
    (network issue etc., as opposed to an HTTP error status)."""
    try:
        credentials = f"{client_id}:{client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        return requests.post(
            "https://secure.soundcloud.com/oauth/token",
            headers={
                "Authorization": f"Basic {encoded_credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
            timeout=10,
        )
    except Exception as error:
        log(f"  [SoundCloud] Error during authentication: {error}")
        return None


UNSOLICITED_EDIT_MARKERS = ("slowed", "reverb", "sped up", "speed up", "nightcore", "8d audio")


def has_unsolicited_edit_marker(text):
    """
    True if text carries a tell-tale fan-edit marker (e.g. "slowed +
    reverb", "sped up", "nightcore") - SoundCloud is full of these,
    uploaded by a third party with no connection to the real artist, whose
    title often just prepends/appends the marker to the real artist/title
    (e.g. "1997 - Aqua - Barbie Girl [slowed + reverb]" by an unrelated
    uploader). artist_names_match()'s substring check against a TITLE
    (meant for a legit repost formatted as "Artist - Title") can't tell
    that apart from the real thing on its own, so this catches it
    separately - see its use in search_cover_soundcloud().
    """
    lowered = text.lower()
    return any(marker in lowered for marker in UNSOLICITED_EDIT_MARKERS)


MIX_VARIANT_KEYWORDS = ("edit", "reboot", "bootleg", "mashup", "flip", "rework", "vip", "mix", "cover")
# "remix" alone skips the LEADING \b - a remixer's name is often glued
# directly onto it with no separator ("GYPZEREMIX", "Lustyremix"), which a
# leading \b would silently miss (no boundary between two word chars). The
# trailing \b (nothing glued after it, e.g. before ")"/end-of-string) is
# kept, so this doesn't swallow an unrelated longer word.
MIX_VARIANT_RE = re.compile(
    r"remix\b|\b(?:" + "|".join(MIX_VARIANT_KEYWORDS) + r")\b", re.IGNORECASE,
)


def looks_like_mix_variant(text):
    """
    True if text names some kind of remix/edit/mix variant (generic like
    "Mix" or named like "DJ Name Remix") ANYWHERE in it, not just in a
    trailing "(...)"/"[...]" group - a messy DJ-mashup-style SoundCloud
    title can bury it mid-string (e.g. "Soolking ft Sif Lssane(Hkayne)
    Remix DeejayTM Ca.Va.Pas / Mi Amigo") rather than cleanly at the end.
    Word-boundary matched so it doesn't fire on an unrelated word merely
    containing one of these as a substring (e.g. "Remixed" doesn't match
    "\\bmix\\b"). Used to reject a SoundCloud candidate carrying a variant
    we never asked for (see its use in search_cover_soundcloud) without
    also rejecting a harmless descriptive tag like "(Official Audio)".
    """
    return bool(MIX_VARIANT_RE.search(text))


# Paces every SoundCloud search request the same way _itunes_throttle()/
# _spotify_throttle() pace their own sources (see there) - SoundCloud is
# tried for every file iTunes AND Spotify (when reached) didn't already
# resolve, and unlike those two this endpoint had no pacing at all before,
# only a reactive cooldown on the separate TOKEN endpoint. 1.5s apart,
# matching the other two, to stay under SoundCloud's real per-app limit
# instead of only reacting after a 429 already happened.
SOUNDCLOUD_MIN_REQUEST_INTERVAL_SECONDS = 1.5
_soundcloud_throttle = _SourceThrottle(SOUNDCLOUD_MIN_REQUEST_INTERVAL_SECONDS)


def search_cover_soundcloud(artist, title, token, log=safe_print):
    """
    Checks up to 10 candidates, not just the top one - SoundCloud's
    relevance ranking doesn't always put the exact upload we want first
    (e.g. a specific named bootleg can easily rank behind more generic
    uploads of the same base song), same reasoning as search_cover_itunes().
    """
    if not token:
        return None

    _soundcloud_throttle()

    try:
        response = requests.get(
            "https://api.soundcloud.com/tracks",
            headers={"Authorization": f"OAuth {token}"},
            params={"q": f"{artist} {title}", "limit": 10},
            timeout=10,
        )

        if response.status_code != 200:
            log(f"  [SoundCloud] Search failed: HTTP {response.status_code} - {response.text[:300]}")
            return None

        results = response.json()
        if not results:
            log(f"  [SoundCloud] No result at all for '{artist} - {title}'")
            return None

        qualifier_words = named_qualifier_name_words(title)

        for result in results:
            # Same NFD/NFC issue as on the iTunes side - normalize before any
            # comparison or logging.
            track_title = unicodedata.normalize("NFC", result.get("title", ""))
            uploader_name = unicodedata.normalize("NFC", result.get("user", {}).get("username", ""))

            artist_ok = (
                artist_names_match(artist, track_title) or artist_names_match(artist, uploader_name)
            ) and title_words_overlap(title, track_title)

            # Requires a real (non-blank) artist - it exists to catch OUR
            # OWN artist/title fields being swapped (e.g. the filename put
            # the title where the artist should be), which is meaningless
            # with no artist at all to have been swapped in the first
            # place. Without this guard, a blank artist made
            # title_words_overlap(artist, track_title) trivially True (see
            # its own "nothing meaningful to compare against" shortcut),
            # collapsing swapped_ok down to just artist_names_match(title,
            # track_title) - a bare substring check against the ENTIRE raw
            # title, loose enough to false-positive on an unrelated upload
            # for a file with only a title tag and no artist (e.g. a bare
            # "Titre.mp3" with nothing else to go on). Real report: too
            # many wrong matches on exactly this kind of no-artist file.
            swapped_ok = bool(artist) and (
                artist_names_match(title, track_title) or artist_names_match(title, uploader_name)
            ) and title_words_overlap(artist, track_title)

            # A remix upload often credits only the remixer, never
            # repeating the original artist's name anywhere in its own
            # title or the uploader's username - real report: SoundCloud
            # upload titled just "MILLION DOLLAR BABY (YUMA REMIX)" by
            # uploader "YUMA", searching for "TOMMY RICHMAN - Million
            # Dollar Baby (YUMA Remix)" - "Tommy Richman" appears nowhere
            # in either, so artist_ok/swapped_ok both fail despite this
            # clearly being the right, correctly-covered upload. Accepted
            # instead when the base title still overlaps AND the
            # uploader's own username IS the specific remixer being
            # searched for - a strong, narrow signal (only even checked
            # when a named remix was actually asked for) that this is the
            # remixer's own upload of exactly this remix.
            remixer_upload_ok = (
                bool(qualifier_words)
                and title_words_overlap(title, track_title)
                and bool(qualifier_words & significant_words(uploader_name))
            )

            if not (artist_ok or swapped_ok or remixer_upload_ok):
                continue

            # artist_names_match() above only requires ONE of several expected
            # artists to show up (loose, to tolerate a store crediting a
            # featured artist differently) - fine for a single-artist search,
            # but for a multi-artist one it let a DIFFERENT, unrelated release
            # by just one of the expected artists win over the real match.
            # Real report: searching "Toman, Bad Bunny - Verano En NY
            # (Extended Mix)" matched a SoundCloud upload titled just "Toman -
            # Verano En NY (Extended Mix) [Solid Grooves]" (Toman's own solo
            # original, wrong cover) instead of falling through to the actual
            # Toman+Bad Bunny mashup further down the results, because "Toman"
            # alone was enough to satisfy artist_ok. Skipped for
            # remixer_upload_ok (see its own docstring - the original
            # artist(s) are expected to be absent there on purpose).
            if (artist_ok or swapped_ok) and not remixer_upload_ok:
                expected_artist_names = split_artist_names(artist)
                if len(expected_artist_names) > 1:
                    candidate_words = significant_words(f"{track_title} {uploader_name}")
                    if any(not (significant_words(name) & candidate_words) for name in expected_artist_names):
                        continue

            # Reject an unsolicited fan edit (see has_unsolicited_edit_marker) -
            # unless we actually asked for one ourselves, in which case it's
            # not "unsolicited" at all.
            if (
                has_unsolicited_edit_marker(f"{track_title} {uploader_name}")
                and not has_unsolicited_edit_marker(title)
            ):
                continue

            # Reject a candidate whose OWN title carries a remix/edit/mix
            # variant tag (generic or named) we never asked for - real
            # report: searching for the plain "Mi Amigo" matched a random
            # account's "Soolking - Mi Amigo (Remix)" upload (wrong cover)
            # instead of falling through to a plain, correctly-covered
            # upload further down the results. A harmless descriptive tag
            # like "(Official Audio)"/"(HQ)" is deliberately left alone
            # (see looks_like_mix_variant) so this doesn't also reject
            # otherwise-good legitimate reposts.
            if not looks_like_mix_variant(title) and looks_like_mix_variant(track_title):
                continue

            # title_words_overlap only requires ONE shared word with the base
            # title, which a DIFFERENT upload of the same song (e.g. a plain
            # rip, or someone else's edit) can easily satisfy too - when a
            # SPECIFIC remix/bootleg was asked for, also require its name to
            # actually show up somewhere (track title or uploader), not just
            # the base song.
            if qualifier_words and not (qualifier_words & significant_words(f"{track_title} {uploader_name}")):
                # Our own qualifier name might simply be wrong (a typo, or a
                # mislabeled filename) rather than a different remix - if the
                # CANDIDATE's own named qualifier already credits someone in
                # our own artist field, that's independent confirmation this
                # is the right upload despite the name mismatch. Real report:
                # "KASSIN - Crazy (MRET Remix)" for a track that's actually
                # SoundCloud's own "Crazy (KASSIN Remix)" by uploader KASSIN -
                # "MRET" was simply a wrong remix name in the filename, but
                # the artist credit alone already confirms the upload.
                candidate_named_groups = find_named_qualifier_groups(track_title)
                if not any(_qualifier_names_already_expected(group, artist) for group in candidate_named_groups):
                    continue

            cover_url = result.get("artwork_url")
            if not cover_url:
                continue

            cover_url_hd = cover_url.replace("-large", "-t500x500")
            image_response = requests.get(cover_url_hd, timeout=10)

            if image_response.status_code == 200:
                if is_banned_cover_image(image_response.content):
                    log(f"  [SoundCloud] Match found for '{artist} - {title}' but its cover is a known placeholder - skipping.")
                    continue
                return image_response.content, uploader_name or track_title, track_title

            log(f"  [SoundCloud] Image download failed (HTTP {image_response.status_code}) for '{artist} - {title}'")

        top_result = results[0]
        log(
            f"  [SoundCloud] Match rejected (not an exact match among {len(results)} candidate(s)): "
            f"expected '{artist} - {title}', got track title "
            f"'{unicodedata.normalize('NFC', top_result.get('title', ''))}' / uploader "
            f"'{unicodedata.normalize('NFC', top_result.get('user', {}).get('username', ''))}'"
        )
        return None

    except Exception as error:
        log(f"  [SoundCloud] Error while searching for cover: {error}")
        return None


# ============================================================================
# 10.5. COVER SEARCH - SPOTIFY (last resort only - see USE_SPOTIFY)
# ============================================================================

_cached_spotify_token = None
_cached_spotify_token_expiry = 0  # Unix timestamp


def invalidate_spotify_token():
    """Forces the next get_spotify_token() call to authenticate again
    instead of reusing the cached token - e.g. after the credentials change."""
    global _cached_spotify_token, _cached_spotify_token_expiry
    _cached_spotify_token = None
    _cached_spotify_token_expiry = 0


# Unlike SoundCloud, Spotify's Accounts service documents a Retry-After
# header (seconds) on a 429 from this endpoint - read and honored below
# instead of guessing, falling back to this only if the header is somehow
# missing.
SPOTIFY_TOKEN_RATE_LIMIT_FALLBACK_COOLDOWN_SECONDS = 60
_spotify_token_cooldown = _SourceCooldown()


def get_spotify_token(log=safe_print, on_auth_error=None, on_rate_limited=None):
    global _cached_spotify_token, _cached_spotify_token_expiry

    if _cached_spotify_token and time.time() < _cached_spotify_token_expiry - 60:
        return _cached_spotify_token

    if _spotify_token_cooldown.active():
        log("  [Spotify] Still rate limited from earlier - skipping the token request.")
        return None

    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        log("  [Spotify] No credentials found.")
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

        if response.status_code == 429:
            try:
                wait_seconds = int(response.headers.get("Retry-After", ""))
            except ValueError:
                wait_seconds = SPOTIFY_TOKEN_RATE_LIMIT_FALLBACK_COOLDOWN_SECONDS
            _spotify_token_cooldown.trigger(wait_seconds)
            log(f"  [Spotify] Rate limit reached (too many token requests) - pausing Spotify for {wait_seconds}s.")
            if on_rate_limited:
                on_rate_limited()
            return None

        message = f"HTTP {response.status_code} - {response.text[:300]}"
        log(f"  [Spotify] Authentication error: {message}")
        if on_auth_error:
            on_auth_error("Spotify", message)
        return None

    except Exception as error:
        log(f"  [Spotify] Error during authentication: {error}")
        return None


# The SEARCH endpoint (unlike the token endpoint above) has its own,
# separate rate limit - a real report hit HTTP 429 "QUOTA_EXCEEDED" on it
# directly, with the token itself still perfectly valid. Same "pause for
# the rest of this scan instead of hammering it again on every remaining
# track" treatment as iTunes/SoundCloud, since without this every
# subsequent Spotify search this scan would otherwise just fail the same
# way, one more wasted request at a time.
SPOTIFY_SEARCH_RATE_LIMIT_COOLDOWN_SECONDS = 60
_spotify_search_cooldown = _SourceCooldown()

# Paces every Spotify search request the same way _itunes_throttle() paces
# iTunes (see there) - search_cover_spotify() can fire up to 4 of these
# back-to-back for a single track (plain + remix-qualified retry, each with
# its own title-only fallback - see its docstring), and it's tried for
# every file iTunes didn't already resolve, which in practice is often most
# of a scan, not "very few files" as originally assumed. Without any
# pacing, that burst was tripping Spotify's real rate limit within the
# first several files, making the "Spotify's request limit has been
# reached" popup show up on nearly every scan. A first attempt at 0.5s
# (~120/min) still wasn't enough - real report: the popup kept showing up
# too often even with that in place. Matched to iTunes' own already-proven
# 1.5s (~40/min, see ITUNES_MIN_REQUEST_INTERVAL_SECONDS) instead of
# re-guessing a new number, since that pacing already stopped the
# identical problem there.
SPOTIFY_MIN_REQUEST_INTERVAL_SECONDS = 1.5
_spotify_throttle = _SourceThrottle(SPOTIFY_MIN_REQUEST_INTERVAL_SECONDS)


def _search_cover_spotify_query(query, artist, title, token, log, allow_loose_remix_match=False, on_rate_limited=None):
    """
    Runs a single Spotify search query and validates its candidates -
    factored out of search_cover_spotify() so it can be tried with more
    than one query string (see there) without duplicating the candidate-
    checking logic. Returns (image_bytes, returned_artist, returned_title)
    on a confirmed match, or None (also returning the raw result list, for
    logging) when nothing in this query's results checks out.

    allow_loose_remix_match mirrors search_cover_itunes's own parameter of
    the same name - see there. on_rate_limited is called once if THIS call
    hits the search endpoint's own rate limit (see
    SPOTIFY_SEARCH_RATE_LIMIT_COOLDOWN_SECONDS).
    """
    if _spotify_search_cooldown.active():
        log("  [Spotify] Still rate limited from earlier in this scan - skipping.")
        return None, []

    _spotify_throttle()

    response = requests.get(
        "https://api.spotify.com/v1/search",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": query, "type": "track", "limit": 10},
        timeout=10,
    )

    if response.status_code == 429:
        global SPOTIFY_SEARCH_RATE_LIMITED
        SPOTIFY_SEARCH_RATE_LIMITED = True
        _spotify_search_cooldown.trigger(SPOTIFY_SEARCH_RATE_LIMIT_COOLDOWN_SECONDS)
        log(
            f"  [Spotify] Rate limited (HTTP 429: {response.text[:200]}) - pausing Spotify search "
            "for the rest of this scan."
        )
        if on_rate_limited:
            on_rate_limited()
        return None, []

    if response.status_code != 200:
        log(f"  [Spotify] Search failed: HTTP {response.status_code} - {response.text[:300]}")
        return None, []

    results = response.json().get("tracks", {}).get("items", [])
    if not results:
        return None, []

    title_normalized = strip_feature_suffix(strip_generic_qualifier_modifiers(strip_generic_mix_suffix(title)))

    for result in results:
        returned_artist = unicodedata.normalize(
            "NFC", ", ".join(a.get("name", "") for a in result.get("artists", []))
        )
        returned_title = unicodedata.normalize("NFC", result.get("name", ""))
        # See search_cover_itunes's identical normalization for why -
        # Spotify commonly credits a remix as "Title - X Remix" (dash)
        # rather than our own "Title (X Remix)" convention, and/or drops a
        # droppable "Extended"/"Radio" modifier one side has and the other
        # doesn't (see strip_generic_qualifier_modifiers).
        # strip_slash_credit() drops an inline "/ Name" original-artist
        # credit some stores insert right into the title (see its own
        # docstring) - applied only for this title comparison, not to
        # returned_title itself (still used above as an artist-match
        # signal and for logging).
        returned_title_reformatted = reformat_trailing_dash_mix(strip_slash_credit(returned_title))
        returned_title_normalized = strip_feature_suffix(
            strip_generic_qualifier_modifiers(strip_generic_mix_suffix(returned_title_reformatted))
        )

        artist_ok = (
            artist_sets_match(artist, returned_artist, returned_title)
            and exact_match(title_normalized, returned_title_normalized)
        )
        swapped_ok = exact_match(title, returned_artist) and artist_sets_match(artist, returned_title_normalized)

        loose_ok = False
        if (
            not (artist_ok or swapped_ok) and allow_loose_remix_match
            and loose_remix_match(title, returned_title_reformatted, expected_artist=artist)
        ):
            _, returned_groups = strip_all_trailing_groups(returned_title_reformatted)
            returned_artist_set = split_artist_names(returned_artist) | extract_feature_names_from_groups(returned_groups)
            loose_ok = split_artist_names(artist) <= returned_artist_set

        if not (artist_ok or swapped_ok or loose_ok):
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
            if is_banned_cover_image(image_response.content):
                log(f"  [Spotify] Match found for '{artist} - {title}' but its cover is a known placeholder - skipping.")
                continue
            return (image_response.content, returned_artist, returned_title), results

        log(f"  [Spotify] Image download failed (HTTP {image_response.status_code}) for '{artist} - {title}'")

    return None, results


def search_cover_spotify(artist, title, token, log=safe_print, allow_loose_remix_match=False, on_rate_limited=None):
    """
    Checks up to 10 candidates (not just the top one), mirroring
    search_cover_itunes()/search_cover_soundcloud().

    A single query only (per request, to cut Spotify's request budget per
    track - see _search_one_source's own two calls, one plain and one
    remix-qualified, which is the actual "one without correction, one
    with correction" budget now) - this used to also retry title-only on
    a miss (real report: "Raven Maize, Dave Lee ZR - Forever Together
    (CASSIMM Remix)", a multi-artist credit with a garbled handle-style
    name that derailed Spotify's own ranking when combined with the
    title), which could double every call's request count. Accepted
    tradeoff: that specific kind of match is missed again now, in
    exchange for roughly half the Spotify requests per track.

    allow_loose_remix_match: see search_cover_itunes's identical parameter.
    on_rate_limited: see _search_cover_spotify_query's identical parameter.
    """
    if not token:
        return None

    try:
        match, results = _search_cover_spotify_query(
            build_search_query(artist, title), artist, title, token, log,
            allow_loose_remix_match=allow_loose_remix_match, on_rate_limited=on_rate_limited,
        )
        if match:
            return match

        if not results:
            log(f"  [Spotify] No result at all for '{artist} - {title}'")
        else:
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


ACOUSTID_MIN_SCORE = 0.5  # AcoustID scores range 0-1; below this, not worth trusting

# pyacoustid defaults to plain HTTP for the lookup API - confirmed the
# server also accepts HTTPS on the exact same endpoint, and switching to
# it measurably cut down on "Connection aborted" (WinError 10054) resets
# seen constantly in real scans: some antivirus/firewall/router setups are
# far more likely to interfere with (or outright reset) plain HTTP traffic
# than HTTPS.
acoustid.set_base_url("https://api.acoustid.org/v2/")


def _run_without_console_window(func, *args, **kwargs):
    """
    Runs func() with subprocess.Popen temporarily patched to suppress the
    console window Windows would otherwise briefly flash for any child
    process it spawns. Every OTHER subprocess call in this codebase
    (convert_to_mp3, _write_wav_riff_info, ...) already passes
    creationflags=CREATE_NO_WINDOW itself - pyacoustid's own internal
    fpcalc invocation (inside acoustid.match()) has no parameter to do the
    same, so this patches it in from the outside instead. Scoped to just
    this call (restored in a finally, even on error) rather than a
    permanent global patch, to keep the blast radius as small as possible.
    """
    if sys.platform != "win32":
        return func(*args, **kwargs)

    original_popen = subprocess.Popen

    def popen_no_window(*popen_args, **popen_kwargs):
        popen_kwargs["creationflags"] = popen_kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        return original_popen(*popen_args, **popen_kwargs)

    subprocess.Popen = popen_no_window
    try:
        return func(*args, **kwargs)
    finally:
        subprocess.Popen = original_popen


# Best-effort text match against a WebServiceError's own message - pyacoustid
# doesn't expose a distinct exception type or the API's numeric error code
# for a rate limit specifically (unlike Spotify/iTunes, which give a clean
# HTTP 429), so this is a heuristic, not a guaranteed detection.
ACOUSTID_RATE_LIMIT_MESSAGE_KEYWORDS = ("rate limit", "too many requests", "429", "quota")

ACOUSTID_RATE_LIMIT_COOLDOWN_SECONDS = 60
_acoustid_cooldown = _SourceCooldown()

# Paces every AcoustID lookup the same way _itunes_throttle()/
# _spotify_throttle()/_soundcloud_throttle() pace their own sources (see
# there) - same shared-key exposure as SoundCloud/Spotify
# (ACOUSTID_API_KEY is a single embedded key, not per-user), and it's
# tried for every file the text-based search couldn't resolve at all, so
# a big scan can fire many of these back-to-back with no pacing before
# now. 1.5s apart, matching the other three.
ACOUSTID_MIN_REQUEST_INTERVAL_SECONDS = 1.5
_acoustid_throttle = _SourceThrottle(ACOUSTID_MIN_REQUEST_INTERVAL_SECONDS)


def identify_via_acoustid(file_path, log=safe_print, on_rate_limited=None):
    """
    Identifies a track from its actual audio content via AcoustID/
    Chromaprint, instead of its filename/tags - last resort, only meant to
    be tried when the normal text-based search (iTunes/SoundCloud)
    already came up empty, or never had anything to search with in the
    first place (a filename too mangled to parse an artist/title from at
    all). Returns (artist, title) from the best-scoring match at or above
    ACOUSTID_MIN_SCORE, or None if unavailable/no confident match - never
    raises, same philosophy as the other cover sources.
    """
    if not ACOUSTID_API_KEY:
        log("  [AcoustID] No API key configured - skipping (Settings > AcoustID API key...).")
        return None

    if _acoustid_cooldown.active():
        log("  [AcoustID] Still rate limited from earlier in this scan - skipping.")
        return None

    _acoustid_throttle()

    acoustid.FPCALC_COMMAND = find_fpcalc()
    # WebServiceError covers connection resets/timeouts, which are usually a
    # transient network hiccup (flaky Wi-Fi, antivirus HTTP inspection) - worth
    # a couple of quick retries. Fingerprinting errors are deterministic (same
    # file will fail the same way again), so those return immediately instead.
    max_attempts = 3
    results = None
    for attempt in range(1, max_attempts + 1):
        try:
            results = list(_run_without_console_window(acoustid.match, ACOUSTID_API_KEY, file_path))
            break
        except acoustid.NoBackendError:
            log("  [AcoustID] fpcalc not found - check it's bundled or installed.")
            return None
        except acoustid.FingerprintGenerationError as error:
            log(f"  [AcoustID] Could not fingerprint '{file_path}': {error}")
            return None
        except acoustid.WebServiceError as error:
            if any(keyword in str(error).lower() for keyword in ACOUSTID_RATE_LIMIT_MESSAGE_KEYWORDS):
                _acoustid_cooldown.trigger(ACOUSTID_RATE_LIMIT_COOLDOWN_SECONDS)
                log(
                    f"  [AcoustID] Rate limited ({error}) - pausing AcoustID for "
                    f"{ACOUSTID_RATE_LIMIT_COOLDOWN_SECONDS}s for the rest of this scan."
                )
                if on_rate_limited:
                    on_rate_limited()
                return None
            if attempt == max_attempts:
                log(f"  [AcoustID] Lookup failed after {max_attempts} attempts: {error}")
                return None
            log(f"  [AcoustID] Lookup failed (retrying, attempt {attempt}/{max_attempts}): {error}")
            time.sleep(1.5)
        except Exception as error:
            log(f"  [AcoustID] Unexpected error identifying '{file_path}': {error}")
            return None

    for score, _recording_id, title, artist in results:
        if not artist or not title:
            continue
        if score < ACOUSTID_MIN_SCORE:
            break  # results are sorted best-first - nothing after this scores any higher
        log(f"  [AcoustID] Identified as '{artist} - {title}' (score {score:.2f}).")
        return artist, title

    if results:
        log(f"  [AcoustID] Best match scored too low to trust (top score: {results[0][0]:.2f}).")
    else:
        log("  [AcoustID] No match found.")
    return None


def check_source_credentials(log=safe_print):
    """
    Verifies the SHARED credentials behind SoundCloud/Spotify/AcoustID
    actually authenticate, once per app launch - SoundCloud/AcoustID can't
    be turned off from Settings (see the comment above USE_ITUNES), so a
    revoked/expired credential would otherwise silently degrade cover
    matching with nothing visible to explain why. Spotify (USE_SPOTIFY)
    IS a user-facing toggle now, and is skipped entirely here while it's
    off. iTunes needs no credentials, so it's not checked here (see
    ITUNES_RATE_LIMIT_COOLDOWN_SECONDS/search_cover_itunes's
    on_rate_limited instead for iTunes-specific trouble).

    Returns a list of source names ("SoundCloud"/"Spotify"/"AcoustID")
    whose credentials were confirmed broken - never raises, and a source
    is only reported here on a CONFIRMED rejection (invalid/revoked
    credentials), not on a transient rate limit or network hiccup, so
    this doesn't cry wolf over an ordinary flaky connection.
    """
    broken = []

    soundcloud_rate_limited = {"value": False}
    invalidate_soundcloud_token()
    soundcloud_token = get_soundcloud_token(
        log=log, on_rate_limited=lambda: soundcloud_rate_limited.update(value=True),
    )
    if not soundcloud_token and not soundcloud_rate_limited["value"] and SOUNDCLOUD_CLIENT_ID and SOUNDCLOUD_CLIENT_SECRET:
        broken.append("SoundCloud")

    # Skipped entirely while the user has Spotify turned off (see the
    # comment above USE_SPOTIFY) - checking a source they've deliberately
    # disabled would just spend a request against its own real rate limit
    # for nothing actionable.
    if USE_SPOTIFY:
        invalidate_spotify_token()
        spotify_token = get_spotify_token(log=log)
        if not spotify_token and SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
            broken.append("Spotify")

    if ACOUSTID_API_KEY:
        # acoustid.lookup() talks to the same web service as identify_via_acoustid()
        # but takes a fingerprint/duration directly, so a dummy pair is enough to
        # provoke a real "invalid API key" response (error code 4) without needing
        # an actual audio file to fingerprint. A handful of retries because a bad
        # request to this endpoint has been observed to sometimes come back as a
        # plain connection reset instead of the real JSON error (same flakiness
        # noted in identify_via_acoustid's own retry loop).
        acoustid_broken = False
        for attempt in range(3):
            try:
                result = acoustid.lookup(ACOUSTID_API_KEY, "AAAA", 1, meta=["recordings"])
                acoustid_broken = (
                    result.get("status") == "error" and result.get("error", {}).get("code") == 4
                )
                break
            except acoustid.WebServiceError as error:
                log(f"  [AcoustID] Credential check failed (retrying, attempt {attempt + 1}/3): {error}")
                time.sleep(1.5)
            except Exception as error:
                log(f"  [AcoustID] Unexpected error during credential check: {error}")
                break
        if acoustid_broken:
            broken.append("AcoustID")

    return broken


# ============================================================================
# 11. FORMAT CONVERSION
# ============================================================================

def _find_bundled_tool(base_name):
    """
    Looks for a bundled command-line tool (ffmpeg, fpcalc) next to the app
    first, falling back to the system PATH if it's not there. The bundled
    binary is named "<base_name>.exe" on Windows (build_all.bat/
    installer.iss) or plain "<base_name>" with no extension on macOS
    (build_mac.sh puts it in Contents/MacOS/, alongside the app's own
    executable - app_base_dir() resolves there too when frozen).
    """
    bundled_name = f"{base_name}.exe" if sys.platform == "win32" else base_name
    bundled_path = os.path.join(app_base_dir(), bundled_name)
    if os.path.exists(bundled_path):
        return bundled_path
    return base_name  # relies on it being installed and in the system PATH


def find_ffmpeg():
    return _find_bundled_tool("ffmpeg")


def find_fpcalc():
    """Used by identify_via_acoustid() for AcoustID/Chromaprint audio fingerprinting."""
    return _find_bundled_tool("fpcalc")


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

    FFmpeg's plain conversion drops every existing tag (genre, year,
    etc.) - confirmed live: its WAV demuxer doesn't surface the ID3 chunk
    mutagen writes as ffmpeg-level metadata to carry over, so the output
    AIFF comes out with none at all, not just the ones this app itself
    doesn't otherwise rewrite. Read the source's ID3 tags via mutagen
    first and reapply them to the converted file below - the caller's
    own artist/title/cover writes still happen normally afterward, same
    as for a file that was never converted.
    """
    source_tags = None
    try:
        source_audio = WAVE(source_path)
        source_tags = source_audio.tags
    except Exception as error:
        print(f"  Could not read existing tags before conversion: {error}")

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

        if source_tags:
            try:
                aiff_audio = AIFF(aiff_path)
                if aiff_audio.tags is None:
                    aiff_audio.add_tags()
                # Add each frame individually into the AIFF's own tags
                # object (already correctly bound to its FORM container by
                # add_tags() above) rather than assigning source_tags
                # wholesale - WAV's ID3 tags are bound to a RIFF container
                # internally, and reusing that object as-is against an
                # AIFF/FORM file fails to save ("Root chunk must be a RIFF
                # chunk, got FORM").
                for frame in source_tags.values():
                    aiff_audio.tags.add(frame)
                aiff_audio.save()
            except Exception as error:
                print(f"  Could not carry over existing tags after conversion: {error}")

        os.remove(source_path)
        return aiff_path

    except FileNotFoundError:
        print("  FFmpeg was not found. Check that it's installed and in the PATH.")
        return None
    except Exception as error:
        print(f"  Error during conversion: {error}")
        return None


def convert_aiff_to_wav(source_path):
    """
    Reverses convert_wav_to_aiff(): the same lossless PCM byte-order swap
    (big-endian -> little-endian), not a re-encode, so there's no quality
    loss - used by restore_history_entry() to fully undo a WAV->AIFF
    conversion that happened as part of the very run being restored, not
    just its tags/filename. Removes the source AIFF file on success.
    """
    source_tags = None
    try:
        source_audio = AIFF(source_path)
        source_tags = source_audio.tags
    except Exception as error:
        print(f"  Could not read existing tags before reverting to WAV: {error}")

    wav_path = os.path.splitext(source_path)[0] + ".wav"
    try:
        result = subprocess.run(
            [find_ffmpeg(), "-i", source_path, "-y", wav_path],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            print(f"  FFmpeg error while reverting to WAV: {result.stderr[-300:]}")
            return None

        if source_tags:
            try:
                wav_audio = WAVE(wav_path)
                if wav_audio.tags is None:
                    wav_audio.add_tags()
                for frame in source_tags.values():
                    wav_audio.tags.add(frame)
                wav_audio.save()
            except Exception as error:
                print(f"  Could not carry over existing tags while reverting to WAV: {error}")

        os.remove(source_path)
        return wav_path

    except FileNotFoundError:
        print("  FFmpeg was not found. Check that it's installed and in the PATH.")
        return None
    except Exception as error:
        print(f"  Error while reverting to WAV: {error}")
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
# 12. TAG WRITING
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
    current_cover_bytes = info.get("current_cover_bytes")
    if detect_fuviclan_mention(info.get("file", "")) or is_banned_cover_image(current_cover_bytes):
        return None
    return current_cover_bytes


def has_usable_cover(info):
    """
    Whether this row should count as "has a cover" for deciding if it still
    needs cover-search attention (the "Only show tracks with no cover
    match" filter, the post-scan no-cover count, the scan summary's kept-
    existing/no-cover split) - distinct from effective_cover_bytes(), which
    answers a different question ("what will actually be WRITTEN").

    A cover an online search actually FOUND (found_cover_image) always
    counts, regardless of checked state. Otherwise, an existing cover only
    counts while the row is still CHECKED (apply_changes) - Apply is
    actually going to keep it, so it's a real, current answer to "does
    this track have a cover" - and only if it isn't itself a banned/
    generic placeholder (is_banned_cover_image). Once UNCHECKED, an
    existing cover no longer counts at all, even a legitimate one: the
    whole point of unchecking a no-match row is to flag it for later
    review, not to quietly let its (unverified, possibly wrong/low-
    quality) existing cover excuse it from that list. Real report:
    unchecking a row whose online search genuinely found nothing still
    hid it from the filter because it happened to already have SOME
    cover embedded.
    """
    if info.get("found_cover_image"):
        return True
    if not info.get("apply_changes", True):
        return False
    if not info.get("has_cover"):
        return False
    return not (detect_fuviclan_mention(info.get("file", "")) or is_banned_cover_image(info.get("current_cover_bytes")))


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
        if title:
            tags.setall("TIT2", [TIT2(encoding=3, text=[title])])
        else:
            tags.delall("TIT2")
    if update_artist:
        if artist:
            tags.setall("TPE1", [TPE1(encoding=3, text=[artist])])
        else:
            tags.delall("TPE1")

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

def process_files(plan, log=safe_print, on_progress=None, on_file_processed=None, should_cancel=None):
    """
    Processes a list of already-scanned files (see scan_files()).
    Each item in the plan carries its own options:
    convert, update_title, update_artist, update_cover.
    on_file_processed(identifier, success, reason=None) is called after
    each file, whether it was processed successfully or skipped/failed -
    reason is a short human-readable explanation, only set when success
    is False (e.g. a corrupted file's tags/audio couldn't be read).
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

        try:
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
                        on_file_processed(identifier, False, "Conversion failed")
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
                    on_file_processed(identifier, False, "No title to write")
                continue

            log(f"  Artist: '{artist}' | Title: '{title}'")

            update_title = update_artist = update_cover = info.get("apply_changes", True)

            force_remove_if_missing = bool(detect_fuviclan_mention(file_name)) or is_banned_cover_image(
                info.get("current_cover_bytes")
            )

            cover_image = info.get("found_cover_image") if update_cover else None

            write_tags(
                full_path, artist, title, cover_image, force_remove_if_missing,
                update_title=update_title, update_artist=update_artist, update_cover=update_cover,
                log=log,
            )

            if update_title and update_artist and FIX_TRACK_FILE_NAME:
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

        except Exception as error:
            # Never let one bad file (e.g. corrupted audio data mutagen
            # can't parse - "can't sync to MPEG frame" and similar) abort
            # the whole batch - every file queued after it would otherwise
            # silently never get tagged/renamed at all, with nothing in the
            # log to explain why.
            log(f"  Error processing '{file_name}': {error}\n")
            info["processed"] = True
            if on_file_processed:
                on_file_processed(identifier, False, str(error))

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
