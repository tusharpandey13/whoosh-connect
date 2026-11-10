# Physical-puck probes (11-Nov-2026)

These talk to a real Click v2 and need a human to press buttons. They are kept
because the notes cite them, not because they are polished.

| Script | Use |
|---|---|
| `unlock_shot.py` | one unlock attempt per wake cycle. `MODE=passive\|rideon\|ff0400`, optional `SLEEP` (quiet, then 3 beeps = press now), signs the `ff 03` challenge with a token file and pushes every framing with a beep before each press window. |
| `probe_0202_clean.py` | `RideOn` → `02 02`@`0100` → 30 s press window, no `ff 04 00` anywhere. The last confound-free `02 02` test. |
| `watch_click.py` | reconnecting listener: survives a sleep or a power cycle, logs every frame. `watch_click.py right 600`. |
| `listen_click.py` | same idea, one connection, no writes beyond `RideOn`. |

Conventions that matter:

- A Click answers with **one** challenge per wake cycle, and only after the press
  that woke it. Run exactly one shot, then let it sleep ~2 min before the next.
- Silence without a press in the window is **not** a result. Three conclusions in
  `notes/unlock-analysis.md` were wrong for exactly this reason.
- `osascript -e "beep N"` is how these ask the human to press; 1 beep = challenge
  seen, 2 = press now, 3+3 = accepted.

Token: `unlock_shot.py` reads `/tmp/whoosh-zwift.token` (mode 600). Populate it from
the running Zwift game (`whoosh-connect dlock` does the same via `acquire_token`).
Never commit that file.
