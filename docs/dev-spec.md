# whoosh-connect: development spec

Implements docs/journey.md (journey and failure IDs F1–F12 are referenced below). Keep it small: the target is under about 900 lines of source. Pure logic is separated from I/O so most behaviour is testable without hardware.

## Stack
- Python ≥ 3.11, `src/` layout, package `whoosh_connect`, console script `whoosh-connect`.
- Runtime deps: `bleak` (BLE) and `zeroconf` (mDNS). Dev deps: `pytest` and `pytest-asyncio`. Use the stdlib for everything else (`asyncio`, `argparse`, `tomllib`, `json`, `logging`, `subprocess`).
- `pyproject.toml` (hatchling or setuptools) plus a `Makefile` with `install` (venv `.venv`, `pip install -e '.[dev]'`), `test`, and `lint` (`python -m compileall`; no extra linters required).
- License: MIT.

## Modules
```
src/whoosh_connect/
  zwift.py      pure: Zwift controller protocol (constants, frame parsing, button bits)
  obc.py        pure: OpenBikeControl message encode/decode, IDs, TXT record
  mapping.py    pure: Button → Action map, config loading, ButtonTracker (diff → events)
  pipeline.py   glue: frames → tracker → mapper → sink fan-out; status handling
  ble.py        I/O: bleak scan/select/connect/handshake/reconnect (injectable factories)
  obc_server.py I/O: asyncio TCP server + zeroconf advertiser (injectable advertiser)
  keyboard.py   I/O: macOS key injection via osascript (injectable runner)
  state.py      unlock timestamp store, Zwift process detection (injectable)
  capture.py    JSONL capture writer and reader (probe2.py format)
  cli.py        argparse: run | unlock | replay; exit codes
```

### zwift.py
- Constants: `MANUFACTURER_ID = 0x094A`; `TYPE_CLICK_V2_LEFT = 0x0B`, `TYPE_CLICK_V2_RIGHT = 0x0A`; service `0000fc82-…`; chars `ASYNC = 00000002-19ca-4651-86e5-fa29dcdd09d1`, `SYNC_RX = 00000003-…` (host writes), `SYNC_TX = 00000004-…` (indications); `RIDE_ON = b"RideOn"`; `POST_HANDSHAKE = bytes([0xff, 0x04, 0x00])`.
- `enum Button(IntEnum)` holds bit positions: `LEFT=0, UP=1, RIGHT=2, DOWN=3, A=4, B=5, Y=6, Z=7, SHIFT_L=8, SHIFT_DN_L=9, POWERUP_L=10, ONOFF_L=11, SHIFT_R=12, SHIFT_DN_R=13, POWERUP_R=14, ONOFF_R=15`.
- `decode_varint(buf, i) -> (value, next_i)`, a LEB128 decoder. It raises `ValueError` on truncation.
- `parse_frame(data: bytes) -> Frame`, where `Frame` is a tagged dataclass:
  - `ButtonsFrame(pressed: frozenset[Button])`: opcode 0x23, field-1 tag 0x08, varint bitmask, **active-low** (a bit cleared means pressed). Bits above 15 are ignored.
  - `BatteryFrame(percent)`: opcode 0x19, protobuf field 2 (tag 0x10) varint.
  - `ChallengeFrame(raw)`: `ff 03 …`.
  - `VendorFrame(raw)`: other `ff …`.
  - `LogFrame(raw)`: 0x2a.
  - `RideOnFrame`: the reply starts with `RideOn`.
  - `UnknownFrame(raw)`: anything else, including malformed frames. Never raise (F8).
- `parse_adv(manufacturer_data: dict[int, bytes]) -> PuckInfo | None`, giving the side (left/right/unknown) and a 4-hex id from bytes 1..2 (e.g. `2d7e`).

