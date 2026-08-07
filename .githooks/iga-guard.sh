#!/usr/bin/env bash
#
# iga-guard — LLM privacy/PII judge for an open-source repo.
#
# A static denylist can never keep up with a living, personal AI layer, so this
# guard has NO dictionary. It asks an LLM to judge whether a change is safe to
# publish to a PUBLIC, GENERIC, reusable repo — blocking anything user-specific
# (real people / clients / companies / projects, emails, phones, finances,
# secrets, home paths, private URLs, calendar/health/relationship data, etc.).
#
# Judge backend, auto-detected (first available wins):
#   1. Claude Code     →  `claude -p`            (IGA_GUARD_MODEL, default sonnet)
#   2. GitHub Models   →  `gh models run`        (IGA_GUARD_GH_MODEL)
#
# Fail-CLOSED: if no judge is available or it errors, the commit/push is BLOCKED
# (a guard you can't run must not silently pass). Emergency override (rare,
# deliberate): IGA_GUARD_OFF=1.
#
# Enable in any clone:  git config core.hooksPath .githooks
#
set -euo pipefail

if [ "${IGA_GUARD_OFF:-0}" = "1" ]; then
  echo "iga-guard: SKIPPED (IGA_GUARD_OFF=1)" >&2
  exit 0
fi

mode="${1:-}"; shift || true
MAXBYTES="${IGA_GUARD_MAXBYTES:-120000}"

# The pattern file is excluded from every diff that is scanned against it —
# otherwise adding a pattern would trip the very rule it defines.
PATFILE_REL=".githooks/session-link-patterns.txt"
EXCL=":(exclude)$PATFILE_REL"

case "$mode" in
  staged) payload="$(git diff --cached --no-color -- . "$EXCL")";          label="staged diff" ;;
  msg)    payload="commit message:"$'\n'"$(cat "$1")";                     label="commit message" ;;
  range)  payload="$(git diff --no-color "$1" -- . "$EXCL" 2>/dev/null)";  label="pushed diff" ;;
  *) echo "iga-guard: unknown mode '$mode'" >&2; exit 2 ;;
esac

# Nothing substantive to check. NOTE: do NOT use ${payload//[[:space:]]/} here —
# that global substitution is O(n^2) in macOS's bash 3.2 and hangs for ~minutes on
# a 64 KB diff (this was THE bug that looked like a "judge hang" for hours; see
# GUARD_NOTES.md). And do NOT `printf "$payload" | grep -q ...` either: under
# `set -o pipefail`, grep exits early on a match → printf gets SIGPIPE → the
# pipeline returns non-zero → a `|| exit 0` would SILENTLY skip the judge. A plain
# empty check is O(1) and sufficient (a real git diff is never whitespace-only).
[ -z "$payload" ] && exit 0

# ---------------------------------------------------------------------------
# Deterministic backstop, checked BEFORE the judge — and BEFORE truncation.
#
# This guard is deliberately dictionary-free for OPEN-ENDED personal data — a
# static list can never keep up with that, which is why an LLM judges it. But a
# model asked the same question twice does not reliably answer the same way:
# on 2026-08-07 the judge passed a `Claude-Session:` trailer on one commit and
# blocked byte-identical content on the very next one, and two commits carrying
# a private session URL reached the public remote before anyone noticed.
#
# So artifacts with a FIXED, machine-generated shape get a hard rule. This is
# not a PII dictionary and must not grow into one: add a pattern here only when
# it is emitted automatically by a tool, has an unmistakable literal form, and
# is never legitimately publishable. Everything judgement-shaped stays with the
# judge below.
# ---------------------------------------------------------------------------
# Patterns live in one file shared with the CI job, so the local and
# server-side rules cannot drift apart.
PATFILE="$(git rev-parse --show-toplevel)/$PATFILE_REL"
HARD_DENY="$(grep -vE '^\s*(#|$)' "$PATFILE" 2>/dev/null | paste -sd'|' -)"

if [ -n "$HARD_DENY" ] && printf '%s' "$payload" | grep -qE "$HARD_DENY"; then
  hit="$(printf '%s' "$payload" | grep -m1 -oE "$HARD_DENY")"
  echo "🚫 iga-guard BLOCKED this ${label} — private agent-session link (deterministic rule):" >&2
  echo "   matched: $hit" >&2
  echo "   These are openable only by the maintainer, so publishing one leaks a private" >&2
  echo "   endpoint and buys no traceability. Remove it. Commit-message trailers are" >&2
  echo "   stripped automatically by .githooks/strip-agent-trailers.sh — if you are seeing" >&2
  echo "   this, the link is in the DIFF, not the trailer." >&2
  exit 1
fi

