# grok-build-release-watcher

Small watcher that (1) detects new Grok Build CLI releases from the public stable-version pointer and posts one short announcement tweet (no links), and (2) triggers existing changelog bots as soon as something new appears upstream.

## How it works
1. Reads the public `cli/stable` pointer (a file containing only a version number). If it differs from `state/last_announced.txt`, posts one tweet and records the version. First run only initializes state.
2. Runs three independent triggers. Each keeps its own state file in `state/` (release identifiers only):

| Trigger | Source | State file | Bot workflow dispatched |
|---|---|---|---|
| Cursor | `https://cursor.com/changelog/rss.xml` (newest slug) | `cursor_feed.txt` | `Xtinkerlog/cursor-changelog-bot` |
| Hermes | GitHub releases of `NousResearch/hermes-agent` (non-draft, prereleases included) | `hermes_releases.txt` | `Xtinkerlog/hermes-agent-changelog` |
| OpenClaw | GitHub releases of `openclaw/openclaw` (non-draft, prereleases ignored) | `openclaw_releases.txt` | `Xtinkerlog/openclaw-changelog-bot` |

   When the newest identifier differs from the stored one, the watcher calls `POST /repos/{repo}/actions/workflows/check-releases.yml/dispatches` with `{"ref":"main"}`. State is updated only after a successful dispatch (a failure is retried on the next run), with at most one dispatch per bot per run. The first run of each trigger only initializes state, without dispatching.
3. Failures are soft: an unavailable source (including transient HTTP 403s) is skipped and never blocks the others.

Runs only via `workflow_dispatch` (triggered externally, e.g. by a cron service). Required repository secrets: `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET`, and `DISPATCH_TOKEN_MAIN` (fine-grained token with Actions read/write on the three bot repositories; also used for public reads when present, otherwise reads are anonymous). If it is missing, the triggers are skipped and their state is left untouched.

## Local test
`pip install -r requirements.txt && python watcher.py --dry-run` never posts, dispatches or writes state. Add `--simulate-behind` to pretend each trigger's state is one release old and see the simulated dispatches.
