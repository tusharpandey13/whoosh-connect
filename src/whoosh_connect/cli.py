"""Command line: run | unlock | replay."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

from .ble import BluetoothUnavailable, ClickLink
from .capture import CaptureWriter, replay
from .keyboard import KeyboardSink
from .mapping import ConfigError, load_map
from .obc_server import DEFAULT_PORT, ObcServer, ObcSink, PortsBusy
from .pipeline import Pipeline, PrintSink
from .state import StateStore, default_state_path, unlock_flow, zwift_running

log = logging.getLogger("whoosh_connect")
OK, USAGE, BT, PORTS = 0, 2, 3, 4
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
    sub.add_parser("unlock", help="guide the daily Zwift unlock")
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
            asyncio.run(unlock_flow(StateStore()))
            return OK
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


if __name__ == "__main__":
    sys.exit(main())
