# What Zwift actually does to unlock a Click v2 (10-Nov-2026)

Evidence: `raw/ble/probe*.jsonl` (puck capture), `raw/ble/spy_2026-11-10_2030.jsonl`
(passive capture while real Zwift was connected), Zwift's own logs
(`~/Documents/Zwift/Logs/Log*.txt`) and a `strings` dump of
`~/Library/Application Support/Zwift/ZwiftAppSilicon`.

## The one-line answer

The Click v2 firmware does not verify a *local* secret: on connect it sends an
authentication challenge and only streams button frames once a **Zwift cloud
service has signed that challenge for your account**. Zwift's app is just the
transport between the puck and that service, which is why no local tool can
compute the answer (and why BikeControl proxies through real Zwift).

## Sequence, measured

| t (after connect) | direction | frame | note |
|---|---|---|---|
| +0.0 s | host → puck `0003` | `52 69 64 65 4f 6e` (`RideOn`) | write-without-response |
| +0.06 s | puck → host `0004` | `RideOn` | plain echo, no key exchange, no encryption |
| +0.06…7 s | puck → host `0002` | `23 08 <varint mask>` | buttons work **for a grace window only** |
| +~1 s | puck → host `0002` | `2a…` log, `ff 05 00 …` vendor status (serial `D852FAA92D7E`, battery) | |
| **+4.5 s** | **puck → host `0002`** | **`ff 03 00` + 82 bytes** | **auth challenge** |
| +6.9 s | — | button frames stop | 2.4 s after the challenge, if unanswered |
| every 5 s | puck → host `0002` | `19 10 50` | battery heartbeat only |

Challenge payload (82 B, protobuf-shaped):
`0a 21 <33 B, starts 0x03> · 10 <varint 4 B> · 1a 28 <40 B>`
— looks like a compressed EC public key (or device identity) + a 40-byte
nonce/challenge. The trainer's `ff 03` is 75/76 B, so the size is device-specific.

## Who signs it

`strings ZwiftAppSilicon` (line numbers in that dump):

```
144009  Received auth challenge (size = %zu)
        Device challenge failed: Network service not initialized
144011  Received auth response (size = %zu)
        Failed to find ZP component
        Device challenge failed: %s
```

plus the symbols `zwift::exercise_hardware::ZPAuth::ProcessAuthChallenge(std::vector<u8>)`
and
`DLockRestInvoker::challengeDevice(const std::vector<u8>&) → NetworkResponse<std::vector<u8>>`
with the API strings `Authenticate Device` → `/device/authenticate` under
`/api/d-lock-service`, and the hosts `https://us-or-rly101.zwift.com`,
`Authorization: Bearer …`.

So: `ZPAuth` receives the 82-byte challenge, `DLockRestInvoker` POSTs it to

```
POST https://us-or-rly101.zwift.com/api/d-lock-service/device/authenticate
Content-Type: application/protobuf        (415 for anything else)
Authorization: Bearer <game session token>
body: the 82-byte challenge, header (`ff 03 00`) NOT included
```

and Zwift writes the returned blob (log says **23 bytes**) to `0003`. Real log lines:

```
[19:52:54] [ZwiftProtocol] Received auth challenge (size = 82)
[19:52:54] [ZwiftProtocol] Received auth response (size = 0)     <- cloud call failed, puck stays locked
[19:24:14] (same pair, this time the click did unlock)
Log (old 3).txt: [19:57:51] Received auth challenge (size = 76) / (size = 23)
```

`size = 0` matches *"Device challenge failed: Network service not initialized"*:
no answer, no buttons. That is exactly `probe2/probe3/probe4` from our side.

## Consequences for whoosh-connect

- The 24 h "unlock" is a timestamp the puck keeps after one successful signed
  exchange; while it is fresh the puck never sends `ff 03` at all (`probe5`,
  `spy_2026-11-10_2030`) and buttons flow for the whole session.
