#!/bin/sh
# Uninstall the iga-research-scanner LaunchAgent.

set -e

PLIST_NAME="com.iga.research-scanner.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"

launchctl unload "$PLIST_DST" 2>/dev/null || true
rm -f "$PLIST_DST"

echo "uninstalled: $PLIST_DST"
echo "logs preserved at $HOME/Library/Logs/iga/research-scanner.*"