### obc.py (per OpenBikeControl PROTOCOL.md / MDNS.md @ b0dbbe8)
- IDs: `SHIFT_UP=0x01, SHIFT_DOWN=0x02, GEAR_SET=0x03, UP=0x10, DOWN=0x11, LEFT=0x12, RIGHT=0x13, SELECT=0x14, BACK=0x15, MENU=0x16, HOME=0x17, STEER_LEFT=0x18, STEER_RIGHT=0x19, CAMERA=0x40, HUD=0x44`. Also a name table for logs.
- `encode_button_state(changes: list[tuple[int, bool]]) -> bytes` produces `01 id st id st …`, where st is 01 or 00.
- `encode_device_status(battery: int | None, connected: bool) -> bytes` produces `02 <0-100 or 0xFF> <00|01>`.
- `parse_app_info(data) -> AppInfo(app_id, version, buttons: frozenset[int])` follows the spec layout. It raises `ValueError` on malformed input, and buttons are empty when the count is 0 (meaning all supported).
- `split_messages(buffer) -> (messages, rest)` frames the TCP stream from the app. Known app→device types are 0x03 haptic (fixed 4 bytes) and 0x04 app info (self-describing length). Unknown type: drop the buffer and log.
- `txt_record(name, device_id) -> dict` returns `version=1, id, name, service-uuids=d273f680-d548-419d-b9d1-fa0472345229, manufacturer=whoosh-connect, model=Zwift Click v2 bridge`.

### mapping.py
- `Action` holds the OBC id plus a keyboard key (or None) and a name. `DEFAULT_MAP: dict[Button, Action]` is the table in journey.md.
- `load_map(path | None) -> dict[Button, Action]`: TOML `[map]` with `SHIFT_R = "SHIFT_UP"` style entries, keyed by `Button` name and `obc` id name. Unknown names produce a `ConfigError` with a clear message. Partial overrides merge onto the defaults, and the value `"NONE"` unmaps a button.
- `ButtonTracker.update(pressed) -> list[tuple[Button, bool]]` returns the transitions against the previous set, sorted by bit. It produces no output for a repeated frame (F10). `release_all()` returns releases for everything held (F2).

### pipeline.py
- `Pipeline(map, sinks, clock)` with `on_frame(Frame)` and `on_link(connected: bool, battery)`.
  - Button transitions are mapped through `map`, then sent to every sink as one batch per frame (F9). Unmapped buttons are logged at debug level.
  - Battery changes go to `sink.status(...)`.
  - `link(False)` triggers `release_all` and then a status update.
- Sink protocol: `async send(changes: list[tuple[Action, bool]])`, `async status(battery, connected)`, `async close()`. There are three sinks: `ObcSink` (wraps the server), `KeyboardSink`, and `PrintSink`.
- F3: if no `ButtonsFrame` arrives within 60 s of the link coming up, log the unlock hint once per link.

### ble.py
- `ClickLink(scanner_factory, client_factory, on_frame, on_link, clock)`; the factories default to bleak's `BleakScanner` and `BleakClient`.
- Selection (journey C4): collect adverts with `parse_adv`, choose the left puck, and if only the right puck is seen 10 s after the first advert, choose it with a warning. Print the wake hint every 30 s while nothing is found (F1).
- Connect sequence:
  1. `start_notify(ASYNC)`, then `start_notify(SYNC_TX)`.
  2. Write `RideOn` to `SYNC_RX` with `response=False`.
  3. Await a `RideOnFrame` within 5 s; on timeout, disconnect and retry (F7).
  4. Write `POST_HANDSHAKE` with `response=False`.
  5. Call `on_link(True, battery)`, reading the battery from 2A19 (best effort).
- Disconnect callback triggers `on_link(False)` and a reconnect with backoff 1, 2, 4, 8, 10, 10… s (F2). Before each attempt, call `state.zwift_running()` and warn if Zwift is running (F4).
- Map bleak errors for Bluetooth off or unauthorised to `BluetoothUnavailable`, which the CLI turns into exit code 3 (F11).
- Optional `capture` writer that records adv, tx, rx and link events.

