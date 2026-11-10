#!/usr/bin/env bash
# Install whoosh-connect as a per-user LaunchAgent: starts at login, restarts
# on a non-zero exit, needs no terminal. Opt-in — run `make uninstall-launchd`
# to stop and remove it. `--print` emits the plist without touching anything.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_BIN="$REPO_ROOT/.venv/bin/whoosh-connect"
LABEL="com.tusharpandey13.whoosh-connect"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGDIR="$HOME/Library/Logs/whoosh-connect"
LOG="$LOGDIR/ride.log"

plist() {
	cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$LABEL</string>
	<key>ProgramArguments</key>
	<array>
		<string>$VENV_BIN</string>
		<string>run</string>
	</array>
	<key>WorkingDirectory</key>
	<string>$REPO_ROOT</string>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<dict>
		<key>SuccessfulExit</key>
		<false/>
	</dict>
	<key>ThrottleInterval</key>
	<integer>10</integer>
	<key>StandardOutPath</key>
	<string>$LOG</string>
	<key>StandardErrorPath</key>
	<string>$LOG</string>
	<key>ProcessType</key>
	<string>Interactive</string>
</dict>
</plist>
PLIST
}

if [[ "${1:-}" == "--print" ]]; then
	plist
	exit 0
fi

if [[ ! -x "$VENV_BIN" ]]; then
	echo "missing $VENV_BIN — run 'make install' first" >&2
	exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$LOGDIR"
plist > "$PLIST"

UID_="$(id -u)"
launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID_" "$PLIST"
launchctl kickstart -k "gui/$UID_/$LABEL"

echo "installed $LABEL"
echo "log:    $LOG"
echo "watch:  tail -f '$LOG'"
echo "remove: make uninstall-launchd"
