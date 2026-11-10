# whoosh-connect

## Usage

Bridges a Zwift Click v2 pair to MyWhoosh (macOS): right shift = gear up, left shift = gear down, other buttons = steering and menu. The bridge speaks OpenBikeControl to MyWhoosh; the trainer stays paired in MyWhoosh.

```
make install                       # creates .venv, installs the package + bleak + zeroconf
.venv/bin/whoosh-connect unlock    # daily: follow the Zwift checklist (pair trainer, then Clicks, ~30 s, Cmd-Q)
.venv/bin/whoosh-connect unlock --capture raw/ble/unlock.jsonl   # same, plus a passive BLE recording (catches the ff 03 challenge)
.venv/bin/whoosh-connect dlock raw/ble/unlock.jsonl [--push]     # try signing that challenge via Zwift's D-Lock service
.venv/bin/whoosh-connect run       # ride: OpenBikeControl server + mDNS + BLE link to the left puck
```

1. Allow Bluetooth for your terminal on the first `run` (System Settings -> Privacy & Security -> Bluetooth).
2. Quit Zwift before riding (`run` waits until it exits). Press a Click button to wake it.
3. In MyWhoosh: connection screen, tap the OpenBikeControl icon, pick `whoosh-connect`.
4. Optional: `--sink keyboard` sends K/I/A/D through System Events (needs Accessibility permission, MyWhoosh frontmost). `--capture FILE.jsonl` records the BLE traffic; `whoosh-connect replay FILE --sink print` replays it with no Bluetooth. `-v` shows raw hex.
5. Remap buttons in `~/.config/whoosh-connect/config.toml`: `[map]` with `SHIFT_R = "SHIFT_UP"`, `Z = "NONE"`, etc.

Troubleshooting: no button events means the Clicks are locked (run `unlock`; the unlock window measured ~70 min on 10-Nov-2026, not 24 h). `run` now logs `Click sent an unlock challenge (ff 03): the pucks are locked` the moment the puck asks, instead of waiting for the 60 s timer. Exit codes: 2 usage/config, 3 Bluetooth unavailable, 4 no free port in 36867-36877, 5 the D-Lock service signed nothing. Design: `docs/`. Tests: `make test` (fully offline).

---

## Research notes: Zwift Click v2 + Cog → MyWhoosh

Goal: use the owner's Zwift Click v2 controllers (and Zwift Cog) for virtual shifting in MyWhoosh on macOS.
Status as of 10-Nov-2026: BLE interface understood; buttons are **gated by Zwift's unlock**. The practical route is BikeControl, or a self-made bridge after a Zwift unlock.

## Hardware

| Thing | Facts |
|---|---|
| Trainer | `VANRYSEL-HT-b50b` (Van Rysel D-series). CoreBluetooth id `BB48099E-F6E0-EDBB-DC8E-E471222C122F`. Firmware 0.108, software "Jan 7 2025". Exposes FTMS **and** Zwift's trainer service `00000001-19CA-…` ("configured for Zwift Protocol" in Zwift logs). |
| Zwift Cog | Passive cog, no electronics, no BLE. Gives a fixed chainline; the app makes "gears" by changing resistance. |
| Click v2 **left** | Name `Zwift Click`, adv mfr data (0x094A) `0b 2d7e`, CoreBluetooth id `15018857-AF57-60C6-E4C3-49E31712DED0`, serial `0B-D852FAA92D7E`, HW `B.0`, FW `1.2.0`, battery 80 %. Zwift name "Zwift Click 2D7E". |
| Click v2 **right** | Adv `0a e89b`, CoreBluetooth id `F2640CDB-976F-2DB0-8780-2AA8386B5FFE`, serial `0A-6C1AEAD9E89B`, HW `B.0`, FW `1.2.0`, battery 80 %. Zwift name "Zwift Click E89B". |

The left/right meaning of the 0x0B/0x0A mfr byte comes from BikeControl, where it's marked "unconfirmed". The button data agrees with it: the 2D7E unit reported nav plus shift-up-left.

