#!/usr/bin/env bash
#
# strip-agent-trailers — remove harness-injected commit trailers that must never
# reach a PUBLIC repo.
#
# WHY THIS EXISTS (incident 2026-08-07)
# -------------------------------------
# Coding agents are instructed by their own harness to append a trailer linking
# the commit back to the private conversation that produced it — a
# "<Vendor>-Session:" line carrying a per-conversation URL on the vendor's host.
# (The literal URL shape is deliberately NOT written out here: iga-guard.sh has
# a deterministic rule matching it, and spelling it in full would make this file
# block its own commit.)
#
# That is useful in a private repo and actively harmful in a public one: the URL
# is openable only by the maintainer, so publishing it adds zero traceability
# and leaks a private endpoint to the world.
#
# The agent cannot reliably suppress it: the instruction to add it lives in the
# harness, above any repo-level rule, and reasserts itself every session. On
# 2026-08-07 exactly that happened — two commits reached the public remote with
# a session URL before anyone noticed, and history had to be rewritten.
#
# WHY IT IS DETERMINISTIC AND NOT PART OF THE LLM JUDGE
# ----------------------------------------------------
# iga-guard is deliberately dictionary-free: a static list can never keep up
# with open-ended personal data, so an LLM judges it. That is the right call for
# open-ended PII, and the wrong call here. This trailer is a machine-generated
# artifact with a FIXED shape, emitted on essentially every agent commit. Asking
# a model the same question hundreds of times will not return the same answer
# hundreds of times — the 2026-08-07 incident is the proof: the judge passed the
# trailer on one commit and blocked byte-identical content on the very next one.
#
# So: known fixed shape → strip it mechanically here. Everything open-ended →
# still the judge's job. This does not reintroduce a denylist; it normalizes a
# known artifact before the judge ever sees it.
#
# Usage: strip-agent-trailers.sh <path-to-commit-message-file>
set -euo pipefail

msg_file="${1:?usage: strip-agent-trailers.sh <commit-msg-file>}"
[ -f "$msg_file" ] || exit 0

# Patterns are anchored to the start of a line so a commit that legitimately
# *discusses* the trailer (like this repo's own docs) is untouched.
#
# Add a pattern here when a new harness starts injecting its own back-link.
STRIP_PATTERNS=(
  '^Claude-Session:'
  '^Session-Link:'
  '^Codex-Session:'
)

before="$(wc -c <"$msg_file")"

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
cp "$msg_file" "$tmp"
for pat in "${STRIP_PATTERNS[@]}"; do
  grep -v "$pat" "$tmp" >"$tmp.new" || true   # grep -v exits 1 when all lines match
  mv "$tmp.new" "$tmp"
done

# Collapse any trailing blank lines the removal left behind, then restore the
# single newline git expects at EOF.
printf '%s\n' "$(cat "$tmp")" >"$msg_file"

after="$(wc -c <"$msg_file")"
if [ "$before" -ne "$after" ]; then
  echo "iga-guard: stripped harness session trailer from the commit message" >&2
fi

exit 0