### obc_server.py
- `ObcServer(host="0.0.0.0", port=36867, advertiser=ZeroconfAdvertiser, name="whoosh-connect")`.
- `start()` binds the first free port in 36867–36877 (F6; if none is free, raise `PortsBusy`, exit 4) and registers mDNS for the bound port and the primary IPv4. Tests inject a fake advertiser plus host `127.0.0.1` and port 0.
- Per client: read loop → `split_messages`; App Info is stored and logged (journey C6), haptic is ignored. On connect, the server sends the current device status.
- `broadcast(bytes)` writes to all clients (F12) and removes dead ones. With no clients, it drops the message and logs "no app connected" at most once per 30 s (F5).
- `stop()` unregisters mDNS and closes the clients and the server.

### keyboard.py
- `KeyboardSink(runner=subprocess.run, frontmost=…)`.
  - Press or release of a mapped key: send key down/up via `osascript -e 'tell application "System Events" to key down "k"'`.
  - For taps (shift), use `keystroke "k"` on press and nothing on release.
  - Holds (steer) use key down on press and key up on release.
- Guard: `frontmost()` uses osascript to read the frontmost app name. If it isn't the one containing "MyWhoosh", drop the action and log "MyWhoosh not frontmost".
- All subprocess calls go through `runner` (injectable). Never shell=True.

### state.py
- `StateStore(path=~/.config/whoosh-connect/state.json)` with `record_unlock(ts)`, `last_unlock()`, and `unlock_age(now)`.
- `zwift_running(ps=…) -> bool`: scans `ps -axo comm=` (names `ZwiftApp`, `Zwift`, `ZwiftLauncher`); `ps` is an injectable callable returning process names. (Implemented with `ps`, not `pgrep`, so the test guard can forbid `pgrep` outright.)
- `unlock_flow(clock, ps, store, sleep)` covers journey B (detect start → exit; at least 20 s of runtime to record).

### capture.py
- `CaptureWriter(path)` writes JSONL lines like `{"t": float, "dev": "1501", "ev": "adv|tx|rx|read|start|end|link", "ch": "00000002", "hex": "…"}`, the same as `scripts/probe2.py`.
- `read_capture(path) -> Iterator[dict]`.
- `replay(path, pipeline, device=None, speed=0)`:
  - It feeds `rx` frames on characteristics 00000002 and 00000004 into `parse_frame`, then `pipeline.on_frame`.
  - Default device: the left puck if present, detected from the `adv` mfr `0b…`, otherwise the first device.
  - `speed=0` means no sleeping.

### cli.py
- `whoosh-connect run [--sink obc|keyboard] [--config PATH] [--capture FILE] [--port N] [-v]`
- `whoosh-connect unlock`
- `whoosh-connect replay FILE [--sink print|obc|keyboard] [--speed N] [--device ID]`
- Exit codes: 0 ok, 2 usage/config error, 3 Bluetooth unavailable, 4 ports busy.
- Graceful Ctrl-C (journey C9): cancel tasks, then `sink.close()`, `server.stop()`, `link.close()`, all within 3 s.
- Logging: `HH:MM:SS message` to stderr; `-v` sets DEBUG with hex.

## Repo hygiene
- Keep the existing `README.md` (research), `raw/`, `notes/` and `scripts/` (research probes). Add a top section "Usage" to the README (install, unlock, run, MyWhoosh steps, troubleshooting).
- Copy the probe5, probe2 and probe3 captures into `tests/fixtures/` (or reference them from `raw/ble/`).
- `.gitignore`: `.venv/`, `__pycache__/`, `*.egg-info/`, `.pytest_cache/`, `dist/`.
- No secrets and no personal data; `raw/zwift/prefs.xml` is already redacted.

## Implementation notes
- `ClickLink(handshake_timeout=5, pair_wait=10)` take the timeouts as parameters so tests can shrink them.
- Keyboard key-up for held keys is never dropped by the frontmost check (it would leave the key stuck).
- Unverified on real hardware: `System Events` `key down "a"` may only accept modifier keys; if so, switch holds to key codes.
