# Setup: daily auto-triage via launchd

Wakes the Mac at 05:55 and triages all 4 inboxes at 06:00 every day. By the time you wake up, the inbox is groomed.

## Prerequisites

- macOS (launchd is native)
- Engine working interactively first: `pnpm tsx src/cli.ts triage --account umbrella --dry-run --json --max 3` returns sensible output
- OAuth tokens present at `~/.local/share/iga-email/credentials/`
- `claude` CLI on `$PATH` (for the LLM classifier subprocess)

## One-time install (3 commands)

```sh
cd ~/Gaia/skills/email   # installed location (in-repo template: community_skills/email)
chmod +x engine/launchd/install.sh engine/launchd/uninstall.sh
./engine/launchd/install.sh
```

The installer:
- Writes `~/Library/LaunchAgents/com.iga.email-triage.plist` with `<SKILL_DIR>` and `<HOME>` substituted
- Creates `~/Library/Logs/iga/` if missing
- Loads the LaunchAgent via `launchctl load`
- Idempotent — safe to re-run after plist edits

## pmset-wake

(One-time wake schedule, requires sudo.)

LaunchAgents only fire if the Mac is awake. Schedule a daily wake:

```sh
sudo pmset repeat wake MTWRFSU 05:55:00
```

This wakes the Mac at 05:55 every day. The triage job fires at 06:00 (5 min buffer for warm-up). Verify:

```sh
pmset -g sched
```

You should see a `wakepoweron` line for 05:55:00.

## Force a test run (no waiting until 06:00)

```sh
launchctl start com.iga.email-triage
sleep 90  # let it finish a triage cycle
tail -100 ~/Library/Logs/iga/email-triage.err.log
ls -lh ~/Library/Logs/iga/email-triage-$(date +%Y-%m-%d).json
```

The JSON log contains the full report (decisions per account). Eyeball it — if labels were misapplied, that's your signal to tighten taxonomy or pre-filter rules before the next morning.

## Daily verification

Add a one-liner check to `/gm`:

```sh
ls -1t ~/Library/Logs/iga/email-triage-*.json | head -1
```

If the most recent log isn't from today, the morning job didn't fire. Causes to check (in order):
1. Mac was asleep AND on battery (pmset wake requires power)
2. User wasn't logged in (launchctl requires active user session)
3. `pnpm` or `claude` not on the LaunchAgent's PATH — check `email-triage.err.log`
4. Macos update reset `pmset repeat` — re-run the sudo command

## Cost notes

Each daily run: ~30-60 sec wallclock. ~10 calls to `claude -p` (4 accounts × ~25 unread / 15 batch size, minus pre-filter hits). Uses your MAX subscription quota, not API billing. Negligible cost.

## Uninstall

```sh
~/Gaia/skills/email/engine/launchd/uninstall.sh   # installed location (in-repo template: community_skills/email)
sudo pmset repeat cancel  # optional, removes the wake schedule
```

Logs at `~/Library/Logs/iga/email-triage*` are retained — delete manually if not wanted.

## Constraints recap

| Setting | Required for morning auto-triage to work |
|---|---|
| Power | Plugged in (Mac mini = always satisfied) |
| Lid (laptops) | Open or clamshell with external display |
| Login session | User logged in before sleep |
| `pmset repeat wake` | Set + non-zero |
| LaunchAgent loaded | `launchctl list \| grep com.iga.email-triage` returns a row |
| `claude` CLI | On `$PATH` from a `/bin/zsh -lc` shell |

If Mac mini stays powered + logged in 24/7 (the user's setup), all checkboxes are passive.

## Logs structure

```
~/Library/Logs/iga/
├── email-triage.log              ← stdout (mostly empty when --json mode)
├── email-triage.err.log          ← stderr (human summary + warnings)
└── email-triage-2026-05-15.json  ← one per day; full report (decisions, counts, missing labels)
```

The dated JSON files accumulate. Rotate or prune manually if disk space is a concern (a year of daily runs ≈ ~15 MB).
