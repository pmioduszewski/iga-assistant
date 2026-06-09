#!/usr/bin/env bash
# Migrate ~/Gaia -> ~/Iga: moves the dir (palace + venvs included), then rewrites
# every absolute path reference in the global config and inside the moved tree.
#
# RUN THIS WITH NO CLAUDE CODE SESSION OPEN IN ~/Gaia, FROM OUTSIDE THE DIR.
# The dir is still ~/Gaia when you start, so invoke it via the Gaia path:
#   cd ~ && bash Gaia/scripts/rename-gaia-to-iga.sh --dry-run   # preview
#   cd ~ && bash Gaia/scripts/rename-gaia-to-iga.sh             # do it
#
# Rollback (if anything looks wrong before reopening):
#   mv ~/Iga ~/Gaia ; cp ~/.claude.json.pre-iga.bak ~/.claude.json
set -euo pipefail

OLD="$HOME/Gaia"
NEW="$HOME/Iga"
MODE="${1:-}"

case "$PWD" in
  "$OLD"|"$OLD"/*) echo "❌ You are inside $OLD. cd out first (e.g. cd ~) and quit any Claude session here."; exit 1;;
esac
[ -e "$NEW" ] && { echo "❌ $NEW already exists — aborting so nothing is clobbered."; exit 1; }
[ -d "$OLD" ] || { echo "❌ $OLD not found — already moved?"; exit 1; }

say(){ echo "▶ $*"; }
do_(){ if [ "$MODE" = "--dry-run" ]; then echo "  DRY: $*"; else bash -c "$*"; fi; }

say "Back up global config -> ~/.claude.json.pre-iga.bak"
do_ "cp ~/.claude.json ~/.claude.json.pre-iga.bak"

say "Move $OLD -> $NEW (palace, venvs, repo — all of it)"
do_ "mv '$OLD' '$NEW'"

say "Rewrite global config (~/.claude.json): MCP commands, palace path, project keys"
do_ "sed -i '' 's#$OLD#$NEW#g' ~/.claude.json"

say "Rewrite absolute paths inside the moved tree (venv shebangs, settings, docs) — excl .git/node_modules/dist"
do_ "grep -rIl --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=dist '$OLD' '$NEW' | while IFS= read -r f; do sed -i '' 's#$OLD#$NEW#g' \"\$f\"; done"

echo
echo "✅ Done. Next:"
echo "   1. cd $NEW"
echo "   2. Reopen Claude Code from there"
echo "   3. Verify: mempalace_status responds, then run /iga gm"
echo "   Rollback if needed: mv $NEW $OLD ; cp ~/.claude.json.pre-iga.bak ~/.claude.json"
