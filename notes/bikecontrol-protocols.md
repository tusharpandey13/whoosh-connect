# BikeControl protocol notes (Zwift Click v2, MyWhoosh)

Source: /tmp/bcsrc/bikecontrol @ edd1e2a (Flutter/Dart). Paths relative to repo root.
Caveat: the public repo STUBS the core unlock logic (`prop_public/lib/devices/click_logic.dart:1-5`,
`prop_public/lib/emulators/definitions/zwift_click_definition.dart:1-4` "This is a stub - contact me").
So the real Click v2 unlock handshake is NOT visible; only the public-facing parts are.

## 1. Zwift Click v2 BLE

### UUIDs (lib/bluetooth/devices/zwift/constants.dart)
- Service `0000fc82-0000-1000-8000-00805f9b34fb` (Ride/Click v2) :10; legacy `00000001-19CA-...` :8. Lookup tries FC82 first, then 0001 (zwift_device.dart:37-43).
- 0002 ASYNC (device->host notify) :12; 0003 SYNC_RX (host writes here) :13; 0004 SYNC_TX (indications) :14. Base `-19CA-4651-86E5-FA29DCDD09D1`.
- Subscribe: notifications on 0002, **indications** on 0004 (zwift_device.dart:88-89). Writes to 0003 are write-without-response (zwift_ride.dart:139-145, 301-307).
- The task text mentions chars 00000100/101/102: NOT referenced anywhere in BikeControl.
- Zwift manufacturer id 2378 (0x094A) (constants.dart:16). First payload byte = device type: 0x09 Click(BC1), 0x0A Click v2 RIGHT, 0x0B Click v2 LEFT (marked "unconfirmed") (constants.dart:32-37; mapping 202-208; dispatch bluetooth_device.dart:499-509).
- Click v2 is TWO pucks, each its own BLE peripheral (left 0x0B, right 0x0A). Left: nav diamond + shiftUpLeft; right: Y/Z/A/B + shiftUpRight (zwift_clickv2_left_side.dart:15-21, right_side.dart:26-32). Check which puck your unit is (adv mfr data byte) - the "device name" path `'Zwift Click' => ZwiftClickV2` is the legacy unified class (bluetooth_device.dart:274).

### Handshake
- Plain, no ECDH implemented in public code. Host writes ASCII `RideOn` = `52 69 64 65 4f 6e` (constants.dart:39; zwift_ride.dart:138-146).
- Expected device reply on 0004 (indication) starts with `RideOn` + 2 bytes: Click v2 = `02 03` (constants.dart:46, zwift_clickv2.dart:54). Click v1 `01 03`, Play `01 04` (:44-45). Comment: "these don't actually seem to matter, header must be 7 bytes RIDEON+2" (:42). After that "Device Public Key" bytes are only debug-printed, never used (zwift_ride.dart:171-178). No key exchange / encryption of later traffic is implemented.
- Reply recognition: `bytes.startsWith(startCommand)` -> else `processData` (zwift_ride.dart:155-159). Plain-`RideOn` echo with no `02 03` tail (your device) would fall to processData; opcode 0x52 = RIDE_ON -> only sets initializationTime (zwift_ride.dart:162-165).
- **Unlock gate (the important part)**: for Click v2 (`usesZwiftUnlock => true`, zwift_clickv2.dart:41-42) setupHandshake is:
  ```dart
  if (isPersistedUnlocked || hasScript) {
    super.setupHandshake();                       // RideOn on 0003
    await sendCommandBuffer([0xFF, 0x04, 0x00]);  // second write, 0003
  }
  ```
  (zwift_unlock.dart:217-225). So when NOT unlocked BikeControl sends NOTHING at all (not even RideOn). When unlocked it sends `RideOn` then raw `ff 04 00`. We should try writing `ff 04 00` after RideOn.
