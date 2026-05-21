#!/usr/bin/env bash
#
# iga-guard — personalization / PII guard for the Iga OSS repo.
#
# Blocks commits/pushes whose content or message contains personalization-layer
# data that must never reach the public repo. Two stages:
#   1. Deterministic denylist (fast, offline).
#   2. LLM review via `claude -p` (Claude Code headless) — catches things the
#      denylist doesn't anticipate.
#
# The denylist lives OUTSIDE the tracked tree so it can hold the actual secret
# terms without ever being committed:
#     $IGA_HOOK_DENYLIST  (default: <repo>/.git/iga-guard-denylist.txt)
#
# Escape hatches (use sparingly, for genuine false positives):
#     IGA_HOOK_LLM=0   git commit ...      # skip stage 2 only
#     IGA_GUARD_OFF=1  git commit ...      # skip the guard entirely
#
# The ONLY real name allowed in the repo is the maintainer's, in LICENSE /
# pyproject authorship / repo URLs. Everything else user-specific is blocked.
set -euo pipefail

[ "${IGA_GUARD_OFF:-0}" = "1" ] && exit 0

mode="${1:-}"; shift || true
ROOT="$(git rev-parse --show-toplevel)"
DENYLIST="${IGA_HOOK_DENYLIST:-$ROOT/.git/iga-guard-denylist.txt}"
EMPTY_TREE="4b825dc642cb6eb9a060e54bf8d69288fbee4904"

gather() {
  case "$mode" in
    staged) git diff --cached --no-color ;;
    msg)    cat "$1" ;;
    range)  git diff --no-color "$1" ;;
    *) echo "iga-guard: unknown mode '$mode'" >&2; exit 2 ;;
  esac
}

text="$(gather "${1:-}")"
[ -z "$text" ] && exit 0

# ---- Stage 1: denylist ------------------------------------------------------
if [ -f "$DENYLIST" ]; then
  pat="$(grep -vE '^[[:space:]]*(#|$)' "$DENYLIST" | sed 's/[][\.^$*+?(){}|/]/\\&/g' | paste -sd'|' -)"
  if [ -n "$pat" ]; then
    hits="$(printf '%s' "$text" | grep -inE "$pat" | head -10 || true)"
    if [ -n "$hits" ]; then
      echo "🚫 iga-guard: blocked — personalization-layer term(s) detected:" >&2
      echo "$hits" >&2
      echo "→ Replace with generic placeholders. False positive? edit $DENYLIST." >&2
      exit 1
    fi
  fi
fi

# ---- Stage 2: LLM review ----------------------------------------------------
if [ "${IGA_HOOK_LLM:-1}" = "1" ] && command -v claude >/dev/null 2>&1; then
  sys='You are a strict pre-commit guard for an open-source repository. The ONLY real person allowed to appear is the maintainer "Paweł Mioduszewski" / handle "pmioduszewski" in LICENSE, authorship, or repo URLs. BLOCK anything else that is personalization-layer or PII: other real people'"'"'s names, real client/company/product/project names, email addresses, phone numbers, postal addresses, financial figures, home paths like /Users/<name>, API tokens or secrets, or content that is clearly user-specific rather than generic OSS material. Respond with EXACTLY one line: "BLOCK: <short reason>" or "OK".'
  verdict="$(printf '%s' "$text" | head -c 60000 | claude -p --model "${IGA_HOOK_MODEL:-claude-haiku-4-5-20251001}" --append-system-prompt "$sys" 'Review the diff/text provided on stdin and respond OK or BLOCK.' 2>/dev/null || echo OK)"
  if printf '%s' "$verdict" | grep -qiE '^[[:space:]]*BLOCK'; then
    echo "🚫 iga-guard (LLM): $verdict" >&2
    echo "→ False positive? re-run with IGA_HOOK_LLM=0 (denylist still applies)." >&2
    exit 1
  fi
fi

exit 0
