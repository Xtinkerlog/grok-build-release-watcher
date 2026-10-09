"""Grok Build release watcher.

Reads the public stable-version pointer and posts one short tweet when a new
version appears. Also watches three upstream sources and, when something new
appears, triggers the existing changelog bots via GitHub workflow dispatch.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET
import time
from pathlib import Path

from curl_cffi import requests as cffi_requests

STABLE_URL = "https://storage.googleapis.com/grok-build-public-artifacts/cli/stable"
STATE_FILE = Path(__file__).resolve().parent / "state" / "last_announced.txt"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
TARGETS = ["chrome120", "chrome131", "safari17_0"]
TIMEOUT = 30
STATE_DIR = STATE_FILE.parent
CURSOR_FEED = "https://cursor.com/changelog/rss.xml"
GH_API = "https://api.github.com"
WORKFLOW = "check-releases.yml"
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


def post_tweet(text: str) -> None:
    import tweepy

    client = tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )
    client.create_tweet(text=text)


def token_for(group: str) -> str:
    return os.environ.get(f"DISPATCH_TOKEN_{group}", "").strip()


def gh_headers(token: str = "") -> dict:
    h = {"User-Agent": "release-watcher", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def gh_releases(repo: str) -> list[dict] | None:
    """Public releases of a repo (drafts removed), newest first. None on failure."""
    url = f"{GH_API}/repos/{repo}/releases?per_page=30"
    try:
        r = cffi_requests.get(url, headers=gh_headers(token_for("MAIN")), timeout=TIMEOUT)
        if r.status_code == 401:  # bad token: public data is readable without it
            h = gh_headers()
            h.pop("Authorization", None)
            r = cffi_requests.get(url, headers=h, timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"[warn] {repo} releases: HTTP {r.status_code}")
            return None
        items = [x for x in r.json() if not x.get("draft")]
    except Exception as exc:
        print(f"[warn] {repo} releases: {exc}")
        return None
    items.sort(key=lambda x: x.get("published_at") or x.get("created_at") or "", reverse=True)
    return items


def ids_cursor() -> list[str] | None:
    body = fetch(CURSOR_FEED)
    if body is None:
        return None
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        print(f"[warn] cursor feed parse error: {exc}")
        return None
    ids = []
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        slug = link.rstrip("/").rsplit("/", 1)[-1]
        if slug:
            ids.append(slug)
    return ids or None


def ids_hermes() -> list[str] | None:
    # The Hermes bot reads every non-draft release, prereleases included.
    items = gh_releases("NousResearch/hermes-agent")
    return [x["tag_name"] for x in items] if items else None


def ids_openclaw() -> list[str] | None:
    items = gh_releases("openclaw/openclaw")
    if items is None:
        return None
    ids = [x["tag_name"] for x in items if not x.get("prerelease")]
    return ids or None


def dispatch(repo: str, group: str, dry_run: bool) -> bool:
    if dry_run:
        print(f"[dry-run] would POST {GH_API}/repos/{repo}/actions/workflows/{WORKFLOW}/dispatches")
        return True
    try:
        r = cffi_requests.post(
            f"{GH_API}/repos/{repo}/actions/workflows/{WORKFLOW}/dispatches",
            headers=gh_headers(token_for(group)),
            json={"ref": "main"},
            timeout=TIMEOUT,
        )
    except Exception as exc:
        print(f"[warn] dispatch {repo}: {exc}")
        return False
    if r.status_code == 204:
        return True
    print(f"[warn] dispatch {repo}: HTTP {r.status_code}")
    return False


def run_trigger(name: str, ids_fn, repo: str, group: str, dry_run: bool, behind: bool) -> None:
    try:
        ids = ids_fn()
        if not ids:
            print(f"[{name}] source unavailable; skipping")
            return
        newest = ids[0]
        path = STATE_DIR / f"{name}.txt"
        last = path.read_text(encoding="utf-8").strip() if path.exists() else ""
        if behind:  # test only: pretend the stored state is one release old
            if len(ids) < 2:
                print(f"[{name}] not enough history to simulate")
                return
            last = ids[1]
        if not last:
            print(f"[{name}] first run: state initialized to {newest}, no dispatch")
            if not dry_run:
                path.write_text(newest + "\n", encoding="utf-8")
            return
        if newest == last:
            print(f"[{name}] no news ({newest})")
            return
        print(f"[{name}] new: {newest} (was {last}); dispatching {repo}")
        if dispatch(repo, group, dry_run):
            if not dry_run:
                path.write_text(newest + "\n", encoding="utf-8")
            print(f"[{name}] dispatch ok")
        else:
            print(f"[{name}] dispatch failed; state unchanged, will retry next run")
    except Exception as exc:  # one source never blocks the others
        print(f"[warn] [{name}] error: {exc}")


TRIGGERS = [
    ("cursor_feed", ids_cursor, "Xtinkerlog/cursor-changelog-bot", "MAIN"),
    ("hermes_releases", ids_hermes, "Xtinkerlog/hermes-agent-changelog", "MAIN"),
    ("openclaw_releases", ids_openclaw, "Xtinkerlog/openclaw-changelog-bot", "MAIN"),
]


def run_all_triggers(dry_run: bool, behind: bool) -> None:
    for name, fn, repo, group in TRIGGERS:
        if not token_for(group):
            print(f"[{name}] DISPATCH_TOKEN_{group} not set; skipping trigger (state untouched)")
            continue
        run_trigger(name, fn, repo, group, dry_run, behind)


def tweet_step(dry_run: bool) -> None:
    body = fetch(STABLE_URL)
    version = (body or "").strip()
    if not VERSION_RE.match(version):
        print(f"[warn] stable pointer unavailable or invalid ({version[:20]!r}); skipping tweet step")
        return
    print(f"stable pointer: {version}")

    last = read_state()
    if not last:
        print("first run: initializing state without posting")
        if not dry_run:
            write_state(version)
        return

    if version != last:
        text = TWEET_TEMPLATE.format(version=version)
        assert "http" not in text and "/" not in text
        if dry_run:
            print(f"[dry-run] would tweet: {text}")
        else:
            post_tweet(text)
            write_state(version)
            print(f"tweeted: {text}")
    else:
        print("no new version")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="never post, dispatch or write state")
    ap.add_argument("--simulate-behind", action="store_true",
                    help="with --dry-run: pretend each trigger's state is one release old")
    args = ap.parse_args()
    if args.simulate_behind and not args.dry_run:
        ap.error("--simulate-behind requires --dry-run")

    try:
        tweet_step(args.dry_run)
    except Exception as exc:
        print(f"[warn] tweet step error: {exc}")
    run_all_triggers(args.dry_run, args.simulate_behind)
    return 0


if __name__ == "__main__":
    sys.exit(main())