## Click v2 BLE interface (measured)

GATT, the same on both pucks:
```
180A Device Information   2A29 "Zwift Inc"  2A25 serial  2A27 "B.0"  2A26 "1.2.0"
FC82 Zwift controller
  00000002-19ca-4651-86e5-fa29dcdd09d1  notify        device→host async messages
  00000003-19ca-4651-86e5-fa29dcdd09d1  write-no-rsp  host→device commands
  00000004-19ca-4651-86e5-fa29dcdd09d1  read,indicate device→host sync replies
  00000100/101/102-19ca-…               write/notify  unused by BikeControl (probably firmware update/unlock)
180F Battery               2A19 = 0x50 (80 %)
```

Session:
1. Write `RideOn` (`52 69 64 65 4f 6e`) to 0003 without response. A write *with* response fails with "Write Not Permitted".
2. 0004 indicates `RideOn` back in plain text. There's no ECDH or encryption.
3. 0002 then sends:
   - `2a …` log record
   - `ff 05 00 fa/ea 05 …` vendor status (contains the serial in ASCII)
   - `ff 03 00 0a 21 …` ~90-byte **unlock challenge**, which Zwift answers
   - `19 10 50` battery heartbeat every 5 s
4. Buttons: `23 08 <varint>`, where the varint (LEB128) is an **active-low** bitmask; all-released is `ff ff ff ff 0f`.

Physical layout confirmed by a labelled run (probe5: 4–5 rounds of up/right/down/left plus shift×4, left puck then right puck):

| puck | position | bit | mask | Zwift name | use |
|---|---|---|---|---|---|
| left | up | 1 | 0x2 | UP | |
| left | right | 2 | 0x4 | RIGHT | |
| left | down | 3 | 0x8 | DOWN | |
| left | left | 0 | 0x1 | LEFT | |
| left | shift | 8 | 0x100 | SHIFT_UP_L ("−") | **shift down** |
| right | up | 6 | 0x40 | Y | |
| right | right | 4 | 0x10 | A | |
| right | down | 5 | 0x20 | B | |
| right | left | 7 | 0x80 | Z | |
| right | shift | 12 | 0x1000 | SHIFT_UP_R ("+") | **shift up** |

The left puck (2D7E) reports both pucks' buttons; the right puck (E89B) reports only its own.

