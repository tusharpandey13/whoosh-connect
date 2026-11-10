# Hardware and protocol, as measured

Everything below comes from captures in `raw/` and from the owner's own devices,
not from documentation. Dates are when a thing was measured, because several
earlier conclusions were later overturned (see `notes/unlock-analysis.md`).

## Hardware

| Thing | Facts |
|---|---|
| Trainer | `VANRYSEL-HT-b50b` (Van Rysel D-series), CoreBluetooth id `BB48099E-F6E0-EDBB-DC8E-E471222C122F`, firmware 0.108, software "Jan 7 2025". Exposes FTMS **and** Zwift's trainer service `00000001-19CA-…` ("configured for Zwift Protocol" in Zwift's logs). |
| Zwift Cog | Passive, no electronics, no BLE. Fixed chainline; the app makes "gears" by changing resistance. |
| Click v2 **left** | Name `Zwift Click`, adv `0x094A: 0b 2d7e`, id `15018857-AF57-60C6-E4C3-49E31712DED0`, serial `0B-D852FAA92D7E`, HW `B.0`, FW `1.2.0`, battery 80 % → 70 % after ~1.5 h of probing. **Relays both pucks' buttons.** |
| Click v2 **right** | Adv `0a e89b`, id `F2640CDB-976F-2DB0-8780-2AA8386B5FFE`, serial `0A-6C1AEAD9E89B`, HW `B.0`, FW `1.2.0`. Reports only its own buttons. |

The 0x0B/0x0A side byte is BikeControl's interpretation (marked "unconfirmed"
there); the button data agrees with it.

## GATT (identical on both pucks)

```
180A Device Information   2A29 "Zwift Inc"  2A25 serial  2A27 "B.0"  2A26 "1.2.0"
FC82 Zwift controller
  00000002-19ca-4651-86e5-fa29dcdd09d1  notify        device -> host async messages
  00000003-19ca-4651-86e5-fa29dcdd09d1  write-no-rsp  host -> device commands
  00000004-19ca-4651-86e5-fa29dcdd09d1  read,indicate device -> host sync replies
  00000100/101/102-19ca-…               write/notify  unused by BikeControl (firmware/unlock?)
180F Battery               2A19 = 0x50 (80 %)
```

### Session

1. Write `RideOn` (`52 69 64 65 4f 6e`) to `0003` without response. A write *with*
   response is rejected ("Write Not Permitted").
2. `0004` indicates `RideOn` back in plain text. No ECDH, no encryption.
3. `0002` then sends:
   - `2a …` log record
   - `ff 05 00 fa/ea 05 …` vendor status, serial in ASCII, battery-ish varints
   - `ff 03 00 0a 21 …` ~85-byte **unlock challenge** (see below)
   - `19 10 50` battery heartbeat every 5 s
   - `ff 05 00 ea 05 …` / `fa 05 …` **"stopped talking"** once the window ends
4. Buttons: `23 08 <varint>`, the varint being an **active-low** bitmask
   (all released = `ff ff ff ff 0f`).

### Buttons, confirmed by a labelled run (`probe5`)

| puck | position | bit | mask | name | default action |
|---|---|---|---|---|---|
| left | up | 1 | `0x2` | UP | up |
| left | right | 2 | `0x4` | RIGHT | steer right |
| left | down | 3 | `0x8` | DOWN | down |
| left | left | 0 | `0x1` | LEFT | steer left |
| left | shift | 8 | `0x100` | SHIFT_UP_L ("−") | **gear down** |
| right | up | 6 | `0x40` | Y | camera |
| right | right | 4 | `0x10` | A | select |
| right | down | 5 | `0x20` | B | back |
| right | left | 7 | `0x80` | Z | menu |
| right | shift | 12 | `0x1000` | SHIFT_UP_R ("+") | **gear up** |

## The unlock challenge

`ff 03 00` + protobuf:

```
0a 21 <33 B, SEC1 point 0x02/0x03 + 32 B>   field 1: fresh per challenge
10 80 80 8c 10                              field 2: constant 33751040
1a <n> <n B>                                field 3: nonce, 40 / 52 / 58 bytes seen
```

Zwift POSTs the payload to `/api/d-lock-service/device/authenticate` and writes
the returned 23 bytes (`0a 15` + 21 B) back to `0003`.

Measured facts (11-Nov-2026):

- The service is **deterministic and non-expiring**: re-POSTing a challenge that
  was signed two hours earlier returned HTTP 200 and a byte-identical answer.
- `f3 = 40 B` always got **204** (8+ attempts); `f3 = 52/58 B` always got
  **200**. Latency 0.31–0.34 s against a 2.4 s mute deadline — latency was never
  the issue.
- No host command reliably produces the signable shape: `RideOn`, `RideOn+ff 04 00`,
  `RideOn+02 03` and a fully passive connection all produced 40-byte `f3` on 11-Nov.
  The 52/58-byte variant appeared twice on 10-Nov and never again.

### What the challenge does *not* do

Evidence: `raw/ble/2026-11-11/` (verbose bridge log of a working ride, plus captures).

It does not gate button reporting on firmware 1.2.0. A puck whose every challenge
went unanswered streamed all presses for a full ride. What ends a session is the
unit's own `ff 05 00 ea/fa 05` "stopped" frame, roughly **50 s after the press
that woke it**; the next press opens a new window.

`0x18` RESET (clickbridge's trick) restarts the unit, it drops the link,
reconnects and says "stopped" again seconds later — a reconnect loop. That is why
`--reset` is opt-in and off by default.

`ff 04 00` (BikeControl's unlocked-path command) is part of this bridge's normal
handshake and does **not** stop a locked unit from streaming — `probe4` looked
silent only because no button was pressed in its window. `02 02` on char `0100` earns a
one-byte `0a` reply and suppresses the challenge for that connection; `00 09 00`
earns `10`. Neither was proven to unlock anything (every trial that looked
"silent" had no press in the window).

## MyWhoosh side (Unreal Engine, v5.7.0 tested, bundle `com.whoosh.whooshgame`)

- No text logs. Saved hardware map `raw/mywhoosh/HardwareSaveData.sav` lists the
  trainer under `E_PowerSource`/`E_Controllable`/`E_Cadence`; no controller entry.
- Binary strings show `EVirtualGears::E_Gear1..30`, `GearUp`/`GearDown`,
  `IsVirtualGearShiftingFeatureEnabled`, `ConnectToOpenBike`. There is **no Zwift
  FC82 controller UUID**, so MyWhoosh cannot read a Click directly.
- Three injection routes exist: keyboard (`K`/`I`/`A`/`D`), the legacy MyWhoosh
  Link TCP-JSON protocol on 21587, and **OpenBikeControl** (what this repo uses).
- MyWhoosh draws its own gear number and runs the 30 virtual gears on the FTMS
  trainer itself.

### OpenBikeControl behaviour worth knowing

- On connect the app sends App Info (`04 …`); supported ids observed:
  `01 02 03 10 11 12 13 14 15 16 17 18 19 40`.
- It then **pings with a bare `00` every ~4 s**. The device is expected to answer
  with a status message (`02 <battery> <connected>`). Ignoring the ping is
  survivable; swallowing it while also never re-sending status means the game
  accepts nothing. Both bugs existed here until 11-Nov-2026.
- Device status must also be sent *after* the app-info handshake, not only when
  the TCP client connects.
