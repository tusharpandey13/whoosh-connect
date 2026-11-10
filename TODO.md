# TODO

Open research questions. Neither blocks riding — the bridge works without
answering either — but they're tracked here so the knowledge isn't lost.

## 1. Why the 58-byte `f3` challenge only appears sometimes

- **What:** on connect the puck can offer three unlock-challenge shapes —
  82 B (`f3=40`), 94 B (`f3=52`), 100 B (`f3=58`). Zwift's D-Lock cloud only
  signs the last one, and the puck only *sometimes* offers it.
- **Why it matters:** that's the only route found so far to uninterrupted
  button windows (no ~50 s press gating).
- **Leads:** watch characteristic `0002` for `ff 03` across many connects and
  compare what precedes each shape; note whether the 58-byte form correlates
  with the app-info handshake, a prior `RideOn`, or a fresh vs. re-connect.
- **Evidence:** `notes/unlock-analysis.md`, `docs/hardware.md`, `raw/ble/`.

## 2. Why the right puck `E89B` stays dormant

- **What:** the right puck never reports button presses in any capture; the
  left puck `2D7E` relays both units' buttons, so riding is unaffected.
- **Why it matters:** the right puck's own battery/button path is unverified;
  if the left puck dies, the right should be able to take over.
- **Leads:** connect to the right puck directly, press its buttons, and watch
  `0002`; check whether it sends `ff 05 00 …` "stopped" immediately, what its
  advertisement manufacturer-data reports, and whether its battery value is
  sane.
- **Evidence:** `raw/ble/2026-11-11/`.
