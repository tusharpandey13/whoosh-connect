"""Command line: run | unlock | dlock | replay."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

from . import dlock
from .ble import BluetoothUnavailable, ClickLink
from .capture import CaptureWriter, replay
from .keyboard import KeyboardSink
from .mapping import ConfigError, load_map
from .obc_server import DEFAULT_PORT, ObcServer, ObcSink, PortsBusy
from .pipeline import Pipeline, PrintSink
from .spy import Spy
from .state import StateStore, default_state_path, unlock_flow, zwift_running
from .zwift import TYPE_CLICK_V2_LEFT

log = logging.getLogger("whoosh_connect")
OK, USAGE, BT, PORTS, DLOCK = 0, 2, 3, 4, 5
WAIT_MSG = "Waiting for MyWhoosh: open the connection screen, tap the OpenBikeControl icon, pick whoosh-connect"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="whoosh-connect", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="bridge the Clicks to MyWhoosh")
    r.add_argument("--sink", choices=["obc", "keyboard"], default="obc")
    r.add_argument("--config")
    r.add_argument("--capture")
    r.add_argument("--port", type=int, default=DEFAULT_PORT)
    u = sub.add_parser("unlock", help="guide the daily Zwift unlock")
    u.add_argument("--capture", help="also record the BLE traffic (catches the ff 03 challenge)")
    d = sub.add_parser("dlock", help="sign a captured unlock challenge via Zwift's D-Lock service")
    d.add_argument("file", help="capture holding the ff 03 challenge")
    d.add_argument("--token", help="file with the game's bearer token (default: read it from the Zwift process)")
    d.add_argument("--all", action="store_true", help="try every challenge in the file")
    d.add_argument("--push", action="store_true", help="write the answer to the puck and watch for buttons")
    d.add_argument("--listen", type=float, default=8.0, help="seconds to watch for buttons per framing")
    p2 = sub.add_parser("replay", help="feed a capture through the pipeline")
    p2.add_argument("file")
    p2.add_argument("--sink", choices=["print", "obc", "keyboard"], default="print")
    p2.add_argument("--speed", type=float, default=0)
    p2.add_argument("--device")
    p2.add_argument("--config")
    p2.add_argument("--port", type=int, default=DEFAULT_PORT)
    return p


def _config_path(arg):
    if arg:
        return arg
    default = default_state_path().parent / "config.toml"
    return default if default.exists() else None


async def preflight(store, ps=zwift_running, clock=time.time, sleep=asyncio.sleep, say=print) -> None:
    if ps():
        say("Zwift is running and will grab the Clicks; quit it (Cmd-Q)")
        while ps():
            await sleep(2)
    age = store.unlock_age(clock())
    if age is None or age > 24 * 3600:
        say("Clicks probably locked: run `whoosh-connect unlock`")


async def _make_sink(kind: str, port: int):
    if kind == "print":
        return PrintSink()
    if kind == "keyboard":
        return KeyboardSink()
    server = ObcServer(port=port)
    await server.start()
    log.info(WAIT_MSG)
    return ObcSink(server)


async def _run(args) -> int:
    m = load_map(_config_path(args.config))
    await preflight(StateStore())
    sink = await _make_sink(args.sink, args.port)
    pipeline = Pipeline(m, [sink])
    cap = CaptureWriter(args.capture) if args.capture else None
    link = ClickLink(pipeline.on_frame, pipeline.on_link, capture=cap)

    async def ticker():
        while True:
            await asyncio.sleep(5)
            pipeline.tick()

    tasks = [asyncio.create_task(link.run()), asyncio.create_task(ticker())]
    try:
        await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            t.cancel()
        try:
            async with asyncio.timeout(3):
                await link.close()
                await pipeline.close()
        except (TimeoutError, Exception) as e:
            log.debug("shutdown: %r", e)
        if cap:
            cap.close()
    return OK


async def _unlock(args) -> int:
    cap = CaptureWriter(args.capture) if args.capture else None
    spy = None
    spy_task = None

    def on_frame(dev: str, data: bytes) -> None:
        if data.startswith(dlock.HEADER):
            log.info("captured the %d B unlock challenge from %s; when Zwift is done: "
                     "whoosh-connect dlock %s --push", len(data), dev[:4].upper(), args.capture)

    if cap:
        spy = Spy(on_frame=on_frame, capture=cap)
        spy_task = asyncio.create_task(spy.run())
    try:
        await unlock_flow(StateStore())
    finally:
        if spy:
            await spy.close()
        if spy_task:
            spy_task.cancel()
        if cap:
            cap.close()
    return OK


def _read_token(path) -> str | None:
    if not path:
        return None
    try:
        with open(path) as f:
            return f.read().strip() or None
    except OSError as e:
        raise FileNotFoundError(f"token file: {e}") from e


async def _find_puck(timeout: float = 60.0):
    from bleak import BleakScanner
    found: dict[str, tuple] = {}

    def cb(device, adv):
        mfr = adv.manufacturer_data.get(0x094A)
        if mfr:
            found[device.address] = (device, mfr[0])

    print("Press a Click button to wake it")
    async with BleakScanner(cb):
        for _ in range(int(timeout * 2)):
            await asyncio.sleep(0.5)
            if len(found) >= 2:
                break
    if not found:
        raise BluetoothUnavailable("no Click advertising; press a button to wake it")
    left = [d for d, kind in found.values() if kind == TYPE_CLICK_V2_LEFT]
    return left[0] if left else next(iter(found.values()))[0]


async def _dlock(args) -> int:
    chs = dlock.challenges_in_file(args.file)
    if not chs:
        print(f"no ff 03 challenge in {args.file} (was Zwift connected while capturing?)",
              file=sys.stderr)
        return USAGE
    token = _read_token(args.token) or dlock.acquire_token()
    if not token:
        print("no Zwift token: start Zwift (signed in) and keep it running, then retry — "
              "or pass --token FILE", file=sys.stderr)
        return USAGE
    picked = chs if args.all else chs[-1:]
    answer = None
    for c in picked:
        age = "" if c.t is None else f" (captured {int(time.time() - c.t)} s ago)"
        print(f"challenge {len(c.raw)} B{age}: {c.raw.hex()}")
        try:
            result = await dlock.sign(c, token)
        except OSError as e:
            print(f"  request failed: {e}", file=sys.stderr)
            return DLOCK
        print("  " + result.describe())
        if result.signed:
            answer = result.body
            break
    if answer is None:
        print("nothing signed: the challenge is probably stale (each Zwift session signs a new one)",
              file=sys.stderr)
        return DLOCK
    if not args.push:
        print("add --push to write it to the puck and see whether buttons start flowing")
        return OK
    try:
        from bleak import BleakClient
        device = await _find_puck()
        async with BleakClient(device) as client:
            for r in await dlock.push(client, answer, listen=args.listen):
                verdict = f"{r.buttons} button frames -> accepted" if r.buttons else "silent"
                print(f"  {r.framing}: {verdict}")
                if r.buttons:
                    print("unlock works without Zwift; record it and stop running Zwift")
                    StateStore().record_unlock(time.time())
                    break
    except BluetoothUnavailable as e:
        print(str(e), file=sys.stderr)
        return BT
    return OK


async def _replay(args) -> int:
    pipeline = Pipeline(load_map(args.config), [await _make_sink(args.sink, args.port)])
    try:
        await replay(args.file, pipeline, args.device, args.speed)
    finally:
        await pipeline.close()
    return OK


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    try:
        if args.cmd == "unlock":
            return asyncio.run(_unlock(args))
        if args.cmd == "dlock":
            return asyncio.run(_dlock(args))
        return asyncio.run(_run(args) if args.cmd == "run" else _replay(args))
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return USAGE
    except BluetoothUnavailable as e:
        print(str(e), file=sys.stderr)
        return BT
    except PortsBusy as e:
        print(str(e), file=sys.stderr)
        return PORTS
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return USAGE
    except KeyboardInterrupt:
        return OK
        return OK


if __name__ == "__main__":
    sys.exit(main())
