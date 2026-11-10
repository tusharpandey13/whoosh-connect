# whoosh-connect: test spec

Covers docs/journey.md and docs/dev-spec.md. Automated tests run **offline**. They never touch real Bluetooth, the real network (mDNS), real keystrokes, Zwift or MyWhoosh. The owner may be riding MyWhoosh while tests run, so an injected K/I keystroke would really shift their gear.

## Safety guard (tests/conftest.py, autouse)
- Monkeypatch `bleak.BleakScanner` and `bleak.BleakClient` to raise `AssertionError("real BLE in tests")`.
- Monkeypatch `zeroconf.Zeroconf` to raise the same kind of error.
- Monkeypatch `subprocess.run` and `subprocess.Popen` to raise if argv[0] is `osascript`, `open` or `pgrep`.
- Tests that need those use the injectable fakes instead.
- The safety tests themselves (S1, S2) check that the guard fires.

## Fixtures
- `tests/fixtures/probe5_unlocked.jsonl`: copy of `raw/ble/probe5_after_zwift_unlock.jsonl`. It's the owner's labelled run: 4–5 rounds of "left puck up, right, down, left, shift ×4, then right puck the same, ending with shift ×4". It records 115 button-downs across the two pucks.
- `tests/fixtures/probe3_locked.jsonl` (locked, zero button frames).
- `tests/fixtures/probe2_burst.jsonl` (locked, short burst).
- Expected numbers for the left puck (dev `1501`) in probe5, from `scripts/decode.py`: SHIFT_R presses 18, SHIFT_L 17, LEFT 4, UP 5, RIGHT 5, DOWN 8, A 4, B 6, Y 6, Z 4. The test must recompute these through the new code, and the totals must match. The shift counts fit the rider's account (4–5 rounds × 4 presses = 16–20).

## Unit tests
### zwift (Z)
- Z1 `decode_varint` on `ff ff ff ff 0f` → 0xFFFFFFFF; on `fb ff ff ff 0f` → 0xFFFFFFFB; a truncated buffer raises.
- Z2 `parse_frame(23 08 ff ff ff ff 0f)` → `ButtonsFrame(∅)`.
- Z3 Real frames (LEB128 then active-low): `2308fbffffff0f` → {RIGHT}; `2308feffffff0f` → {LEFT}; `2308bfffffff0f` → {Y}; `2308fffeffff0f` → {Z}; `2308fffdffff0f` → {SHIFT_L}; `2308ffdfffff0f` → {SHIFT_R}; `2308fefeffff0f` → {LEFT, Z}.
- Z4 `19 10 50` → `BatteryFrame(80)`.
- Z5 Real frames: `ff 03 00 0a 21 …` → Challenge; `ff 05 00 fa …` → Vendor; `2a 08 …` → Log; `52 69 64 65 4f 6e` → RideOn.
- Z6 Malformed (`23`, `23 08`, `23 08 ff`, empty, random bytes) → `UnknownFrame`, never an exception (F8).
- Z7 `parse_adv({0x094A: 0b2d7e})` → left, id `2d7e`; `0ae89b` → right; other manufacturers → None.

### obc (O)
- O1 `encode_button_state([(0x01, True)])` = `01 01 01`; multi = `01 01 01 02 00`.
- O2 `encode_device_status(80, True)` = `02 50 01`; None → `02 ff 01`; disconnected → `… 00`.
- O3 `parse_app_info` on the spec example (`zwift`, `1.52.0`, `[01 02 10 14]`) → the fields; count 0 → empty set; truncated → ValueError.
- O4 `split_messages`: two concatenated app-info messages, a message split across two reads, a haptic message, and an unknown type.
- O5 `txt_record` has exactly the MDNS.md keys and `version=1`.

### mapping (M)
- M1 DEFAULT_MAP matches journey.md (SHIFT_R → 0x01, SHIFT_L → 0x02, LEFT → 0x18, RIGHT → 0x19, UP → 0x10, DOWN → 0x11, A → 0x14, B → 0x15, Y → 0x40, Z → 0x16). Every default id is in the MyWhoosh supported set {0x01–0x03, 0x10–0x19, 0x40}.
- M2 TOML override merges, `NONE` unmaps, and an unknown button or action raises ConfigError.
- M3 `ButtonTracker`: ∅ → {A} gives [(A, down)]; repeat gives [] (F10); {A} → {A, SHIFT_R} gives [(SHIFT_R, down)]; → ∅ gives releases sorted by bit; `release_all` releases what's held.

## Component tests
### pipeline (P)
- P1 Replay probe5 (left puck) through Pipeline with a recording sink. The press counts per action equal the fixture numbers above, and every press has a matching release.
- P2 A frame with two changes gives one `send` call with two items (F9).
- P3 `on_link(False)` while A is held gives a release of A, then `status(batt, False)` (F2).
- P4 No ButtonsFrame within 60 s (fake clock) after the link comes up gives the unlock hint logged exactly once; it isn't logged again after a frame arrives (F3).
- P5 Replay probe3 (locked) gives zero sends.

