#!/bin/bash
# Watch what Zwift writes to the Clicks (host -> puck), on this Mac.
#
# Attaches lldb to your own ZwiftAppSilicon process and logs every
# CoreBluetooth write with the characteristic UUID and bytes. Observation only.
#
# Usage:  tools/trace_zwift_writes.sh     (start Zwift and pair the Clicks first)
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
pid=$(pgrep -f ZwiftAppSilicon | head -1 || true)
if [ -z "${pid:-}" ]; then
    echo "ZwiftAppSilicon is not running: launch Zwift and pair the Clicks first" >&2
    exit 1
fi

log=${ZWIFT_WRITE_LOG:-/tmp/whoosh-zwift-writes.log}
rm -f "$log"
echo "attaching lldb to ZwiftAppSilicon (pid $pid); writes -> $log"
echo "press a Click button while it runs; quit lldb with Ctrl-C then 'quit'"

exec lldb -p "$pid" \
    -o "settings set target.process.stop-on-sharedlibrary-events false" \
    -o "command script import $here/zwift_write_trace.py" \
    -o "continue"
