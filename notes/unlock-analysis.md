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
| live run #2, RideOn only, **no button pressed** | 100 B | 33 B (`02…`) / 58 B | **200, 23 B** `0a153507…044b6d` | pushed, 0 frames |

So: challenges with a 52/58-byte `f3` are signed by the cloud, and every
40-byte one we tried got 204. The answer is protobuf `field1 = 21 bytes`. A
stored answer is not valid for a different challenge — every shape carries a
fresh 33-byte `0x02/0x03` blob and a fresh 40-58 byte nonce.

### Correction: the push test was not a real test

The only push test that had a signed answer (live run #2) was run **without
pressing any Click button** (owner, 10-Nov). The puck emits `23 08` only when a
button is pressed; with no press, zero frames is the expected result for *every*
framing, accepted or not. `raw/ble/push_negative_2026-11-10.log` therefore does
**not** show that `ff0400+answer`, `ff04+answer` or bare `answer` are wrong — the
framing search has not actually been run yet. (The empty `ff 04 00` reply,
`empty_reply_negative_2026-11-10.log`, was also unpresssed.)

## Challenge payload shape (all three came from the same left puck, 1501/2D7E)

    protobuf: 0a 21 <0x02|0x03 + 32 B> 10 80 80 8c 10 1a <n> <n B>

- field 1: 33 B, a compressed P-256-style point (SEC1 prefix `02`/`03` + 32 B X).
  Fresh on every challenge, so it is a key, not an id.
- field 2: the same varint `0x02030000` (33751040) in all three captures.
- field 3: 40 / 52 / 58 B of high-entropy bytes. Only the 52 and 58 B variants
  were signed (200 + 23 B); the 40 B variant got 204.

The 82 B / f3=40 shape is the one seen after a press and it mutes the buttons;
the 94 B (spontaneous) and 100 B (RideOn, no press) shapes were signed. The
service is deciding on the *payload contents*, so never assume an `ff 03` is
signable: POST it and require 200 with a body.

### Evening timeline (local time) and the shape hypothesis

| time | connection | challenge | POST |
|---|---|---|---|
| 19:24 | Zwift pairs the Clicks | (Zwift signed) | unlock starts |
| 20:34:49 | passive spy, no RideOn, no writes | 94 B / f3=52 | 200, 23 B |
| 20:45 | our live run, **RideOn only** | 82 B / f3=40 | 204 |
| 20:47 | RideOn then empty `ff 04 00` reply | 82 B | not signed |
| 20:48:50 | reconnect | 100 B / f3=58 | stored answer pushed |
| 20:50:22 | reconnect | 100 B / f3=58 | 200, 23 B, pushed |

The unlock lapsed at 20:34:49, i.e. 70 min after the 19:24 auth. The shape seems
to follow the last host command: RideOn alone → 82 B (204); after the puck has
seen `ff 04 00` → 100 B (signed). The passive 94 B at 20:34 arrived after Zwift
had been talking to the puck, consistent with the same effect. This is a
hypothesis with n=1, which is why `dlock --live --ff0400` exists: send RideOn,
then `ff 04 00`, and see whether the signable 100 B shape comes back.

Consequences:
- The unlock is not replayable and not a constant secret: sign per challenge.
- The missing piece is the *write format* of the answer (prefix/wrapper,
  characteristic, write type) — and possibly latency, below.
- Unlock lifetime is **not** 24 h: last Zwift auth 19:24, first re-challenge
  20:34:49 (~70 min, and it re-challenged on every reconnect after that). The
  bridge now watches `0002` for `ff 03` and logs "pucks are locked" immediately
  (`Pipeline.locked`) instead of trusting a timestamp.

## Latency is a second suspect (2.4 s budget)

The puck stops streaming buttons **2.4 s after its `ff 03`**. Our signing path is
DNS + TLS + POST through `urllib`, a new connection every time; if that round trip
is over 2.4 s the answer is written after the puck muted itself and no framing can
work. `dlock --live` now prints the signing time and warns when it exceeds the
grace window. Measure that before blaming the framing. If it is slow, keep the TLS
connection alive (or pre-resolve/pre-connect) so the POST is one RTT.

## Candidate list now tried by `dlock.write_candidates`

1. `ff 04 00` + full 23 B answer, char `0003`, write-without-response
2. `ff 04 00` + **21 B inner signature** (`0a15` wrapper peeled), char `0003`
3. `ff 04` + answer, char `0003`
4. bare answer, char `0003`
5. the same four as write-with-response
6. last resort: `ff 04 00` + answer on `0004` (indication char; may reject the write)

`push()` prompts "press Click buttons now" before each window, counts all
notifications (not just `23 08`), and records write errors instead of aborting.

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
3. **Framing search with a press** (the real test): `dlock --live --push` against
   a locked puck, pressing a button in every prompted window. Silence only counts
   if the press happened. Check the printed signing time first: if it is > 2.4 s,
   fix latency before drawing conclusions.
4. Confirm the exact validity window: our unlock was recorded by Zwift at
   ~19:24 (10-Nov); poll with `scripts/spy.py` until `ff 03` reappears.