### ble (B), with fake scanner and fake client
- B1 Selection: adverts for right then left within 10 s → the left is chosen. Only the right for 10 s → the right is chosen, with a warning.
- B2 Handshake order: `start_notify(0002)`, `start_notify(0004)`, write(0003, `RideOn`, response=False); the fake replies `RideOn` on 0004; then write(0003, `ff0400`, response=False); then `on_link(True)`.
- B3 No RideOn reply within 5 s (fake clock) → disconnect and retry (F7).
- B4 A disconnect callback → `on_link(False)`, then a reconnect with backoff 1, 2, 4 s (fake sleep records the delays) → handshake again (F2).
- B5 With `zwift_running` true at reconnect, the warning is logged (F4).
- B6 Notifications on 0002 reach `on_frame` as parsed frames.
- B7 A bleak "unauthorized" or "powered off" error → `BluetoothUnavailable` (F11).
- B8 The capture writer records tx and rx lines in the probe2 format, round-trip readable by `read_capture`.

### obc_server (S), on real asyncio TCP at 127.0.0.1 port 0 with a fake advertiser
- S1 The conftest guard: instantiating the real `Zeroconf` in a test raises.
- S2 The conftest guard: `subprocess.run(["osascript", …])` raises.
- S3 Start → the fake advertiser receives a registration with the bound port and the TXT record. Stop → it receives an unregistration.
- S4 A fake app connects and receives device status first. It sends App Info for "mywhoosh" with buttons [01 02 18 19]; the server records it. A broadcast of `01 01 01` is received byte-exact.
- S5 Two clients both receive the broadcast. When one disconnects, the other still receives it (F12).
- S6 A broadcast with no clients doesn't raise, and the log line is rate-limited (F5).
- S7 Port busy: occupy the start port, and the server binds the next one and advertises it (F6). All ports busy → `PortsBusy`.
- S8 End-to-end offline: replay probe5 → ObcSink → server → fake app socket. The app receives a `01 01 01` count equal to SHIFT_R presses (18) and a `01 02 01` count equal to SHIFT_L presses (17).

### keyboard (K), with a fake runner and fake frontmost
- K1 SHIFT_UP press → one runner call with `osascript -e … keystroke "k"`; the release → no call.
- K2 STEER_LEFT press → `key down "a"`; release → `key up "a"`.
- K3 Frontmost isn't MyWhoosh → no runner call, and a log line.
- K4 The runner is called with an argv list, never `shell=True`.

### state / cli (C)
- C1 StateStore round-trip in tmp_path; `unlock_age` with a fake clock.
- C2 `unlock_flow` with a fake ps sequence (absent, present ×15 polls = 30 s, absent) → records the unlock. A run shorter than 20 s → not recorded, with a warning.
- C3 `run` pre-flight: no unlock recorded or older than 24 h → warning text. Zwift running → wait message (fake ps).
- C4 CLI arg parsing and exit codes: a bad config gives 2; `BluetoothUnavailable` gives 3; `PortsBusy` gives 4 (patch the components).
- C5 `replay tests/fixtures/probe5_unlocked.jsonl --sink print` exits 0 and prints at least 35 lines containing SHIFT_UP or SHIFT_DOWN.

## Pass criteria
- `make test` is green. It runs with no network, Bluetooth or GUI access, and takes under 30 s.
- `python -m compileall src` is clean.
- Coverage is not gated, but every F1–F12 path has at least one test (F1 is covered by B1's wake-hint log via the fake clock).

## Manual acceptance (owner, after the ride; not automated)
| # | Step | Expect |
|---|---|---|
| A1 | `whoosh-connect unlock`, do the Zwift steps, Cmd-Q | "Unlocked at …" |
| A2 | `whoosh-connect run`, press a Click button | "Clicks connected (left puck 2D7E …)" |
| A3 | MyWhoosh → connection screen → OBC icon → whoosh-connect | "MyWhoosh connected" in the terminal |
| A4 | Ride: right shift ×3, left shift ×3 | MyWhoosh gear +3, then −3; terminal logs 6 actions |
| A5 | Hold the left puck's left button | Avatar steers left while held (where the route allows) |
| A6 | Switch the Click off or walk away until it disconnects, then press a button | Auto-reconnect, shifting works again |
| A7 | Quit MyWhoosh and reopen | Reconnects via the OBC icon without restarting the bridge |
| A8 | `--sink keyboard` with MyWhoosh frontmost | Shifts work via K/I |
| A9 | Ctrl-C | Clean exit within 3 s |