- Right puck (not locked): `sendCommandBuffer(RideOn + 02 03)` i.e. 8 bytes `52 69 64 65 4f 6e 02 03` then `ClickLogic.setupHandshake(isRight:true)` (stubbed) (zwift_clickv2_right_side.dart:116-120, 85-86). Left puck does the same 8-byte write first (left_side.dart:76-84). **Note: host sends `RideOn 02 03`, not bare `RideOn`, on those paths.**

### "Unlocked" requirement (explains no button events)
- Click v2 firmware 1.2.0 (BikeControl's `latestFirmwareVersion`, zwift_clickv2.dart:56-57) is Zwift-locked: it must have been connected to the real Zwift app within ~24 h. TROUBLESHOOTING.md:17-34: "connect it in the Zwift app once before a workout... If you don't, BikeControl will need to reconnect every minute." Manual method: Zwift app -> connect trainer then Click v2, keep 10-30 s, close Zwift, then connect in BikeControl.
- BikeControl treats unlock as valid 24 h (zwift_unlock.dart:181-188, "persisted"); `markUnlockedManually` (204-207).
- Locked Ride V2 hides the FC82 service entirely until Zwift unlocks it (zwift_ride.dart:91-102; zwift_unlock.dart:238-246). Yours exposes it, so it is partly awake.
- In-app unlock = BikeControl emulates a Zwift-visible trainer over DirCon/mDNS (`ftmsEmulator`, advertised under the Zwift name while `zwiftUnlockInProgress`, zwift_unlock.dart:36-60, 29) and proxies the puck's FC82 service to Zwift (`ZwiftClickDefinition`, services FC82 with chars 0003 write, 0002/0004 notify; zwift_click_definition.dart:35-51). Zwift answers the puck's `ff 03` vendor challenge; BikeControl captures the 0002 message with opcode 0xFF and byte[1]==0x03 as `_vendorMessage` and replays it to Zwift on reconnect (zwift_unlock.dart:145-147, 282-291). => the `ff03000a21...` blob is the vendor/unlock challenge; the puck stays "silent" on buttons until Zwift (or a replay) completes it. The response format is in the stubbed code - not recoverable here.
- Workaround modes (ClickV2 onboarding): "right side only" (no unlock, never restarts; keep-awake logic) vs "unlock with Zwift" (both pucks, 24 h). Legacy: left puck RESET loop - app sends Opcode.RESET (24) periodically to force restart (zwift_unlock.dart:401; CHANGELOG.md:406 "reset the device when button events are no longer sent"; right_side.dart:47-51, left_side.dart:27-30 "restart loop... rebooting the puck every few seconds"). Logic stubbed (click_logic.dart:4,53).

### Message types (first byte of 0002/0004 notifications)
Opcode enum prop_public/lib/protocol/zp.pbenum.dart: 0x19(25) BATTERY_NOTIF, 0x23(35) CONTROLLER_NOTIFICATION, 0x2A(42) LOG_DATA, 0x3C GET_RESPONSE, 0x3E STATUS_RESPONSE, 0x52 RIDE_ON, 0x18(24) RESET, 0xFE LOST_CONTROL, 0xFF VENDOR_MESSAGE.
Handling (zwift_ride.dart:150-237, ZwiftRide overrides processData):
- `0x23` + protobuf `RideKeyPadStatus`: field1 varint ButtonMap, field3 repeated analog paddles (zwift.pb.dart:504-505). Test frame: `23 08 ff ff fe ff 0f` (zwift_clickv2_button_passthrough_test.dart:76).
- `0x19` BatteryNotification proto (`19 10 50` = field2 varint 0x50 = 80 %): your `191050` every 5 s is a battery report (zwift_ride.dart:214-220). Also `0x1A` BatteryStatus (:230).
- `0x2A` LogDataNotification (debug only, :208-213). Yours `2a0803121212...` is a log record.
- `0xFF` VENDOR_MESSAGE ignored (:206). Variants `ff 05 00 ea 05`/`ff 05 00 fa 05` are named RESPONSE_STOPPED_CLICK_V2_VARIANT_1/2 ("from device") but unused elsewhere (constants.dart:47-48). `ff 03 ...` = unlock challenge (above). `ff 04 00` = host->device (above).
- Constants 0x15 empty, 0x3C UNKNOWN_CLICKV2_TYPE, 0xFE disconnect "if connected to Core then Zwift connects to it" (constants.dart:50-62) - legacy ZwiftDevice path.

### Button bitmask decode (active-LOW: bit==0 means pressed)
`PlayButtonStatus.ON = 0`, OFF = 1 (zwift.pbenum.dart:17-18). Pressed iff `(ButtonMap & mask) == 0` (zwift_ride.dart:248-264). Released frame = all ones `08 ff ff ff ff 0f` (controller_keep_alive.dart:6).
RideButtonMask (zwift_ride.dart:326-345):
```
0x00001 LEFT   0x00002 UP    0x00004 RIGHT  0x00008 DOWN
0x00010 A      0x00020 B     0x00040 Y      0x00080 Z
0x00100 SHIFT_UP_L  0x00200 SHIFT_DN_L  0x01000 SHIFT_UP_R  0x02000 SHIFT_DN_R
0x00400 POWERUP_L   0x04000 POWERUP_R   0x00800 ONOFF_L      0x08000 ONOFF_R
```
Click v2 left uses nav + SHIFT_UP_L ("minus"); right uses A/B/Y/Z + SHIFT_UP_R ("plus") (left_side.dart:94-98 filters; right_side.dart:26-32). Duplicate identical frames are dropped (zwift_ride.dart? zwift_device.dart:208-214). Analog paddles (Ride only): loc 0/1, |value|>=25 (zwift_ride.dart:22, 271-286).
Varint note: ButtonMap is a protobuf varint (tag 0x08) -> `ff ff fe ff 0f` = 0xFFFFBFFF? decode as LEB128 (7 bits/byte), not raw LE.

## 2. MyWhoosh integration
Two official routes (my_whoosh.dart:15-18): `myWhooshLink` and `obpMdns` (OpenBikeControl).

### A. "MyWhoosh Link" (legacy, deprecated by 5.2.0 CHANGELOG.md:198-206)
- BikeControl is the **TCP server**, fixed port **21587**, no fallback (lib/bluetooth/devices/mywhoosh/link.dart:47-55). MyWhoosh (client) connects to it; discovery is on MyWhoosh's side (code has no advertising; instructions say run the "MyWhoosh Link" app once, same Wi-Fi: INSTRUCTIONS_MYWHOOSH_LINK.md:1-6). Only same-LAN / local-network permission matters (:11-14).
- Wire format: one JSON object per line (`writeln`), only BikeControl->MyWhoosh (link.dart:171-172); inbound only logged (:66-73). Examples (link.dart:104-152):
```json
{"MessageType":"Controls","InGameControls":{"GearShifting":"1"}}
{"MessageType":"Controls","InGameControls":{"GearShifting":"-1"}}
{"MessageType":"Controls","InGameControls":{"Steering":"-1"}}   // key down; "0" on key up; "1" = right
{"MessageType":"Controls","InGameControls":{"CameraAngle":"<n>"}}
{"MessageType":"Controls","InGameControls":{"Emote":"<1-7>"}}
{"MessageType":"Controls","InGameControls":{"UTurn":"true"}}   // also "Tuck":"true"
```
Values are strings. Non-steering actions only sent on key-down (:161-169).

### B. OpenBikeControl (OBC) - current official (CHANGELOG.md:198-206)
- mDNS service `_openbikecontrol._tcp` (instance "BikeControl"), TXT `version=1,id=1337,name=BikeControl,service-uuids=d273f680-d548-419d-b9d1-fa0472345229,manufacturer=OpenBikeControl,model=BikeControl app`; TCP port default **36867** (walks up on contention, advertises actual) (obc_mdns_emulator.dart:171-197, 215-240; openbikecontrol_device.dart:73,76). DirCon variant: `_wahoo-fitness-tnp._tcp` with TXT ble-service-uuids/mac-address/serial-number (:215-222). In MyWhoosh: connection screen -> OpenBikeControl icon top right (CHANGELOG.md:203). Windows needs Bonjour (obc_mdns_emulator.dart:98-112).
- Binary messages (protocol_parser.dart:84-88, 114-125): `[0x01 ButtonState][btnId][state]...`; 0x02 device status, 0x03 haptic, 0x04 app info. Button IDs MyWhoosh supports (my_whoosh.dart:38-53): 0x01 ShiftUp, 0x02 ShiftDown, 0x03 GearSet, 0x10-0x17 nav/menu/home, 0x18/0x19 steer L/R, 0x40 camera. Steering angle 0x1B: `01 1b <v>`, 0x80 center, 0.5 deg steps (protocol_parser.dart:90-112).

### C. Keyboard fallback (desktop, my_whoosh.dart:111-193)
shiftDown = **I**, shiftUp = **K**, steerLeft = **A**, steerRight = **D** (long-press), arrows also steer, toggleUi = **H**, emotes **1-7** (Peace, Wave, First Bump, Dab, Elbow Flick, Toast, Thumbs up; :61-109). Android touch fallback coordinates e.g. shiftUp (97,94), shiftDown (80,94). Keys can be targeted to the MyWhoosh process (desktop.dart:158). CHANGELOG.md:315, 454.

### Virtual shifting / trainer
- MyWhoosh `virtualGearAmount = 30` (my_whoosh.dart:29-30; default 24 supported_app.dart:178). BikeControl does VS by emulating an FTMS trainer ("virtual bike", FitnessBikeDefinition) over BLE or DirCon-WiFi; MyWhoosh on Android only pairs trainers over Bluetooth (my_whoosh.dart:32-35). Needs an FTMS/FE-C trainer proxied by BikeControl (ProxyDevice, proxy_device.dart:44-55). Plain Link/OBC shift commands do NOT need a trainer. MyWhoosh draws its own gear number (`showsOwnGear`, :11-12). MyWhoosh needs mDNS TXT `serial-number` on the DirCon trainer (connection.dart:1175-1177).

## 3. Van Rysel / Decathlon, FC82 on trainers
- Van Rysel HT (D100/D500, RCR) advertise `VANRYSEL-HT...`, often FE-C only or nothing; BikeControl matches by name -> ProxyDevice, services found on connect (bluetooth_device.dart:179-186, 304-305, 400-401; proxy_device.dart:49-54; test/bluetooth/vanrysel_d_series_detection_test.dart). Known quirks: slow-cadence reading hurts target power (CHANGELOG.md:15), drops ~every minute breaking VS gear state -> last gear remembered per trainer (proxy_device.dart:666-683). Nothing Zwift-protocol on these trainers.
- FC82 appears on the trainer side only as an EMULATED service: BikeControl adds `0000fc82` Ride service to its virtual FTMS bridge so Zwift sees controller+trainer together (proxy_device.dart:530-536). Rouvy refuses a bridge with a controller service (zwift_unlock.dart:155-160). No MyWhoosh-specific FC82 handling found; no real FC82-on-trainer support.

## Suggested next experiments (our device)
1. Write `ff 04 00` to 0003 right after the `RideOn` write (the only post-handshake write BikeControl does).
2. Try `52 69 64 65 4f 6e 02 03` as the first write (what the puck code sends) instead of bare RideOn.
3. Subscribe to INDICATE on 0004 and NOTIFY on 0002 (both); BikeControl does both.
4. If still silent, the puck is Zwift-locked: unlock once via real Zwift (trainer first, then Click v2, 10-30 s) and retest within 24 h; or capture the `ff 03` challenge exchange with Zwift (BLE sniff) since the response is not in the public repo.
5. Confirm puck side (adv mfr data byte 0x0A/0x0B); each puck is a separate peripheral.
