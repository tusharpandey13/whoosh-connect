# whoosh-connect: end-to-end user + system journey

Version 1, 10-Nov-2026. Inputs: measured BLE behaviour (README.md), BikeControl source notes (notes/bikecontrol-protocols.md), OpenBikeControl spec (github.com/OpenBikeControl/openbikecontrol-protocol @ b0dbbe8: PROTOCOL.md, MDNS.md).

## Goal
Ride in MyWhoosh on the Mac with the Zwift Click v2 pair as shifters: right shift = gear up, left shift = gear down, other buttons = steering and menu. MyWhoosh does the virtual shifting on the Van Rysel trainer, which it already pairs over FTMS. The bridge only delivers button actions.

## Actors
- **Rider**: the owner, on the bike, Mac in front, hands on the bars.
- **Clicks**: Zwift Click v2 left puck (adv `0x094A: 0b…`, relays all buttons) and right puck (`0a…`, own buttons only). Firmware 1.2.0, Zwift-locked unless Zwift connected them in the last ~24 h.
- **Zwift app**: used only to unlock the Clicks; must be quit before riding.
- **whoosh-connect**: this tool. A Python CLI on the Mac.
- **MyWhoosh 6.2.3**: the OpenBikeControl (OBC) client. It browses mDNS `_openbikecontrol._tcp`, connects over TCP, and receives button messages.
- **Trainer**: paired directly in MyWhoosh; whoosh-connect never touches it.

## Journey A: one-time setup
1. Rider clones the repo and runs `make install` (creates `.venv`, installs the package plus bleak and zeroconf).
2. The first `whoosh-connect run` triggers the macOS Bluetooth permission prompt for the terminal. The rider allows it.
   - If permission is denied, whoosh-connect exits with "Bluetooth permission denied: System Settings → Privacy & Security → Bluetooth → enable your terminal", exit code 3.
3. Optional, keyboard mode only: macOS asks for Accessibility permission on the first injected key.

## Journey B: pre-ride unlock (daily)
1. Rider runs `whoosh-connect unlock`.
2. The tool prints the checklist: wake the trainer, open Zwift, pair the trainer first and then the Clicks, keep them connected for 30 s and press a few buttons, quit Zwift with Cmd-Q.
3. The tool waits for a Zwift process to appear, then for it to exit (it polls the process list every 2 s and accepts Ctrl-C).
4. When Zwift exits after having run for at least 20 s, the tool records the unlock time in `~/.config/whoosh-connect/state.json` and prints "Unlocked at HH:MM; valid until about HH:MM tomorrow".
   - If Zwift ran for less than 20 s, it prints a warning that the unlock may not have happened and does not record it.
5. Rider can skip this command and do the Zwift steps by hand. `run` then simply has no recorded unlock time.

## Journey C: ride
1. Rider runs `whoosh-connect run`. Default sink is `obc`.
2. Pre-flight:
   - If Zwift is running, the tool warns "Zwift is running and will grab the Clicks; quit it (Cmd-Q)" and waits until Zwift exits.
   - If the recorded unlock is missing or older than 24 h, it warns "Clicks probably locked: run `whoosh-connect unlock`" and continues.
3. OBC server: the tool listens on TCP (port 36867, or the next free port) and advertises mDNS `whoosh-connect._openbikecontrol._tcp.local.` with the TXT record from MDNS.md, then prints "Waiting for MyWhoosh: open the connection screen, tap the OpenBikeControl icon, pick whoosh-connect".
4. BLE: the tool scans for Zwift controllers (manufacturer data 0x094A, type byte 0x0A or 0x0B) and prints "Press a Click button to wake it".
   - It prefers the left puck (0x0B). If only the right puck shows up within 10 s of the first advert, it uses the right puck and warns "right puck only: left-side buttons unavailable".
5. Connect: subscribe to notify on 0002 and indicate on 0004, write `RideOn` to 0003 without response, wait for the `RideOn` reply on 0004 (timeout 5 s, then retry the connection), then write `ff 04 00`.
   - Print "Clicks connected (left puck 2D7E, battery 80 %). Press right shift to test".
6. MyWhoosh connects to the TCP server and sends App Info (0x04). The tool prints "MyWhoosh connected (app mywhoosh v…, supports: …)".
   - The tool sends Device Status `02 <battery> 01`, and sends it again whenever the battery level changes.
