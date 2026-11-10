"""Deploys relay/worker.js to Cloudflare Workers through the REST API -
no Node.js/wrangler needed (this machine doesn't have them).

Usage (from the repo root, with the venv's Python):

    python relay/deploy.py                      # (re)deploy the code only
    python relay/deploy.py --set-webhook FILE    # also (re)set the Discord webhook secret
    python relay/deploy.py --seed-users FILE     # bulk-load known user hashes into KV
    python relay/deploy.py --purge-legacy        # drop old unkeyed "u:" user hashes
    python relay/deploy.py --check               # just print the relay URL and health

Every deploy also makes sure the USER_HASH_KEY secret exists (a random key
generated here, sent straight to Cloudflare and never written to disk): the
relay hashes usernames with it, so the stored hashes can't be reversed by
hashing a list of common names.

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
import secrets
import sys

import requests

API = "https://api.cloudflare.com/client/v4"
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT_NAME = "track-tidy-relay"
KV_TITLE = "track-tidy-relay-users"
COMPATIBILITY_DATE = "2025-06-01"
# Cloudflare Rate Limiting binding: 20 requests per IP per 60 s, shared
# across the Worker's isolates (worker.js falls back to an in-memory limit
# if this binding is missing). namespace_id is any account-unique integer.
RATE_LIMIT_BINDING = {
    "type": "ratelimit", "name": "RATE_LIMITER", "namespace_id": "1001",
    "simple": {"limit": 20, "period": 60},
}


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
        kv_binding = {"type": "kv_namespace", "name": "USERS", "namespace_id": namespace_id}

        def upload(bindings):
            metadata = {
                "main_module": "worker.js",
                "compatibility_date": COMPATIBILITY_DATE,
                "bindings": bindings,
                # Leaves the secrets (DISCORD_WEBHOOK_URL, USER_HASH_KEY)
                # untouched on a code-only redeploy.
                "keep_bindings": ["secret_text"],
            }
            files = {
                "metadata": (None, json.dumps(metadata), "application/json"),
                "worker.js": ("worker.js", source, "application/javascript+module"),
            }
            self.call("PUT", f"/accounts/{account}/workers/scripts/{SCRIPT_NAME}", files=files)

        try:
            upload([kv_binding, RATE_LIMIT_BINDING])
            print("Uploaded worker code (with rate-limit binding).")
        except RuntimeError as error:
            print(f"Rate-limit binding refused ({error}) - uploading without it "
                  "(worker.js falls back to its in-memory limit).")
            upload([kv_binding])
            print("Uploaded worker code.")

    def secret_names(self, account):
        body = self.call("GET", f"/accounts/{account}/workers/scripts/{SCRIPT_NAME}/secrets")
        return {secret["name"] for secret in body["result"]}

    def ensure_hash_key(self, account):
        if "USER_HASH_KEY" in self.secret_names(account):
            return
        self.set_secret(account, "USER_HASH_KEY", secrets.token_hex(32))

    def purge_legacy(self, account, namespace_id):
        keys, cursor = [], None
        while True:
            params = {"prefix": "u:", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            body = self.call("GET", f"/accounts/{account}/storage/kv/namespaces/{namespace_id}/keys",
                             params=params)
            keys += [k["name"] for k in body["result"]]
            cursor = body.get("result_info", {}).get("cursor")
            if not cursor:
                break
        for start in range(0, len(keys), 10000):
            self.call("POST", f"/accounts/{account}/storage/kv/namespaces/{namespace_id}/bulk/delete",
                      json=keys[start:start + 10000])
        # The running counter ("meta:count") is left alone: those users were
        # already counted, dropping their hash only means one of them coming
        # back would be counted a second time.
        print(f"Purged {len(keys)} legacy user hash(es).")

    def set_secret(self, account, name, value):
        self.call("PUT", f"/accounts/{account}/workers/scripts/{SCRIPT_NAME}/secrets",
                  json={"name": name, "text": value, "type": "secret_text"})
        print(f"Set secret {name}.")

    def workers_dev_url(self, account, wanted_subdomain=None):
        try:
            subdomain = self.call("GET", f"/accounts/{account}/workers/subdomain")["result"]["subdomain"]
        except RuntimeError:
            subdomain = None
        if not subdomain and wanted_subdomain:
            self.call("PUT", f"/accounts/{account}/workers/subdomain", json={"subdomain": wanted_subdomain})
            subdomain = wanted_subdomain
            print(f"Registered workers.dev subdomain '{subdomain}'.")
        if not subdomain:
            sys.exit("This Cloudflare account has no workers.dev subdomain yet - rerun with "
                     "--subdomain NAME (account-wide, globally unique).")
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
    parser.add_argument("--purge-legacy", action="store_true",
                        help='delete the old unkeyed "u:" user hashes (the count is kept)')
    parser.add_argument("--check", action="store_true", help="only print the URL and hit the health check")
    parser.add_argument("--subdomain", help="workers.dev subdomain to register if the account has none yet")
    args = parser.parse_args()

    cf = Cloudflare(load_token())
    account = cf.account_id()

    if not args.check:
        namespace_id = cf.kv_namespace(account)
        cf.upload_script(account, namespace_id)
        cf.ensure_hash_key(account)
        if args.set_webhook:
            webhook = open(args.set_webhook, encoding="utf-8").read().strip()
            if not webhook.startswith("https://discord.com/api/webhooks/"):
                sys.exit("That file doesn't contain a Discord webhook URL.")
            cf.set_secret(account, "DISCORD_WEBHOOK_URL", webhook)
        if args.seed_users:
            hashes = [line.strip() for line in open(args.seed_users, encoding="utf-8") if line.strip()]
            cf.seed_users(account, namespace_id, hashes)
        if args.purge_legacy:
            cf.purge_legacy(account, namespace_id)

    url = cf.workers_dev_url(account, args.subdomain)
    print("Relay URL:", url)
    try:
        health = requests.get(url + "/", timeout=20)
        print("Health:", health.status_code, health.text[:80])
    except requests.RequestException as error:
        print("Health check failed (a brand-new workers.dev route can take a minute):", error)


if __name__ == "__main__":
    main()
