"""
Uploads a built installer to VirusTotal, waits for the scan to finish, and
prints the detection ratio + permalink - the automated replacement for
manually dragging the file into virustotal.com after each release.

Usage: python src/scan_virustotal.py path/to/Track-Tidy-Setup-X.Y.exe

Reads the API key from release_secrets.json (gitignored, local-only - see
that file's comment in .gitignore for why this is kept separate from
default_credentials.json, which gets bundled into the app itself).

Free-tier VirusTotal API: 4 requests/minute. A single release scan (one
upload + a handful of poll requests) stays well within that.
"""

import json
import os
import sys
import time

import requests

VT_API_BASE = "https://www.virustotal.com/api/v3"
POLL_INTERVAL_SECONDS = 15
MAX_POLL_ATTEMPTS = 40  # up to 10 minutes


def load_api_key():
    # This file lives in src/, one level below the project root where
    # release_secrets.json actually sits (same reasoning as
    # app_base_dir()/resource_path() in track_tidy.py/ui_common.py).
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(project_root, "release_secrets.json")
    with open(path, "r", encoding="utf-8") as f:
        secrets = json.load(f)
    api_key = secrets.get("virustotal_api_key")
    if not api_key:
        raise SystemExit("release_secrets.json has no 'virustotal_api_key' entry.")
    return api_key


# The plain /files endpoint only accepts uploads up to 32MB - anything
# bigger (like our installer) needs a dedicated upload URL first, good for
# files up to 650MB.
DIRECT_UPLOAD_LIMIT_BYTES = 32 * 1024 * 1024


def get_upload_url(api_key):
    headers = {"x-apikey": api_key}
    response = requests.get(f"{VT_API_BASE}/files/upload_url", headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()["data"]


def upload_file(file_path, api_key):
    headers = {"x-apikey": api_key}
    upload_url = f"{VT_API_BASE}/files"
    if os.path.getsize(file_path) > DIRECT_UPLOAD_LIMIT_BYTES:
        upload_url = get_upload_url(api_key)
    with open(file_path, "rb") as f:
        response = requests.post(
            upload_url,
            headers=headers,
            files={"file": (os.path.basename(file_path), f)},
            timeout=300,
        )
    response.raise_for_status()
    return response.json()["data"]["id"]


def poll_analysis(analysis_id, api_key):
    headers = {"x-apikey": api_key}
    for attempt in range(MAX_POLL_ATTEMPTS):
        response = requests.get(f"{VT_API_BASE}/analyses/{analysis_id}", headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()["data"]
        status = data["attributes"]["status"]
        if status == "completed":
            return data
        print(f"  [{attempt + 1}/{MAX_POLL_ATTEMPTS}] Still scanning ({status})... waiting {POLL_INTERVAL_SECONDS}s")
        time.sleep(POLL_INTERVAL_SECONDS)
    raise SystemExit("Timed out waiting for the VirusTotal scan to finish.")


def scan(file_path):
    if not os.path.exists(file_path):
        raise SystemExit(f"File not found: {file_path}")

    api_key = load_api_key()

    print(f"Uploading {os.path.basename(file_path)} to VirusTotal...")
    analysis_id = upload_file(file_path, api_key)

    print("Waiting for the scan to complete...")
    analysis = poll_analysis(analysis_id, api_key)

    stats = analysis["attributes"]["stats"]
    # The analysis response has no "meta.file_info" - the file's SHA256 is
    # the last path segment of its own "item" link instead.
    file_sha256 = analysis["links"]["item"].rsplit("/", 1)[-1]
    malicious = stats.get("malicious", 0)
    total = sum(stats.values())
    permalink = f"https://www.virustotal.com/gui/file/{file_sha256}/detection"

    print()
    print(f"Result: {malicious}/{total} vendors flagged it as malicious.")
    print(f"SHA256: {file_sha256}")
    print(f"Report: {permalink}")

    return {
        "sha256": file_sha256,
        "malicious": malicious,
        "total": total,
        "permalink": permalink,
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"Usage: python {sys.argv[0]} <path-to-installer>")
    scan(sys.argv[1])