7. Riding: each button press or release on the Click becomes a 0x23 frame, which is decoded to a set of pressed buttons and diffed against the previous set.
   - Each change maps to an OBC action and is sent as `01 <id> <01|00>` to every connected app client.
   - The tool logs each action on one line, e.g. `12:03:04 SHIFT_UP ▲`.
8. Buttons whose mapped action is not in the app's App Info list are still sent (harmless per spec) but logged once as "MyWhoosh may ignore X". An App Info button count of 0 means the app supports everything.
9. Rider presses Ctrl-C: the tool unregisters mDNS, closes TCP clients, and disconnects BLE within 3 s. Exit code 0.

## Journey D: keyboard fallback
`whoosh-connect run --sink keyboard`: there's no OBC server. Shift up sends K, shift down sends I, and steer left/right holds A/D (key down on press, key up on release). Keys are sent through macOS System Events only while MyWhoosh is the frontmost app; otherwise the action is dropped and logged as "MyWhoosh not frontmost".

## Failure and edge paths (system behaviour)
| # | Situation | Behaviour |
|---|---|---|
| F1 | Clicks asleep / not advertising | Keep scanning, repeating "press a Click button" every 30 s. |
| F2 | BLE disconnect mid-ride | Log it, tell OBC clients `02 <batt> 00`, rescan and reconnect with backoff (1, 2, 4… max 10 s), redo the handshake, then send `02 <batt> 01`. Buttons held at the disconnect are released (`01 <id> 00`). |
| F3 | Clicks locked (no button frames) | Not detectable from the status frames. If no 0x23 frame arrives within 60 s of connecting, print the unlock hint once. |
| F4 | Zwift starts while running | The Clicks get grabbed and the BLE link drops, so F2 applies. On every reconnect attempt, if Zwift is running, print the F-pre-flight warning. |
| F5 | MyWhoosh disconnects | Keep the server and mDNS up. Drop actions while no client is connected (never queue; stale shifts are worse than lost ones). Log "no app connected" at most once per 30 s. |
| F6 | Port 36867 busy | Try 36868…36877; advertise the port actually bound. If none is free, exit code 4. |
| F7 | Handshake reply missing | Disconnect and retry (counts as F2). |
| F8 | Unknown opcode or malformed frame | Log at debug level with hex; never crash. |
| F9 | Several buttons change in one frame | One OBC message carrying all the changed IDs. |
| F10 | Same frame repeated | No output (diff is empty). |
| F11 | Bluetooth off or unauthorised | Clear message, exit code 3. |
| F12 | Two OBC clients | Broadcast to both. |

## Journey E: diagnostics
- `whoosh-connect run --capture FILE.jsonl` also writes every BLE event in the `probe2.py` JSONL format, so a real ride becomes a test fixture.
- `whoosh-connect replay FILE.jsonl [--sink obc|keyboard|print] [--speed N]` feeds a capture through the decode → map → sink pipeline without Bluetooth. The default sink is `print`.
- `-v` gives debug logs with raw hex.

## Default button map (overridable via `~/.config/whoosh-connect/config.toml`)
| Click | Position | Bit | OBC action | ID | Keyboard |
|---|---|---|---|---|---|
| right | shift | 12 | Shift Up | 0x01 | K |
| left | shift | 8 | Shift Down | 0x02 | I |
| left | left | 0 | Steer Left (hold) | 0x18 | A (hold) |
| left | right | 2 | Steer Right (hold) | 0x19 | D (hold) |
| left | up | 1 | Up | 0x10 | – |
| left | down | 3 | Down | 0x11 | – |
| right | right (A) | 4 | Select | 0x14 | – |
| right | down (B) | 5 | Back | 0x15 | – |
| right | up (Y) | 6 | Camera View | 0x40 | – |
| right | left (Z) | 7 | Menu | 0x16 | – |

MyWhoosh's supported IDs, per BikeControl: 0x01–0x03, 0x10–0x19, 0x40. Every default is in that set.

## Out of scope (v1)
Unlocking without Zwift (the `ff 03` challenge response isn't public), trainer proxying or virtual shifting inside the bridge, the MyWhoosh Link legacy protocol, haptics (ignored if received), Windows/Linux, GUI.
