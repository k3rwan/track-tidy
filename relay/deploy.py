"""Deploys relay/worker.js to Cloudflare Workers through the REST API -
no Node.js/wrangler needed (this machine doesn't have them).

Usage (from the repo root, with the venv's Python):

    python relay/deploy.py                      # (re)deploy the code only
    python relay/deploy.py --set-webhook FILE    # also (re)set the Discord webhook secret
    python relay/deploy.py --seed-users FILE     # bulk-load known user hashes into KV
    python relay/deploy.py --check               # just print the relay URL and health

Credentials, never committed (see .gitignore):
- Cloudflare API token: CLOUDFLARE_API_TOKEN env var, or relay/.cloudflare_api_token.
  Needs Workers Scripts:Edit + Workers KV Storage:Edit + Account Settings:Read
  (Cloudflare's "Edit Cloudflare Workers" token template covers all three).
- Discord webhook URL: only ever passed via --set-webhook FILE, uploaded as an
  encrypted Worker secret, never printed.
"""
import argparse
import json
import os
import sys

import requests

API = "https://api.cloudflare.com/client/v4"
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT_NAME = "track-tidy-relay"
KV_TITLE = "track-tidy-relay-users"
COMPATIBILITY_DATE = "2025-06-01"


def load_token():
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not token:
        path = os.path.join(HERE, ".cloudflare_api_token")
        if os.path.exists(path):
            token = open(path, encoding="utf-8").read().strip()
    if not token:
        sys.exit("No Cloudflare API token (CLOUDFLARE_API_TOKEN or relay/.cloudflare_api_token).")
    return token


class Cloudflare:
    def __init__(self, token):
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"

    def call(self, method, path, **kwargs):
        response = self.session.request(method, API + path, timeout=60, **kwargs)
        try:
            body = response.json()
        except ValueError:
            body = {"success": False, "errors": [{"message": response.text[:300]}]}
        if not body.get("success", False):
            raise RuntimeError(f"{method} {path} -> HTTP {response.status_code}: {body.get('errors')}")
        return body

    def account_id(self):
        explicit = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
        if explicit:
            return explicit
        accounts = self.call("GET", "/accounts")["result"]
        if not accounts:
            sys.exit("This token can't see any Cloudflare account.")
        if len(accounts) > 1:
            names = ", ".join(f"{a['name']} ({a['id']})" for a in accounts)
            sys.exit(f"Several accounts visible - set CLOUDFLARE_ACCOUNT_ID. Accounts: {names}")
        return accounts[0]["id"]

    def kv_namespace(self, account):
        page = 1
        while True:
            body = self.call("GET", f"/accounts/{account}/storage/kv/namespaces",
                             params={"per_page": 100, "page": page})
            for namespace in body["result"]:
                if namespace["title"] == KV_TITLE:
                    return namespace["id"]
            if page >= body.get("result_info", {}).get("total_pages", 1):
                break
            page += 1
        created = self.call("POST", f"/accounts/{account}/storage/kv/namespaces", json={"title": KV_TITLE})
        print(f"Created KV namespace '{KV_TITLE}'.")
        return created["result"]["id"]

    def upload_script(self, account, namespace_id):
        source = open(os.path.join(HERE, "worker.js"), encoding="utf-8").read()
        metadata = {
            "main_module": "worker.js",
            "compatibility_date": COMPATIBILITY_DATE,
            "bindings": [{"type": "kv_namespace", "name": "USERS", "namespace_id": namespace_id}],
            # Leaves DISCORD_WEBHOOK_URL (set separately, below) untouched on
            # a code-only redeploy.
            "keep_bindings": ["secret_text"],
        }
        files = {
            "metadata": (None, json.dumps(metadata), "application/json"),
            "worker.js": ("worker.js", source, "application/javascript+module"),
        }
        self.call("PUT", f"/accounts/{account}/workers/scripts/{SCRIPT_NAME}", files=files)
        print("Uploaded worker code.")

    def set_secret(self, account, name, value):
        self.call("PUT", f"/accounts/{account}/workers/scripts/{SCRIPT_NAME}/secrets",
                  json={"name": name, "text": value, "type": "secret_text"})
        print(f"Set secret {name}.")

    def workers_dev_url(self, account):
        try:
            subdomain = self.call("GET", f"/accounts/{account}/workers/subdomain")["result"]["subdomain"]
        except RuntimeError:
            subdomain = None
        if not subdomain:
            sys.exit("This Cloudflare account has no workers.dev subdomain yet - open Workers & Pages "
                     "in the dashboard once to pick one, then rerun.")
        self.call("POST", f"/accounts/{account}/workers/scripts/{SCRIPT_NAME}/subdomain",
                  json={"enabled": True, "previews_enabled": False})
        return f"https://{SCRIPT_NAME}.{subdomain}.workers.dev"

    def seed_users(self, account, namespace_id, hashes):
        entries = [{"key": f"u:{h}", "value": "1"} for h in hashes]
        for start in range(0, len(entries), 10000):
            self.call("PUT", f"/accounts/{account}/storage/kv/namespaces/{namespace_id}/bulk",
                      json=entries[start:start + 10000])
        print(f"Seeded {len(entries)} user hash(es).")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set-webhook", metavar="FILE", help="file containing the Discord webhook URL")
    parser.add_argument("--seed-users", metavar="FILE", help="file with one SHA-256 user hash per line")
    parser.add_argument("--check", action="store_true", help="only print the URL and hit the health check")
    args = parser.parse_args()

    cf = Cloudflare(load_token())
    account = cf.account_id()

    if not args.check:
        namespace_id = cf.kv_namespace(account)
        cf.upload_script(account, namespace_id)
        if args.set_webhook:
            webhook = open(args.set_webhook, encoding="utf-8").read().strip()
            if not webhook.startswith("https://discord.com/api/webhooks/"):
                sys.exit("That file doesn't contain a Discord webhook URL.")
            cf.set_secret(account, "DISCORD_WEBHOOK_URL", webhook)
        if args.seed_users:
            hashes = [line.strip() for line in open(args.seed_users, encoding="utf-8") if line.strip()]
            cf.seed_users(account, namespace_id, hashes)

    url = cf.workers_dev_url(account)
    print("Relay URL:", url)
    try:
        health = requests.get(url + "/", timeout=20)
        print("Health:", health.status_code, health.text[:80])
    except requests.RequestException as error:
        print("Health check failed (a brand-new workers.dev route can take a minute):", error)


if __name__ == "__main__":
    main()
