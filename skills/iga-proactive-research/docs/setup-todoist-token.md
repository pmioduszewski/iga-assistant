# Setup — Todoist API Token

Iga uses your Todoist API token to read tasks tagged `iga-research` and post comments on them when background research completes. Without the token, Todoist-driven triggers stay dormant; MemPalace-flag triggers still work, so this is **strongly recommended but not strictly required**.

## TL;DR (60 seconds)

```bash
# 1. Get token from Todoist web app (steps below)
# 2. Save it locally:
mkdir -p ~/.config/todoist
echo "YOUR_TOKEN_HERE" > ~/.config/todoist/token
chmod 600 ~/.config/todoist/token

# 3. (Optional) Also export as env var for current shell
export TODOIST_API_TOKEN="YOUR_TOKEN_HERE"

# 4. Verify
python3 ~/Iga/skills/iga-proactive-research/engine/scanner.py
# Expect: scanner runs, exits 0, no "config error" message
```

## Getting the token from Todoist

Per the official help article ([Todoist — Find your API token](https://www.todoist.com/help/articles/find-your-api-token-Jpzx9IIlB)):

1. **Open Todoist in your web browser** — https://todoist.com (the token UI is web-only; not in mobile apps or desktop clients).
2. **Click your avatar** in the top-left corner.
3. **Select Settings** from the menu.
4. **Click the Integrations tab.**
5. **Click the Developer tab** at the top of the Integrations panel.
6. **Click "Copy API token"** — it lands on your clipboard.

The token is a 40-char hex string like `a1b2c3d4e5...`.

### Security notes

- **Treat the token like a password.** Anyone with it can read all your tasks, modify any task, and delete projects. Do not paste it into Slack, email, screenshots, or commit it to git.
- **No expiration.** The token never expires on its own — it stays valid until you rotate it.
- **Single token per account.** Todoist gives you one personal access token, not a set of scoped tokens. The whole API surface is yours; there is no fine-grained permission system on personal tokens.
- **Rotate if leaked:** in the same Developer panel, click **"Issue a new API token"**. This invalidates the old one immediately AND logs you out from all connected devices (apps will prompt for re-login). Confirm with **Create**.

### Where Iga stores it

Iga's scanner looks for the token in this order:

1. **Environment variable `TODOIST_API_TOKEN`** — checked first. Useful for shell sessions or CI.
2. **File `~/.config/todoist/token`** — checked if env var missing. Recommended for the persistent install. Permission must be `0600` (owner read/write only).

If neither is present, the scanner logs a one-line warning and exits cleanly. No crash, no broken `/gm`.

### File-mode permission check

After writing the token file, confirm permissions:

```bash
ls -l ~/.config/todoist/token
# Expect: -rw-------  1 you  staff  41 ...  /Users/you/.config/todoist/token
```

If the leading bits are anything other than `-rw-------`, run `chmod 600 ~/.config/todoist/token`.

## Verifying the setup

After saving the token, run a **dry scan** (no workers, just detect candidates):

```bash
IGA_PROACTIVE_SPAWN=0 python3 ~/Iga/skills/iga-proactive-research/engine/scanner.py
```

What you should see:
- Exit code `0`
- The scanner reads Todoist tasks with label `iga-research` (initially: none — that's fine)
- The scanner reads MemPalace `research-queue` drawers (initially: none)
- An empty queue file written to `~/Iga/scratch/iga-research-queue.json`
- Stdout: `[]` (the empty WORKER_REQUEST array)

If you see `exit code 1 — Todoist API token not configured`, the token wasn't picked up. Re-check the path and permissions.

If you see `exit code 3 — Todoist API error`, the token is reaching the API but Todoist rejected it. Most common cause: token was typed instead of pasted (missing chars), or it was rotated since you copied it. Issue a new one and retry.

## First real trigger

Once the dry scan is green, tag any real task to test end-to-end:

1. Open Todoist, pick a task you'd like Iga to research (e.g., the existing Acme prep task).
2. Add the label `iga-research` to it.
3. Run `/gm` in Claude Code. Within the first second, Iga's `/gm` step 1a fires the scanner and spawns a background subagent.
4. Continue your morning briefing. Within 0–10 minutes, the worker files a MemPalace drawer and posts a Todoist comment.
5. The next `/gm` or `/back` will surface the briefing in the `📑 Iga prepared in the background:` block.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Scanner exit code `1` | Token file missing or env unset | Re-save per TL;DR; check `ls -l ~/.config/todoist/token` |
| Scanner exit code `3` | Token rejected by Todoist | Issue new token in Developer panel; replace file |
| Scanner exit code `4` | `IGA_RUN_MODE` set to something other than `inline\|daemon` | `unset IGA_RUN_MODE` or fix value |
| Workers don't run | `IGA_PROACTIVE_SPAWN=0` set | `unset IGA_PROACTIVE_SPAWN` |
| Everything disabled | `IGA_PROACTIVE_RESEARCH=0` set | `unset IGA_PROACTIVE_RESEARCH` |

## Rotating the token

If you ever suspect the token leaked:

1. Open Todoist → Settings → Integrations → Developer.
2. Click **Issue a new API token** → **Create**.
3. **All your apps and integrations using the old token will stop working**, including Iga, n8n, Home Assistant, Zapier — any service holding the previous token.
4. Replace the new token in `~/.config/todoist/token` (and any other place storing it).

## OSS / fresh install path (future Phase 2)

Once the launchd/systemd installer ships (Todoist task `6gfJXF9xwH8PMF66`), running `/iga install proactive-research` will:

1. Prompt for the Todoist token interactively.
2. Write it to `~/.config/todoist/token` with `0600`.
3. Verify it against `https://api.todoist.com/api/v1/projects` (one-liner check).
4. Install the platform scheduler (LaunchAgent on Mac, systemd timer on Linux).
5. Run a dry scan and report.

Until then, follow the manual steps above.

## References

- Official help: [Todoist — Find your API token](https://www.todoist.com/help/articles/find-your-api-token-Jpzx9IIlB) (web app, Settings → Integrations → Developer)
- Developer docs: [Todoist API v1](https://developer.todoist.com/api/v1/) for the underlying REST/Sync API Iga's scanner uses