- Once it lapses, the puck challenges once per wake-up and mutes itself if the
  answer never arrives (it does not re-challenge on the next connection, which
  is why `probe4`'s bare `RideOn + ff 04 00` test was inconclusive, not negative).
- macOS shares one BLE link between apps: a passive listener in another process
  sees everything Zwift sees (`spy.py` proved it). **But** macOS does not deliver
  a locally generated advertisement to a local scan, so a fake puck running on
  the same Mac is invisible to Zwift (`tools/fake_click.swift` advertises fine,
  `startAdvertising -> ok`, and neither bleak nor Zwift ever sees it). A
  peripheral-side MITM needs a second radio (Pi/ESP32/nRF), not this host.

## Tooling for the next attempt

```sh
whoosh-connect unlock --capture raw/ble/unlock.jsonl     # records the ff 03 challenge passively
whoosh-connect dlock raw/ble/unlock.jsonl                # POSTs it to the D-Lock service, own bearer token
whoosh-connect dlock raw/ble/unlock.jsonl --push         # writes the answer to the puck, watches for buttons
```

`--push` tries the three candidate framings and counts `23 08` notifications for
`--listen` seconds each: the first framing that makes the puck talk again is the
real one. Note that Zwift must still be running when `dlock` reads the token out
of the game process (`--token FILE` otherwise).

## Update, evening of 10-Nov (live experiments)

The D-Lock service does sign, and the puck does not care yet. Hard numbers:

| challenge (device -> `0002`) | payload | f1 / f3 | `POST /device/authenticate` | pushed back |
|---|---|---|---|---|
| 20:34:49, spontaneous, no RideOn | 94 B | 33 B (`03…`) / 52 B | **200, 23 B** `0a157af1…0af090` | not tried |
| live run, after RideOn | 82 B | 33 B (`02…`) / 40 B | 204, 0 B | n/a |
| `probe2` (locked, after RideOn) | 82 B | 33 B (`03…`) / 40 B | 204, 0 B | n/a |
| live run #2, after RideOn | 100 B | 33 B (`02…`) / 58 B | **200, 23 B** `0a153507…044b6d` | **rejected** |

So: *registration*-shaped challenges (94/100 B) are signed by the cloud, and the
82-byte one is not. The answer is protobuf `field1 = 21 bytes`. None of
`ff0400+answer`, `ff04+answer`, bare `answer` made the puck talk
(`raw/ble/push_negative_2026-11-10.log`), and an empty answer `ff 04 00` alone is
not enough either (`raw/ble/empty_reply_negative_2026-11-10.log`). A stored
answer is not valid for a different challenge — every shape carries a fresh
33-byte `0x02/0x03` blob and a fresh 40-58 byte nonce.

Consequences:
- The unlock is not replayable and not a constant secret: sign per challenge.
- The missing piece is the *write format* of the answer (sub-opcode/prefix/wrapper),
  not the signature. Only Zwift's own bytes can tell us that, which needs a
  peripheral-side proxy on a second radio (macOS will not loop a local
  advertisement back — `tools/fake_click.swift`).
- Unlock lifetime is **not** 24 h: last Zwift auth 19:24, first re-challenge
  20:34:49 (~70 min, and it re-challenged on every reconnect after that). The
  bridge should watch `0002` for `ff 03` and warn instead of trusting a timestamp.

## Experiments still worth running

1. **Fresh challenge → our own API call.** During the next lock window (~24 h
   after the last Zwift pairing), run `scripts/spy.py` while Zwift pairs: we see
   the 82 bytes but not the answer. Then POST those exact bytes to the D-Lock
   endpoint with a game bearer token:
   - a *stale* challenge (`probe2` bytes) returns **204, 0 bytes** today;
   - every malformed body returns 400 `ExceptionDto(client_error)`, so 204 means
     "parsed, nothing to sign" — i.e. probably rejected/expired.
   If a fresh challenge comes back with ~23 bytes, the unlock is fully scriptable.
2. **Replay test.** If the 23-byte answer is accepted for a *different* nonce,
   one captured answer is a permanent unlock. Test: send the old answer after a
   fresh `ff 03`, watch for button frames past the 2.4 s deadline.
3. **Framing search** (needs the answer, no MITM): the host→puck write is one of
   `ff 04 00 + answer`, `ff 04 + answer`, bare answer. Each candidate is testable
   because success is visible (buttons keep flowing; failure = silence).
4. Confirm the exact validity window: our unlock was recorded by Zwift at
   ~19:24 (10-Nov); poll with `scripts/spy.py` until `ff 03` reappears.
