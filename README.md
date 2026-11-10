# whoosh-connect

Use a **Zwift Click v2** shifter pair with **MyWhoosh** on macOS.
No Zwift app, no daily unlock, and the trainer stays paired in MyWhoosh.

```
Zwift Click v2  ──────BLE──────►  whoosh-connect  ──────TCP──────►  MyWhoosh
 23 08 <bitmask>                  decode + map      OpenBikeControl   01 01 01
                                                                    (gear up)
```

Right `+` shifts up, left `−` shifts down, the other buttons steer and open
menus. Verified end to end on 11-Nov-2026: MyWhoosh 5.7.0, Click v2 firmware
1.2.0, Van Rysel D-series trainer, macOS.

---

## Requirements

| | |
|---|---|
| OS | macOS (bleak's CoreBluetooth backend; nothing here is Mac-specific apart from keyboard mode) |
| Python | 3.11 or newer |
| Game | MyWhoosh with the **OpenBikeControl** icon on its connection screen |
| Shifter | Zwift Click v2, one or both pucks. **The left puck relays the right puck's buttons**, so one connection is enough |
| Trainer | paired in MyWhoosh as usual — whoosh-connect never touches it |

## Install

```sh
git clone https://github.com/tusharpandey13/whoosh-connect.git
cd whoosh-connect
make install                 # .venv + the package + bleak + zeroconf
```

The first `run` makes macOS ask for Bluetooth access for your terminal
(System Settings → Privacy & Security → Bluetooth). Denying it exits with code 3.

## Ride

1. **Quit Zwift** if it is running — it grabs the Clicks and keeps them.
2. Start the bridge:

   ```sh
   .venv/bin/whoosh-connect run
   ```

   It advertises `whoosh-connect` over mDNS and prints
   `Waiting for MyWhoosh: open the connection screen, tap the OpenBikeControl icon, pick whoosh-connect`.
3. **Press a Click button** to wake the puck:
   `Clicks connected (left puck 2D7E, battery 80 %)`.
4. In MyWhoosh: connection screen → **OpenBikeControl** → **whoosh-connect**.
   The bridge prints `MyWhoosh connected (app mywhoosh v5.7.0, supports: …)`.
5. Ride. Shift with `+` / `−`. Ctrl-C stops cleanly (mDNS unregistered, socket
   closed, BLE disconnected).

If the buttons go quiet, **press a Click button once** — see the next section.

## Controls

Defaults, no configuration needed:

| Click | Physical | Action sent to MyWhoosh | OBC id |
|---|---|---|---|
| right | `+` (shift) | **gear up** | `01` |
| left | `−` (shift) | **gear down** | `02` |
| left | ◀ ▶ | steer left / right (hold) | `18` / `19` |
| left | ▲ ▼ | up / down | `10` / `11` |
| right | A | select | `14` |
| right | B | back | `15` |
| right | Y | camera | `40` |
| right | Z | menu | `16` |

One tap is one gear. Shifting only registers **in a ride**, not on the
connection screen.

## What "the lock" means here

The Click v2 asks for a Zwift-cloud-signed unlock (`ff 03` + a challenge) and
mutes itself if the answer never arrives. On this firmware that does **not**
stop button reporting:

- **A press opens a ~50 s window** in which every button is streamed, answered
  challenge or not. A full ride works like that; just press again when it ends.
- An unlocked puck instead streams continuously, and idles with `23 08 ff ff ff ff 0f`.
- `whoosh-connect unlock` (one Zwift pairing) only makes windows last longer. It
  is optional, and answering the challenge offline is unsolved — see
  `notes/unlock-analysis.md`.

## Commands

| Command | What it does |
|---|---|
| `whoosh-connect run` | the bridge: OpenBikeControl server + mDNS + BLE link (default `--sink obc`) |
| `whoosh-connect run --capture ride.jsonl` | same, and record every BLE event for later replay |
| `whoosh-connect run --sink keyboard` | no OBC: sends `K`/`I`/`A`/`D` via System Events (needs Accessibility, MyWhoosh frontmost) |
| `whoosh-connect replay ride.jsonl` | feed a capture through the whole pipeline, no Bluetooth |
| `whoosh-connect unlock` | optional: guide one Zwift pairing and remember when it happened |
| `whoosh-connect dlock --live --push` | research: sign a live `ff 03` challenge with your own Zwift token and try to answer it |

`-v` adds raw hex and debug detail. Exit codes: `2` usage/config, `3` Bluetooth
unavailable, `4` no free port in 36867–36877, `5` the D-Lock service signed
nothing, `6` another `whoosh-connect run` is already advertising.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Press a Click button to wake it` keeps repeating | the puck is asleep; it only advertises for a while after a press. Press it |
| Buttons scroll in the terminal, game ignores them | MyWhoosh is not attached to the socket: `no app connected; dropping 010101`. Do the OpenBikeControl step *on the connection screen* |
| Everything stops after ~50 s of riding | the puck's `ff 05 00 ea 05` "stopped" frame. One press opens the next window |
| `Click asked for an unlock (ff 03); ignoring it` | informational, not an error — buttons keep working |
| Gear number never moves, other buttons do | MyWhoosh only shifts inside a ride, and needs a trainer supporting virtual shifting paired to it |
| `another whoosh-connect run is still alive` (exit 6) | one socket per machine: `pkill -f whoosh_connect`, then start once |
| Exit 3 | Bluetooth off or the terminal lacks permission |
| Zwift keeps stealing the link | quit Zwift; the bridge survives it, but the Clicks do not |

## Configuration

Optional `~/.config/whoosh-connect/config.toml`; buttons are named by their
position on the puck, actions by OBC name.

```toml
[map]
SHIFT_R  = "SHIFT_UP"     # right +
SHIFT_L  = "SHIFT_DOWN"   # left -
LEFT     = "STEER_LEFT"
RIGHT    = "STEER_RIGHT"
UP       = "UP"
DOWN     = "DOWN"
A        = "SELECT"
B        = "BACK"
Y        = "CAMERA"
Z        = "NONE"         # disable a button
```

Any of `SHIFT_UP SHIFT_DOWN GEAR_SET UP DOWN LEFT RIGHT SELECT BACK MENU HOME
STEER_LEFT STEER_RIGHT CAMERA HUD` or `NONE`.

## How it works

1. **BLE.** `RideOn` is written to char `0003`; the puck echoes it on `0004` and
   streams `23 08 <varint>` button frames on `0002`, where the varint is an
   active-low bitmask (all released = `ff ff ff ff 0f`). No pairing, no crypto.
2. **Decode and map.** Frames are diffed against the previous button set, so a
   hold produces one press, and each change maps to an OBC action
   (`A → SELECT → 0x14`).
3. **OpenBikeControl.** The bridge advertises `_openbikecontrol._tcp` on TCP
   36867, accepts the app's info handshake, and sends `01 <id> <01|00>` per
   change. MyWhoosh pings with a bare `00` every ~4 s and expects a device
   status (`02 <battery> <connected>`) back — that reply is what makes the game
   treat the controller as live. Miss it and everything else still "works" and
   is ignored, which cost us an evening.

Measured details, captures and the protocol table: [docs/hardware.md](docs/hardware.md).
The unlock saga: [notes/unlock-analysis.md](notes/unlock-analysis.md).
Design: [docs/journey.md](docs/journey.md), [docs/dev-spec.md](docs/dev-spec.md).

## Development

```sh
make test        # 80 tests, fully offline: no Bluetooth, network, Zwift or MyWhoosh
make lint        # compileall
```

- `whoosh-connect run --capture ride.jsonl` turns a real ride into a fixture;
  `whoosh-connect replay ride.jsonl` runs it back through the same pipeline.
- `src/whoosh_connect/`: `ble.py` (link, handshake, reconnect, opt-in reset),
  `pipeline.py` (decode → map → sinks), `obc*.py` (protocol + TCP server),
  `zwift.py` (frame parsing), `dlock.py` (the cloud-unlock research),
  `capture.py`, `mapping.py`, `keyboard.py`, `state.py`, `spy.py`.
- `scripts/`: standalone probes (`scan.py`, `probe.py`, `probe2.py`,
  `decode.py`, `spy.py`). `raw/`: captures and the Zwift/MyWhoosh evidence.
- Tests never inject keystrokes: a stray `K` would really shift the owner's bike.

## Known limits

- Windows last ~50 s per press until a real unlock; the challenge answer is only
  obtainable from Zwift's cloud for some challenge shapes (52/58-byte `f3`).
- One OBC client per socket, one bridge per machine.
- No haptics, no trainer proxying, no virtual shifting in the bridge — MyWhoosh
  does that itself.
- macOS only in practice; the BLE and OBC layers are portable, keyboard mode is not.

## Credits

Protocol facts from [BikeControl](https://github.com/jonasbark/swiftcontrol),
[clickbridge](https://github.com/jasasonc/clickbridge),
[zwoosh](https://github.com/amith2368/zwoosh) and the
[OpenBikeControl](https://github.com/OpenBikeControl/openbikecontrol-protocol)
spec. Not affiliated with Zwift or MyWhoosh. MIT licensed.
