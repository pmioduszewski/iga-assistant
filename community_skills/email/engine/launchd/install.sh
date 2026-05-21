#!/bin/sh
# Install the iga-email-triage LaunchAgent.
# Idempotent — safe to re-run after edits to the plist template.

set -e

SKILL_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PLIST_NAME="com.iga.email-triage.plist"
PLIST_SRC="$SKILL_DIR/engine/launchd/$PLIST_NAME"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"
LOG_DIR="$HOME/Library/Logs/iga"

if [ ! -f "$PLIST_SRC" ]; then
  echo "error: missing $PLIST_SRC" >&2
  exit 1
fi

mkdir -p "$LOG_DIR"
mkdir -p "$HOME/Library/LaunchAgents"

# Substitute placeholders. macOS sed needs -i '' but we just write to dst directly.
sed -e "s|<SKILL_DIR>|$SKILL_DIR|g" \
    -e "s|<HOME>|$HOME|g" \
    "$PLIST_SRC" > "$PLIST_DST"

# Reload (idempotent: unload first, ignore failure)
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"

echo "installed:  $PLIST_DST"
echo "logs:       $LOG_DIR/email-triage{.log,.err.log,-YYYY-MM-DD.json}"
echo ""
echo "verify loaded:   launchctl list | grep com.iga.email-triage"
echo "force one run:   launchctl start com.iga.email-triage"
echo "tail logs:       tail -f $LOG_DIR/email-triage.log $LOG_DIR/email-triage.err.log"
echo ""
echo "Don't forget: schedule the wake separately (requires sudo):"
echo "  sudo pmset repeat wake MTWRFSU 05:55:00"
echo "Verify:  pmset -g sched"
