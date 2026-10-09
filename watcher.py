"""Grok Build release watcher.

Reads the public stable-version pointer and posts one short tweet when a new
version appears. Also checks whether the changelog page lists that version
(log only, phase 1).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

from curl_cffi import requests as cffi_requests

STABLE_URL = "https://storage.googleapis.com/grok-build-public-artifacts/cli/stable"
CHANGELOG_URL = "https://x.ai/build/changelog"
STATE_FILE = Path(__file__).resolve().parent / "state" / "last_announced.txt"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
TARGETS = ["chrome120", "chrome131", "safari17_0"]
TIMEOUT = 30
TWEET_TEMPLATE = "Grok Build {version} is out. Full details soon."


def fetch(url: str) -> str | None:
    """GET with Chrome impersonation; rotate targets on transient errors. None on failure."""
    for i, target in enumerate(TARGETS):
        if i:
            time.sleep(2)
        try:
            r = cffi_requests.get(url, impersonate=target, timeout=TIMEOUT)
        except Exception as exc:
            print(f"[warn] {url}: {exc} ({target})")
            continue
        if r.status_code == 200:
            return r.text
        print(f"[warn] {url}: HTTP {r.status_code} ({target})")
    return None


def read_state() -> str:
    return STATE_FILE.read_text(encoding="utf-8").strip() if STATE_FILE.exists() else ""


def write_state(version: str) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(version + "\n", encoding="utf-8")


def page_lists_version(version: str) -> bool | None:
    html = fetch(CHANGELOG_URL)
    if html is None:
        return None
    return re.search(rf'id="v{re.escape(version)}-\d{{4}}-\d{{2}}-\d{{2}}"', html) is not None


def post_tweet(text: str) -> None:
    import tweepy

    client = tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )
    client.create_tweet(text=text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="never post or write state")
    args = ap.parse_args()

    body = fetch(STABLE_URL)
    version = (body or "").strip()
    if not VERSION_RE.match(version):
        print(f"[warn] stable pointer unavailable or invalid ({version[:20]!r}); skipping run")
        return 0
    print(f"stable pointer: {version}")

    last = read_state()
    if not last:
        print("first run: initializing state without posting")
        if not args.dry_run:
            write_state(version)
        return 0

    if version != last:
        text = TWEET_TEMPLATE.format(version=version)
        assert "http" not in text and "/" not in text
        if args.dry_run:
            print(f"[dry-run] would tweet: {text}")
        else:
            post_tweet(text)
            write_state(version)
            print(f"tweeted: {text}")
    else:
        print("no new version")

    listed = page_lists_version(version if args.dry_run or version == last else version)
    if listed is None:
        print("changelog page unavailable (transient); ignoring")
    elif listed:
        print(f"changelog page lists {version}: aligned (phase 2 would post the thread)")
    else:
        print(f"changelog page does not list {version} yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
