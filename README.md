# grok-build-release-watcher

Small watcher that detects new Grok Build CLI releases from the public stable-version pointer and posts one short announcement tweet (no links).

## How it works
1. Reads the public `cli/stable` pointer (a file containing only a version number).
2. If it differs from `state/last_announced.txt`, posts one tweet and commits the new version. First run only initializes state.
3. Checks the changelog page and logs whether it lists the announced version. Transient HTTP errors are ignored.

Runs only via `workflow_dispatch` (triggered externally, e.g. by a cron service). Required repository secrets: `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET`.

Local test: `pip install -r requirements.txt && python watcher.py --dry-run` (never posts or writes state).

## TODO (phase 2)
Post the full changelog thread from this repo once the changelog page lists the announced version, then track it in state to avoid duplicates.