# Truncate to MAXBYTES. `head -c` closes the pipe early when payload > MAXBYTES,
# which SIGPIPEs printf; under `set -o pipefail` + `set -e` that would abort the
# whole guard (exit 141) — so `|| true` absorbs it (payload still gets the bytes
# head read). This bit a 2.6 MB new-branch push range. See GUARD_NOTES.md.
#
# ONLY the judge is truncated — HARD_DENY above ran on the full payload. A
# deterministic rule that silently stops looking after 120 KB is worse than no
# rule, because it reads as coverage. The judge still has this blind spot: a
# payload over MAXBYTES is only partly examined, so the tail goes unjudged.
if [ "${#payload}" -gt "$MAXBYTES" ]; then
  echo "iga-guard: ⚠️  ${label} is ${#payload} bytes; only the first ${MAXBYTES} are judged." >&2
  echo "   The deterministic rules saw all of it, the LLM judge did not. Eyeball the rest." >&2
  payload="$(printf '%s' "$payload" | head -c "$MAXBYTES")" || true
fi

SYS='You are a strict privacy guard for a PUBLIC, open-source repository. The repo is a GENERIC, reusable layer (an AI-assistant framework: skills, rules, a memory engine) — it must contain ZERO data specific to any individual user.

BLOCK the change if it adds (or its message contains) ANY of the following:
- a real person'"'"'s name, EXCEPT the project maintainer used for authorship/copyright/URLs;
- a real client, customer, employer, company, product, or private project/codename;
- an email address, phone number, or postal address;
- financial figures, account numbers, invoices, balances, salaries, prices tied to a real entity;
- credentials, API keys, tokens, secrets;
- an absolute home path revealing a username (e.g. /Users/<name>, /home/<name>);
- private URLs, calendar entries, health, family, or relationship details;
- anything that is clearly one specific person'"'"'s private/personal data rather than generic reusable code or docs.

ALLOW: generic placeholders (e.g. Acme, "the user", /Users/you), and the maintainer'"'"'s own authorship. Text that merely DESCRIBES these categories (documentation, this guard'"'"'s own instructions, example placeholders) is NOT a violation — only ACTUAL personal data is. When genuinely unsure, BLOCK.

Respond with EXACTLY one line: "OK"  — or —  "BLOCK: <what was found and in which file/line>".'

ASK="Judge whether the following ${label} is safe to publish to a public OSS repo. One line only: OK or BLOCK."

# Bounded execution — the judge must NEVER hang a commit/push. Portable timeout.
_iga_to() { # _iga_to SECONDS cmd...
  local s="$1"; shift
  if command -v timeout >/dev/null 2>&1; then timeout -s KILL "$s" "$@"
  elif command -v gtimeout >/dev/null 2>&1; then gtimeout -s KILL "$s" "$@"
  else perl -e 'my $s=shift; my $p=fork; if(!defined$p){exit 127} if(!$p){setpgrp(0,0); exec @ARGV; exit 127} $SIG{ALRM}=sub{kill "KILL",-$p; exit 124}; alarm $s; waitpid $p,0; exit($?>>8)' "$s" "$@"
  fi
}

# The judge is the Python wrapper `iga-judge.py`. It tries GitHub Copilot CLI first
# (`copilot -p` — runs on the SEPARATE Copilot subscription, so it's reliable, never
# touches Claude limits, and has none of the nested-`claude -p` flakiness), then
# nested `claude -p` on the Claude subscription as a fallback. Each backend runs in
# its own process group with a killpg timeout, so a mute/hung CLI can never stall a
# commit. The wrapper prints exactly one verdict line (OK / BLOCK: ...).
#
# Deliberately simple: earlier versions juggled API/claude/gh backends inline in
# bash, and the `set -euo pipefail` interaction with the empty-fallthrough cases
# was the real hang (not the model). One pipe to the wrapper — that's it.
run_judge() {
  command -v python3 >/dev/null 2>&1 || return 0
  local judge
  judge="$(git rev-parse --show-toplevel 2>/dev/null)/.githooks/iga-judge.py"
  [ -f "$judge" ] || return 0
  printf '%s\n\n%s' "$ASK" "$payload" \
    | IGA_GUARD_TIMEOUT="${IGA_GUARD_TIMEOUT:-80}" python3 "$judge" "$SYS" 2>/dev/null \
    | tr -d '\r' | grep -m1 -ioE '^(OK|BLOCK).*' || true
}

verdict="$(run_judge || true)"

if [ -z "$verdict" ]; then
  echo "🚫 iga-guard: judge unavailable or mute after retries (tried API key / claude×3 / gh models)." >&2
  echo "   For a reliable judge when committing from inside an agent, export ANTHROPIC_API_KEY." >&2
  echo "   Blocking to stay safe. Override ONCE (after you've eyeballed the diff): IGA_GUARD_OFF=1 git ..." >&2
  exit 1
fi

if printf '%s' "$verdict" | grep -qiE '^BLOCK'; then
  echo "🚫 iga-guard BLOCKED this ${label} — looks like non-generic / personal data:" >&2
  echo "   $verdict" >&2
  echo "   Use generic placeholders. If it's a false positive: IGA_GUARD_OFF=1 git ..." >&2
  exit 1
fi

echo "iga-guard: OK (${label})" >&2
exit 0
