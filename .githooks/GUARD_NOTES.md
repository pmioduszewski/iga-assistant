# iga-guard — debugging notes (READ BEFORE touching the guard judge)

## TL;DR of the multi-hour battle (2026-06-02)

**The "judge hang" was NEVER the judge.** The guard appeared to hang for
90s–10min on commit. Hours were spent blaming the LLM judge. The actual cause was
**one line of bash:**

```sh
[ -z "${payload//[[:space:]]/}" ] && exit 0   # <-- O(n^2) in macOS bash 3.2
```

macOS ships **bash 3.2.57** (2007). Its `${var//pattern/}` global substitution is
**O(n²)**. On a ~64 KB staged diff that whitespace-strip runs for *minutes* — the
"hang." Fixed by a plain `[ -z "$payload" ]` empty check. That's it.

### Two more bash-3.2 + `set -euo pipefail` landmines (both fixed)
1. **`printf "$x" | grep -q` as an empty check** silently returns 141 (printf
   SIGPIPE when grep -q exits early) → a `|| exit 0` would SKIP the judge entirely
   (a silent no-op guard — worse than blocking). Don't pipe into `grep -q` for a
   presence test under pipefail.
2. **`printf "$x" | head -c $MAX`** truncation: when the payload exceeds MAX (e.g.
   a 2.6 MB *new-branch* push range = whole-tree diff), `head` closes early →
   printf SIGPIPE → pipefail+set-e → guard exits 141 in 0s → push fails with a
   bare "failed to push some refs". Fix: append `|| true` so the SIGPIPE is
   absorbed (payload still gets the truncated bytes).

Rule of thumb: under `set -o pipefail`, ANY `producer | early-exiting-consumer`
(`head -c`, `grep -q`, `grep -m1`) can SIGPIPE the producer and fail the pipe.
Guard such pipes with `|| true` or `set +o pipefail` locally.

## The judge itself (current design — keep)

`run_judge` pipes the diff to `iga-judge.py`, which tries:
1. **GitHub Copilot CLI** (`copilot -p`) — runs on the **Copilot subscription**
   (separate pool from Claude; reliable; ~6–9s). REQUIRES `--available-tools=`
   (empty → zero tools) so it can't reach for a tool and try to prompt y/n with no
   TTY (which hangs — github/copilot-cli#550). Do **NOT** use `--allow-all-tools`
   (auto-runs shell on diff content from a persistent hook = unsafe; the CC
   classifier rightly blocks it). `--disable-builtin-mcps` drops the GitHub MCP.
2. **`claude -p`** fallback on the Claude subscription (no `ANTHROPIC_API_KEY` —
   that forces paid API, the $1,800 trap, github/copilot-cli-adjacent). MCP
   skipped via `--strict-mcp-config` for speed.

Each backend runs in its own process group (`start_new_session=True`) with a
`killpg(SIGKILL)` timeout, and stdout is read incrementally and returned the
moment a verdict line appears — because these CLIs sometimes print the verdict
then fail to exit, so `communicate()` (which waits for exit) would discard it.

## Dead ends — DO NOT re-try these (they were all tested and are NOT the fix)

- ❌ "It's rate-limited" — wrong. Claude/copilot were up the whole time (direct
  calls worked in 8–14s). Don't assume rate limits without proof.
- ❌ bash `timeout`/`timeout -s KILL` around `claude`/`copilot` — the CLIs detach
  + spawn children that ignore it. (killpg via Python is what works.)
- ❌ `setsid` + `kill -- -$pid` in bash — on macOS `setsid &` detaches and `$!`
  isn't the real pgid. Use Python `subprocess` `start_new_session` + `os.killpg`.
- ❌ Blaming `--strict-mcp-config` / stdin mode / `start_new_session` / 64 KB arg
  — a full subprocess config matrix showed ALL configs work in ~6s.
- ❌ Blaming `set -euo pipefail` / the `| tr | grep` pipeline — the exact pipeline
  runs in 5s both plain and under pipefail.
- ❌ Blaming copilot stdout buffering / "prints then hangs" — plausible-sounding,
  not the cause.
- ❌ `--allow-all-tools` — fixes the copilot hang but is an unsafe approval bypass
  (blocked by the CC auto-classifier). Use `--available-tools=` instead.

## How it was finally found

A **trivial `print("OK")` judge still hung** → proved it's the guard script, not
the judge. Then `PS4='+L${LINENO}: ' bash -x` showed the trace stopping at the
case statement, with line 39 (`${payload//[[:space:]]/}`) never tracing. Timing
that expansion in *real bash 3.2* (not the zsh session shell) confirmed it runs
for minutes. **Lesson: when a hook "hangs," bisect with a trivial judge + line-
numbered `bash -x` FIRST, before touching the model/CLI.**

## Other gotchas seen today
- The guard correctly BLOCKED once because a personal name was left in the guard's
  OWN comments — the judge works; keep comments generic.
- A pre-commit privacy hook judging from inside a Claude Code session is awkward
  (nested CLI quirks). If reliability ever regresses, consider moving the judge
  out-of-band, or a pre-commit framework.
