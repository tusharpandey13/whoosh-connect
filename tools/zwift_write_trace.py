"""lldb script: log every CoreBluetooth write the Zwift game makes.

Load it into the *already attached* ZwiftAppSilicon process:

    lldb -p "$(pgrep -f ZwiftAppSilicon | head -1)" \
         -o "command script import /path/to/tools/zwift_write_trace.py" -o continue

Observation only: one breakpoint that reads `$x2`/`$x3`/`$x4`, then lets the
process continue. No patching, no injection, nothing sent to the puck.

Each hit is appended to LOG (and echoed) as:
    <iso time> type=<0 with response | 1 without> char=<uuid> data=<hex>
"""
from __future__ import annotations

import datetime
import os

import lldb

LOG = os.environ.get("ZWIFT_WRITE_LOG", "/tmp/whoosh-zwift-writes.log")
SELECTOR = "-[CBPeripheral writeValue:forCharacteristic:type:]"


def _objc(frame, expr: str) -> str:
    value = frame.EvaluateExpression(expr)
    return value.GetObjectDescription() if value.GetError().Success() else "<error>"


def on_write(frame, bp_loc, internal_dict):
    process = frame.GetThread().GetProcess()
    data = frame.EvaluateExpression("(NSData*)$x2")
    ptr = frame.EvaluateExpression("(const void*)[(NSData*)$x2 bytes]").GetValueAsUnsigned()
    size = frame.EvaluateExpression("(NSUInteger)[(NSData*)$x2 length]").GetValueAsUnsigned()
    err = lldb.SBError()
    raw = process.ReadMemory(ptr, size, err) if ptr and size else b""
    typ = frame.FindRegister("x4").GetValueAsUnsigned() if frame.FindRegister("x4") else -1
    line = (f"{datetime.datetime.now().isoformat(timespec='milliseconds')} "
            f"type={typ} char={_objc(frame, '(NSString*)[[(CBCharacteristic*)$x3 UUID] UUIDString]')} "
            f"data={raw.hex() if err.Success() else '<' + err.GetCString() + '>'}")
    with open(LOG, "a") as f:
        f.write(line + "\n")
    print(line, flush=True)
    del data
    return False  # do not stop: log and keep the game running


def __lldb_init_module(debugger, internal_dict):
    target = debugger.GetSelectedTarget()
    bp = target.BreakpointCreateByName(SELECTOR)
    if not bp.IsValid() or bp.GetNumLocations() == 0:
        print(f"warning: no location for {SELECTOR}; writes will not be logged")
    bp.SetScriptCallbackFunction("zwift_write_trace.on_write")
    print(f"logging CoreBluetooth writes to {LOG}; leave lldb running, then pair the Clicks")