### The blocker: Zwift unlock
- `raw/ble/probe2.jsonl`: button events arrived **only for about 6 s right after connecting** (154 messages), then only the battery heartbeat.
- `probe3_labelled.jsonl` (110 s, slow labelled presses): **0 button events**.
- `probe4_ff0400.jsonl` (`RideOn` then `ff 04 00`, which is BikeControl's unlocked-path command): **0 button events**.
- BikeControl's TROUBLESHOOTING says firmware 1.2.0 Click v2 only sends buttons if it connected to the **real Zwift app within ~24 h**. Zwift answers the `ff 03` challenge; BikeControl's answering code is closed source (a stub in the public repo).
- The Zwift logs show the Clicks last connected to Zwift on 05-Nov-2026, so they're locked now.

**Confirmed 10-Nov-2026** (`probe5_after_zwift_unlock.jsonl`): after pairing the trainer and then the Clicks in Zwift (~30 s), quitting Zwift, and connecting with `RideOn` + `ff 04 00`, buttons flowed for the whole 75 s capture (115 presses; every button in the table seen, repeatedly). A local bridge therefore works once Zwift has unlocked the Clicks. The unlock lasts ~70 min in practice (measured 10-Nov-2026), not 24 h; BikeControl's "within ~24 h" is only an upper bound.

Shortcut for the bridge: the left puck (2D7E) reports **all** buttons, right ones included, so one connection is enough.

## MyWhoosh side (v6.2.3, bundle `com.whoosh.whooshgame`, Unreal Engine 5)
- There are no text logs. Saved hardware map: `raw/mywhoosh/HardwareSaveData.sav` (plain-text extract in `HardwareSaveData.strings.txt`), which has the trainer under `E_PowerSource`/`E_Controllable`/`E_Cadence` plus a HR strap. There's no controller entry.
- Binary strings (`raw/mywhoosh/binary_strings_controls.txt`): `EVirtualGears::E_Gear1..30`, `GearUp`/`GearDown`, `IsVirtualGearShiftingFeatureEnabled`, `ConnectAppGearChange`, `ConnectToOpenBike`, `EInGameControlsParameter::{E_GearShift,E_Steering,E_UTurn,E_Tuck,E_Emote,E_CameraAngle}`. There's no Zwift FC82 controller UUID, so **MyWhoosh can't read a Click itself**.
- There are three ways to inject gear changes (details in `notes/bikecontrol-protocols.md`):
  1. **Keyboard**: `K` shift up, `I` shift down, `A`/`D` steer, `H` toggle the UI, `1`–`7` emotes.
  2. **MyWhoosh Link** (legacy): MyWhoosh connects over TCP to the bridge on port 21587 and receives JSON lines such as `{"MessageType":"Controls","InGameControls":{"GearShifting":"1"}}`.
  3. **OpenBikeControl** (current, official): the bridge advertises mDNS `_openbikecontrol._tcp` on TCP 36867 and sends binary `01 <btn> <state>` (01 ShiftUp, 02 ShiftDown, 18/19 steer). In MyWhoosh: connection screen, OBC icon top right.
- Virtual shifting: MyWhoosh has 30 virtual gears and runs them on an FTMS trainer itself (sim resistance). The Van Rysel pairs in MyWhoosh as a normal FTMS trainer, and the Cog is fine with that.

## Options
| Option | Effort | Caveat |
|---|---|---|
| **BikeControl** (Mac App Store, MyWhoosh partner) | none | Handles the unlock (automatic, or "connect in Zwift once a day"); may be paid |
| Own bridge: bleak → OpenBikeControl/keyboard | ~100 lines | Needs a daily Zwift unlock, since the challenge response isn't public |
| Own bridge, unlock handled too | sign each `ff 03` with the rider's own token via the D-Lock service; write-format still being pinned down | Fragile across firmware; needs a Zwift login once for a token |

## Layout
- `scripts/scan.py`: advert scan. `scripts/probe.py`: GATT dump plus handshake. `scripts/probe2.py DUR OUT [hexwrites]`: concurrent capture from both pucks to JSONL. `scripts/decode.py FILE`: button bitmask → press events.
- `raw/ble/`: all captures. `raw/zwift/`: Zwift `prefs.xml` / `knowndevices.xml` plus the BLE/connectivity log lines. `raw/mywhoosh/`: saved hardware and settings, binary strings.
- `notes/bikecontrol-protocols.md`: BikeControl source analysis (commit edd1e2a), with file:line citations.

Run with: `python3 -m venv .venv && .venv/bin/pip install bleak && .venv/bin/python scripts/probe2.py 60 raw/ble/out.jsonl ff0400`
The terminal app needs Bluetooth permission. Press a Click button first to wake it, because the pucks only advertise for a short time after a press.

## Zwift unlock, in detail (10-Nov-2026)

`notes/unlock-analysis.md` has the measured sequence and the evidence. Short
version: the puck sends `ff 03` + an 82-byte challenge, and only streams buttons
once a Zwift **cloud** service has signed it (`POST .../api/d-lock-service/device/authenticate`,
23-byte answer, written back to char `0003`). No local secret, so nothing
offline can answer it — hence the daily Zwift session.

Tools added while investigating: `scripts/spy.py` (passive listener: connect,
subscribe, never write — shows the device side of Zwift's own handshake),
`tools/fake_click.swift` (impersonates a Click; blocked on macOS because a
locally generated advertisement is not delivered to a local scan) and
`tools/trace_zwift_writes.sh` (attaches lldb to your own Zwift game and logs every
CoreBluetooth write: the missing host→puck framing, with characteristic UUID and
write type. Observation only, no patching).
