#!/bin/sh
# Uninstall the iga-email-triage LaunchAgent.
# Leaves logs and the wake schedule alone (pmset repeat untouched).

set -e

PLIST_DST="$HOME/Library/LaunchAgents/com.iga.email-triage.plist"

if [ ! -f "$PLIST_DST" ]; then
  echo "not installed: $PLIST_DST"
  exit 0
fi

launchctl unload "$PLIST_DST" 2>/dev/null || true
rm -f "$PLIST_DST"

echo "uninstalled: $PLIST_DST"
echo ""
echo "wake schedule untouched. to remove it:"
echo "  sudo pmset repeat cancel"
echo ""
echo "logs retained at: $HOME/Library/Logs/iga/email-triage*"
