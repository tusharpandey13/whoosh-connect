# 11-Nov-2026: the night the bridge actually rode

Everything here was produced on the owner's Mac with MyWhoosh 5.7.0 and the left
Click v2 (`2D7E`, firmware 1.2.0). The point of keeping it: these are the runs
that **overturned** "the unlock gates the buttons" and exposed the two OBC bugs
(the swallowed `0x00` ping and device status being sent only before the app-info
handshake).

| File | What it is |
|---|---|
| `ride_obc_verbose.log` | `whoosh-connect -v run`, the working session. Contains `MyWhoosh connected (app mywhoosh v5.7.0, supports: …)`, 64 × `ping from app, replying with status 024601`, the frames we sent (`-> 01 01 01` press / `-> 01 01 00` release) and every decoded button. **This is the end-to-end proof.** |
| `ride_obc_capture.jsonl` | BLE capture of the same kind of session (`--capture`): adverts, `RideOn`, `ff 03` challenge, `23 08` button frames, `ff 05 …` status, link events. |
| `ride_obc_frames.jsonl` | earlier capture from the same evening, before the ping fix (frames only, MyWhoosh silent). |
| `reset_early_loop.log` | the `0x18` RESET experiment: reset → link drop → reconnect → "stopped" again, repeatedly. Why `--reset` is opt-in. |
| `0202_no_press.log` | `02 02` on char `0100` with **no press** in the window. Worth keeping as a negative example: silence here proves nothing. |
| `right_puck_dormant.log` | the right puck (`E89B`) after a press: link accepted, `RideOn` echoed, no `23 08`, no `ff 03`, then it sleeps. Never explained. |

Related probes that produced these: `tools/probes/`.
