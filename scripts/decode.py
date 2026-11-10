# Decode Zwift Click v2 0x23 button notifications from probe2.jsonl (active-low bitmask, protobuf field 1 varint).
import json, sys, collections
def varint(b, i):
    v = s = 0
    while True:
        x = b[i]; v |= (x & 0x7f) << s; i += 1; s += 7
        if not x & 0x80: return v, i
rows = [json.loads(l) for l in open(sys.argv[1])]
t0 = rows[0]["t"]; prev = {}; stats = collections.Counter()
for r in rows:
    if r["ev"] != "rx" or not r["hex"].startswith("2308"): continue
    b = bytes.fromhex(r["hex"]); v, _ = varint(b, 2)
    pressed = {i for i in range(32) if not (v >> i) & 1}
    old = prev.get(r["dev"], set())
    for i in pressed - old: print(f"{r['t']-t0:6.1f} {r['dev']} DOWN bit{i}"); stats[(r['dev'], i)] += 1
    for i in old - pressed: print(f"{r['t']-t0:6.1f} {r['dev']} UP   bit{i}")
    prev[r["dev"]] = pressed
print("presses per (device, bit):", dict(sorted(stats.items())))
