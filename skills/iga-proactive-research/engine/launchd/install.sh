#!/bin/sh
# Install the iga-research-scanner LaunchAgent.
# Idempotent — safe to re-run after edits to the plist template.

set -e

SKILL_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PLIST_NAME="com.iga.research-scanner.plist"
PLIST_SRC="$SKILL_DIR/engine/launchd/$PLIST_NAME"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"
WRAPPER="$SKILL_DIR/engine/launchd/iga-research-scanner"
LOG_DIR="$HOME/Library/Logs/iga"

if [ ! -f "$PLIST_SRC" ]; then
  echo "error: missing $PLIST_SRC" >&2
  exit 1
fi
if [ ! -f "$WRAPPER" ]; then
  echo "error: missing wrapper $WRAPPER" >&2
  exit 1
fi

chmod 755 "$WRAPPER"
mkdir -p "$LOG_DIR"
mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$HOME/Gaia/scratch"

sed -e "s|<SKILL_DIR>|$SKILL_DIR|g" \
    -e "s|<HOME>|$HOME|g" \
    "$PLIST_SRC" > "$PLIST_DST"

launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"

echo "installed:  $PLIST_DST"
echo "wrapper:    $WRAPPER (chmod 755)"
echo "logs:       $LOG_DIR/research-scanner{.log,.err.log,-YYYY-MM-DD.json}"
echo ""
echo "verify loaded:   launchctl list | grep com.iga.research-scanner"
echo "force one run:   launchctl start com.iga.research-scanner"
echo "tail logs:       tail -f $LOG_DIR/research-scanner.log $LOG_DIR/research-scanner.err.log"
echo ""
echo "Wake schedule (shared with email skill, requires sudo if not set):"
echo "  sudo pmset repeat wake MTWRFSU 05:55:00"
echo "Verify:  pmset -g sched"
