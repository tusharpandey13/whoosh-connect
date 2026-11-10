# whoosh-connect

**Use Zwift Click v2 shifters in MyWhoosh — without Zwift.**

Right `+` shifts up, left `−` shifts down, the other buttons steer and open
menus. No phone, no Zwift app running, no daily unlock dance. Your trainer stays
paired in MyWhoosh exactly as it is now.

```
 Zwift Click v2  ──── Bluetooth ────►  whoosh-connect  ──── OpenBikeControl ────►  MyWhoosh
   a shifter puck                      this program                              the game
```

Verified on a real bike: macOS, MyWhoosh 5.7.0, Click v2 firmware 1.2.0,
Van Rysel D-series trainer. A full ride, gear changes included.

---

## Quick start

You need: macOS, Python 3.11+, MyWhoosh, and one or both Click v2 pucks.

```sh
git clone https://github.com/tusharpandey13/whoosh-connect.git
cd whoosh-connect
make install                # creates .venv and installs everything
```

Then, for every ride:

1. **Quit Zwift** if it is open (it grabs the pucks).
2. **Start the bridge** and leave it running:

   ```sh
   .venv/bin/whoosh-connect run
   ```

3. **Press a button on a Click** to wake it. You'll see
   `Clicks connected (left puck 2D7E, battery 80 %)`.
4. **In MyWhoosh**: open the connection screen → tap the **OpenBikeControl**
   icon → pick **whoosh-connect**. The bridge prints
   `MyWhoosh connected (app mywhoosh …)`.
5. **Ride.** Ctrl-C stops the bridge cleanly.

That's it. If the buttons ever stop responding, press one on the puck again.

> First run only: macOS asks whether your terminal may use Bluetooth — say yes
> (System Settings → Privacy & Security → Bluetooth).

## Controls

No configuration needed; these are the defaults.

| Click | Button | What it does |
|---|---|---|
| **right** | `+` | **gear up** |
| **left** | `−` | **gear down** |
| left | ◀ ▶ | steer left / right (hold) |
| left | ▲ ▼ | navigate up / down |
| right | A | select |
| right | B | back |
| right | Y | camera view |
| right | Z | menu |

One tap is one gear. Gears only move **during a ride**, not on the connection
screen — and MyWhoosh still needs a virtual-shifting-capable trainer paired for
the gear number to change.

**Don't have both pucks?** Fine. The left puck relays the right puck's buttons
too, so one puck is enough.

## If something's off

| What you see | What it means | Do this |
|---|---|---|
| `Press a Click button to wake it` repeats | a puck sleeps until touched | press a button on the puck |
| Buttons appear in the terminal, the game ignores them | MyWhoosh isn't attached yet | connection screen → **OpenBikeControl** → **whoosh-connect** |
| `no app connected; dropping …` | same thing, seen from the bridge | as above |
| Everything stops after about a minute | the puck's own idle timeout | press a button — each press gives ~50 s |
| `Click is awake but quiet (ff 03 seen)` | informational, not an error | keep riding; press if you want more |
| `another whoosh-connect run is still alive` | one bridge per machine | `pkill -f whoosh_connect`, then start once |
| Exit code 3 | Bluetooth is off, or the terminal lacks permission | see above |

Still stuck? `-v` prints the raw traffic: `.venv/bin/whoosh-connect -v run`.

## Commands

```sh
whoosh-connect run                     # the bridge (what you want)
whoosh-connect run --capture ride.jsonl  # also record the ride for debugging
whoosh-connect run --sink keyboard     # fallback: send K/I/A/D keystrokes instead
whoosh-connect replay ride.jsonl       # replay a recording, no Bluetooth needed
whoosh-connect unlock                  # optional: one Zwift pairing, longer sessions
whoosh-connect dlock --live --push     # research: answer the puck's unlock request
```

`-v` = verbose. Exit codes: `2` bad usage/config, `3` Bluetooth unavailable,
`4` no free port, `5` the unlock service signed nothing, `6` another bridge is
already running.

## Configuration

Optional. Create `~/.config/whoosh-connect/config.toml`:

```toml
[map]
SHIFT_R = "SHIFT_UP"      # right +
SHIFT_L = "SHIFT_DOWN"    # left -
Z       = "NONE"          # disable a button
```

Available actions: `SHIFT_UP SHIFT_DOWN GEAR_SET UP DOWN LEFT RIGHT SELECT BACK
MENU HOME STEER_LEFT STEER_RIGHT CAMERA HUD`, or `NONE`.

## How it works

1. **Talk to the pucks.** The Click v2 speaks a simple Bluetooth protocol: one
   notification per button change, sent as a bitmask. No pairing, no encryption.
2. **Turn buttons into actions.** Each change is diffed against the previous
   state (so a hold is one press, not a hundred) and mapped to an
   [OpenBikeControl](https://github.com/OpenBikeControl/openbikecontrol-protocol)
   action — the protocol MyWhoosh already understands.
3. **Serve it to the game.** The bridge advertises itself over mDNS, accepts
   MyWhoosh's connection, answers its heartbeat, and streams the actions.

## About the Click's "daily unlock"

Zwift's pucks ask their cloud service for a signed "you may report buttons"
token once a day. That request does **not** stop buttons from working here: a
press opens a ~50 s window in which everything is reported, and pressing again
opens the next one. `whoosh-connect unlock` (a single Zwift pairing) only makes
those windows longer.

The full investigation — captures, the challenge format, what did and didn't
work — is in [notes/unlock-analysis.md](notes/unlock-analysis.md) and
[docs/hardware.md](docs/hardware.md).

## Documentation

| Where | What |
|---|---|
| [docs/hardware.md](docs/hardware.md) | measured protocol: GATT, frames, button bits, challenge format |
| [docs/journey.md](docs/journey.md) | the end-to-end user and system journey, failure paths |
| [docs/dev-spec.md](docs/dev-spec.md) | module-by-module design |
| [docs/test-spec.md](docs/test-spec.md) | what the tests cover |
| [notes/unlock-analysis.md](notes/unlock-analysis.md) | the unlock investigation, with corrections |
| [notes/bikecontrol-protocols.md](notes/bikecontrol-protocols.md) | prior art analysis (BikeControl and friends) |
| [raw/](raw/) | the captures behind every claim, including the working ride |

## Development

```sh
make test     # fully offline: no Bluetooth, no network, no Zwift, no MyWhoosh
make lint
```

Tests never inject keystrokes on purpose — a stray `K` would really shift the
rider's gear mid-ride.

## Credits

Protocol knowledge comes from
[BikeControl](https://github.com/jonasbark/swiftcontrol),
[clickbridge](https://github.com/jasasonc/clickbridge),
[zwoosh](https://github.com/amith2368/zwoosh) and the
[OpenBikeControl spec](https://github.com/OpenBikeControl/openbikecontrol-protocol).
Not affiliated with Zwift or MyWhoosh. MIT licensed.
