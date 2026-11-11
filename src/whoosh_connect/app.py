"""The menu-bar app: the bridge on an asyncio thread, the tray on the main thread (#2).

``Bridge`` is the same server -> pipeline -> link chain as ``cli._run``, plus what a
rider without a terminal needs: it never exits on a recoverable error, it reports
every state to a ``StatusModel``, and ``stop()`` closes the BLE link and the mDNS
record within 3 s.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import threading

from . import messages
from .ble import BluetoothUnavailable, ClickLink
from .mapping import ConfigError, load_map
from .obc_server import DEFAULT_PORT, AlreadyRunning, ObcServer, ObcSink, PortsBusy
from .pipeline import Pipeline
from .state import default_config_path, zwift_running
from .status import StatusModel

log = logging.getLogger(__name__)
STOP_TIMEOUT_S = 3.0


def default_link(on_frame, on_link):
    """Same link as ``whoosh-connect run`` without ``--reset``: the 0x18 reset loops a locked unit."""
    return ClickLink(on_frame, on_link, reset_enabled=False)


class Bridge:
    def __init__(self, model: StatusModel, *, config=None, port=DEFAULT_PORT,
                 server_factory=None, link_factory=default_link, ps=zwift_running,
                 sleep=asyncio.sleep, retry_s=5.0, poll_s=1.0, clear_after_s=2.0):
        self.model, self.config, self.port = model, config, port
        self.server_factory = server_factory or (lambda port: ObcServer(port=port))
        self.link_factory, self.ps, self.sleep = link_factory, ps, sleep
        self.retry_s, self.poll_s, self.clear_after_s = retry_s, poll_s, clear_after_s
        self.server = self.pipeline = self.link = None
        self._stopping = False
        self._tasks: list[asyncio.Task] = []

    # -- callbacks handed to ClickLink -------------------------------------------------
    async def _on_frame(self, frame) -> None:
        await self.pipeline.on_frame(frame)

    async def _on_link(self, up: bool, battery=None) -> None:
        self.model.set_link(up, battery)
        await self.pipeline.on_link(up, battery)

    # -- lifecycle ---------------------------------------------------------------------
    async def run(self) -> None:
        try:
            mapping = load_map(self.config or (p if (p := default_config_path()).exists() else None))
        except ConfigError as e:
            self.model.set_error(messages.explain(e))
            return
        while self.ps() and not self._stopping:
            self.model.set_error(messages.ZWIFT)
            await self.sleep(2)
        self.model.clear_error()
        if not await self._start_server():
            return
        self.pipeline = Pipeline(mapping, [ObcSink(self.server)])
        self._tasks = [asyncio.create_task(self._poll()), asyncio.create_task(self._tick())]
        await self._link_loop()

    async def _start_server(self) -> bool:
        while not self._stopping:
            self.server = self.server_factory(self.port)
            try:
                await self.server.start()
                return True
            except AlreadyRunning as e:  # the other instance owns the shifters; say so and idle
                self.model.set_error(messages.explain(e))
                self.server = None
                return False
            except PortsBusy as e:
                self.model.set_error(messages.explain(e))
                self.server = None
                await self.sleep(self.retry_s)
        return False

    async def _link_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while not self._stopping:
            self.link = self.link_factory(self._on_frame, self._on_link)
            # Leave an error up until the new attempt has survived a moment, so a
            # still-denied permission doesn't flicker Searching/Error every retry.
            clear = loop.call_later(self.clear_after_s, self.model.clear_error)
            try:
                await self.link.run()
            except BluetoothUnavailable as e:  # off or denied: explain, retry, recover (#4)
                self.model.set_error(messages.explain(e))
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - a rider can't restart a crashed thread
                log.exception("bridge link failed")
                self.model.set_error(messages.explain(e))
            finally:
                clear.cancel()
            if not self._stopping:
                await self.sleep(self.retry_s)

    async def _poll(self) -> None:
        while True:
            self.model.set_apps(len(self.server.app_infos))
            self.model.set_battery(self.pipeline.battery)
            await self.sleep(self.poll_s)

    async def _tick(self) -> None:
        while True:
            await self.sleep(5)
            self.pipeline.tick()

    async def stop(self) -> None:
        """Close the BLE link and unregister mDNS (via the sink) within STOP_TIMEOUT_S."""
        self._stopping = True
        for t in self._tasks:
            t.cancel()
        try:
            async with asyncio.timeout(STOP_TIMEOUT_S):
                if self.link:
                    await self.link.close()
                if self.pipeline:
                    await self.pipeline.close()  # ObcSink.close -> server.stop -> mDNS unregister
                elif self.server:
                    await self.server.stop()
        except (TimeoutError, Exception) as e:  # noqa: BLE001
            log.warning("shutdown incomplete: %r", e)


class LoopThread:
    """An asyncio loop on a daemon thread, so the main thread can run AppKit."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="bridge", daemon=True)

    def start(self, coro):
        self.thread.start()
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def call(self, coro, timeout: float):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def stop(self) -> None:
        """Cancel what is still running (Bridge.run), let it unwind, then stop the loop."""
        async def drain():
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for t in tasks:
                t.cancel()
            await asyncio.wait(tasks, timeout=1) if tasks else None

        try:
            self.call(drain(), 2)
        except Exception as e:  # noqa: BLE001 - quitting must always finish
            log.warning("quit: tasks did not unwind: %r", e)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=1)


def shutdown(bridge: Bridge, runner: LoopThread) -> None:
    """Quit path: stop the bridge (BLE + mDNS) on its own loop, then the loop."""
    try:
        runner.call(bridge.stop(), STOP_TIMEOUT_S + 1)
    except Exception as e:  # noqa: BLE001 - quitting must always finish
        log.warning("quit: %r", e)
    runner.stop()


def _bridge_ended(fut, model: StatusModel) -> None:
    """Bridge.run only returns early on a reported error; a crash must not leave a green icon."""
    if fut.cancelled():
        return
    if (e := fut.exception()) is not None:
        log.error("bridge crashed", exc_info=e)
        model.set_error(messages.explain(e))


def main(argv=None, tray_factory=None, bridge_factory=Bridge) -> int:
    logging.basicConfig(level=logging.DEBUG if argv and "-v" in argv else logging.INFO,
                        stream=sys.stderr, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    from .tray import Tray

    model = StatusModel()
    bridge = bridge_factory(model)
    runner = LoopThread()
    tray = (tray_factory or Tray)(quit=lambda: shutdown(bridge, runner))
    model.on_change = tray.update
    tray.update(model.snapshot())
    fut = runner.start(bridge.run())
    fut.add_done_callback(lambda f: _bridge_ended(f, model))
    log.info("whoosh-connect menu-bar app started")
    tray.run()  # blocks the main thread until Quit
    return 0
