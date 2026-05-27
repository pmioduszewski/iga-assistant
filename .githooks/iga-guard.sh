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

case "$mode" in
  staged) payload="$(git diff --cached --no-color)";          label="staged diff" ;;
  msg)    payload="commit message:"$'\n'"$(cat "$1")";        label="commit message" ;;
  range)  payload="$(git diff --no-color "$1" 2>/dev/null)";  label="pushed diff" ;;
  *) echo "iga-guard: unknown mode '$mode'" >&2; exit 2 ;;
esac

# Nothing substantive to check.
[ -z "${payload//[[:space:]]/}" ] && exit 0
payload="$(printf '%s' "$payload" | head -c "$MAXBYTES")"

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
  if command -v timeout >/dev/null 2>&1; then timeout "$s" "$@"
  elif command -v gtimeout >/dev/null 2>&1; then gtimeout "$s" "$@"
  else perl -e 'my $s=shift; my $p=fork; if(!defined$p){exit 127} if(!$p){setpgrp(0,0); exec @ARGV; exit 127} $SIG{ALRM}=sub{kill "KILL",-$p; exit 124}; alarm $s; waitpid $p,0; exit($?>>8)' "$s" "$@"
  fi
}

run_judge() {
  local prompt to
  prompt="$ASK"$'\n\n'"$payload"
  to="${IGA_GUARD_TIMEOUT:-90}"
  if command -v claude >/dev/null 2>&1; then
    # Clean env so a nested `claude` (when committing from inside Claude Code)
    # starts as an independent instance and can't deadlock on the parent session.
    printf '%s' "$prompt" | _iga_to "$to" env -u CLAUDECODE claude -p --model "${IGA_GUARD_MODEL:-claude-haiku-4-5-20251001}" --append-system-prompt "$SYS" 2>/dev/null || true
    return
  fi
  if command -v gh >/dev/null 2>&1 && gh models --help >/dev/null 2>&1; then
    printf '%s\n\n%s' "$SYS" "$prompt" | _iga_to "$to" gh models run "${IGA_GUARD_GH_MODEL:-openai/gpt-4o}" 2>/dev/null || true
    return
  fi
  return
}

verdict="$(run_judge | tr -d '\r' | grep -m1 -ioE '^(OK|BLOCK).*' || true)"

if [ -z "$verdict" ]; then
  echo "🚫 iga-guard: no LLM judge available (need \`claude\` or \`gh models\`), or it returned nothing." >&2
  echo "   Blocking to stay safe. Install a judge, or override ONCE with: IGA_GUARD_OFF=1 git ..." >&2
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
