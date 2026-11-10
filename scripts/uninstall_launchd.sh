#!/usr/bin/env bash
# Stop and remove the whoosh-connect LaunchAgent.
set -euo pipefail

LABEL="com.tusharpandey13.whoosh-connect"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

UID_="$(id -u)"
launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
echo "removed $LABEL"
