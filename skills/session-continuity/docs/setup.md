# Setup

Requires Python 3.11+ (standard library only). The reader works without any
setup: `/eod`, `/gm` and on-demand lookups call it directly. The hook is the
optional part that tells a new session about the day's other sessions.

```sh
python3 skills/session-continuity/engine/install.py --dry-run
python3 skills/session-continuity/engine/install.py
```

The installer merges one `UserPromptSubmit` handler into this clone's
`.claude/settings.local.json` and `.codex/hooks.json`, preserving everything
else. `--host claude` or `--host codex` limits it to one host. Restart Claude
Code afterwards. Codex runs a project hook only after you approve it: start a
new chat and use `/hooks` in the Codex CLI (the desktop app's approval step is
not verified here). On Windows use `python`.

## Where sessions are read from

| Harness | Store | A session belongs to this workspace when |
|---|---|---|
| Claude Code | `<data dir>/projects/<workspace path, every non-alphanumeric character replaced by ->/*.jsonl` | it is in that folder |
| Codex | `<data dir>/sessions/**/rollout-*.jsonl` and `archived_sessions/` | its `session_meta` record has the workspace as `cwd` and it is not a subagent thread |

The data directories default to `~/.claude` and `~/.codex`. The workspace is
`$IGA_HOME`, or the clone the engine lives in. Nothing is written to either
store.

Each run reports every harness: `ok`, `not installed` (no data directory, so
nothing to review), or `⚠️ <harness> sessions unavailable (<why>)`. A file
whose content is not a transcript the reader understands is counted and
reported as unreadable, never skipped quietly, so a format change in a
harness shows up as a warning instead of an empty day.

## Configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `IGA_HOME` | this clone | the workspace whose sessions count |
| `IGA_SESSIONS_HARNESSES` | `claude` | space or comma separated list; Codex is opt-in: `claude codex` |
| `IGA_SESSIONS_REFRESH_MIN` | `60` | minutes between hook re-checks after the first prompt; `0` means first prompt only |
| `IGA_SESSION_ID` | unset | the current session's id, for harnesses that do not expose one to commands |
| `CLAUDE_CONFIG_DIR`, `CODEX_HOME` | unset | read as the harness data directory when set |
| `IGA_SESSION_CONTINUITY_OFF` | unset | `1` silences the hook |

## Behavior and limits

- The hook adds at most 8 index lines, on the first prompt and later only
  when another session has new activity and the refresh interval has passed.
  It never adds conversation content. Per-session state (which sessions were
  already shown) lives in `state/session-continuity/`, pruned after two days.
- A session counts when it ran in the workspace folder itself. Sessions
  started in subfolders, git worktrees or other projects are out of scope.
- Headless runs in the workspace (for example `codex exec`) leave transcripts
  too and appear as sessions.
- A resumed or forked session starts as a copy of its parent. Copied turns
  are shown once, in the older file.
- Codex progress commentary is dropped, final answers are kept. Blocks a
  harness puts in the user's turn (system reminders, command output, task
  notifications, environment context, AGENTS.md injections) are dropped by a
  list of known prefixes. An unknown wrapper is kept on purpose: a harness
  change then shows up as noise in the output, not as missing user words.
- Credential masking is pattern based (common key and token shapes, URLs with
  a password, `password=` style assignments). It lowers the risk; it is not a
  guarantee. Extracted files are written with mode 600 and should be deleted
  after the review.
- `--exclude-current` relies on the harness exposing the session id to
  commands (`CLAUDE_CODE_SESSION_ID`, seen in the Claude Code desktop app).
  Elsewhere, set `IGA_SESSION_ID` or the engine prints a `note:` and includes
  the current session.
- Malformed hook input, missing state and storage errors fail open: the hook
  prints nothing and the session continues.

## Disable or remove

Set `IGA_SESSION_CONTINUITY_OFF=1` for a temporary bypass. Remove the handler
with:

```sh
python3 skills/session-continuity/engine/install.py --uninstall
```
