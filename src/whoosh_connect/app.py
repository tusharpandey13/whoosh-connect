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
import time

from . import macos, messages
from .asleep import Event, SleepWatch
from .ble import BluetoothUnavailable, ClickLink
from .mapping import ConfigError, load_map
from .obc_server import DEFAULT_PORT, AlreadyRunning, ObcServer, ObcSink, PortsBusy
from .pipeline import Pipeline
from .state import default_config_path, zwift_running
from .status import Phase, StatusModel

log = logging.getLogger(__name__)
STOP_TIMEOUT_S = 3.0
QUIT_BUDGET_S = STOP_TIMEOUT_S + 0.5  # Bridge.stop + unwinding the loop, from the Quit click (#32)
MDNS_STOP_S = 1.5  # mDNS goodbye + TCP close; the BLE disconnect gets the rest


def default_link(on_frame, on_link):
    """Same link as ``whoosh-connect run`` without ``--reset``: the 0x18 reset loops a locked unit."""
    return ClickLink(on_frame, on_link, reset_enabled=False)


class Bridge:
    def __init__(self, model: StatusModel, *, config=None, port=DEFAULT_PORT,
                 server_factory=None, link_factory=default_link, ps=zwift_running,
                 sleep=asyncio.sleep, retry_s=5.0, poll_s=1.0, clear_after_s=2.0,
                 stop_s=STOP_TIMEOUT_S, mdns_stop_s=MDNS_STOP_S, clock=time.monotonic,
                 notify=None):
        self.model, self.config, self.port = model, config, port
        self.server_factory = server_factory or (lambda port: ObcServer(port=port))
        self.link_factory, self.ps, self.sleep = link_factory, ps, sleep
        self.retry_s, self.poll_s, self.clear_after_s = retry_s, poll_s, clear_after_s
        self.stop_s, self.mdns_stop_s = stop_s, mdns_stop_s
        self.server = self.pipeline = self.link = None
        self.watch = SleepWatch(clock=clock)
        self.notify = notify or (macos.notify if sys.platform == "darwin" else (lambda text: None))
        self.zwift_seen = False  # for Copy diagnostics (#10)
        self._stopping = False
        self._starting: asyncio.Future | None = None  # server.start() in flight (#32)
        self._tasks: list[asyncio.Task] = []

    # -- callbacks handed to ClickLink -------------------------------------------------
    async def _on_frame(self, frame) -> None:
        await self.pipeline.on_frame(frame)
        self._sleep_event(self.watch.on_frame(frame))

    async def _on_link(self, up: bool, battery=None) -> None:
        self.watch.on_link(up)
        self.model.set_link(up, battery)
        await self.pipeline.on_link(up, battery)

    def _sleep_event(self, event) -> None:
        """#9: one notification per closed window, only while MyWhoosh is connected."""
        if event is Event.ASLEEP:
            log.info("gears asleep: the Click closed its press window")
            self.model.set_asleep(True)
            if self.model.snapshot().phase is Phase.RIDING:
                self.notify(messages.ASLEEP.text.rstrip("."))
        elif event is Event.AWAKE:
            self.model.set_asleep(False)

    # -- lifecycle ---------------------------------------------------------------------
    async def run(self) -> None:
        try:
            mapping = load_map(self.config or (p if (p := default_config_path()).exists() else None))
        except ConfigError as e:
            self.model.set_error(messages.explain(e))
            return
        while self.ps() and not self._stopping:
            self.zwift_seen = True
            self.model.set_error(messages.ZWIFT)
            await self.sleep(2)
        self.model.clear_error()
        if not await self._start_server() or self._stopping:
            return
        self.pipeline = Pipeline(mapping, [ObcSink(self.server)])
        self._tasks = [asyncio.create_task(self._poll()), asyncio.create_task(self._tick())]
        await self._link_loop()

    async def _start_server(self) -> bool:
        while not self._stopping:
            self.server = self.server_factory(self.port)
            self._starting = asyncio.ensure_future(self.server.start())
            try:
                # Shielded: Quit lets a registration finish, then sends the goodbye (#32).
                await asyncio.shield(self._starting)
                return True
            except AlreadyRunning as e:  # the other instance owns the shifters; say so and idle
                self.model.set_error(messages.explain(e))
                await self._release(self.server)  # start() bound the TCP port before it found out
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

    @staticmethod
    async def _release(server) -> None:
        try:
            await server.stop()
        except Exception as e:  # noqa: BLE001
            log.debug("releasing unused server: %r", e)

    async def stop(self) -> None:
        """Unregister mDNS, then close the BLE link, within ``stop_s`` in total.

        mDNS goes first with its own budget: a stalled CoreBluetooth disconnect must not
        leave MyWhoosh listing a whoosh-connect that is gone. A registration still in
        flight is allowed to finish first, or a late announce would outlive the goodbye.
        """
        loop = asyncio.get_running_loop()
        end = loop.time() + self.stop_s
        left = lambda: max(0.0, end - loop.time())  # noqa: E731
        self._stopping = True
        if self._starting is not None and not self._starting.done():
            await asyncio.wait({self._starting}, timeout=min(self.mdns_stop_s, left()))
            if not self._starting.done():
                log.warning("shutdown: mDNS registration still running; cancelling it")
                self._starting.cancel()
                await asyncio.wait({self._starting}, timeout=min(0.2, left()))  # let it unwind
        for t in self._tasks:
            t.cancel()
        if self.pipeline:
            closing = self.pipeline.close()  # ObcSink.close -> server.stop -> mDNS unregister
        elif self.server:
            closing = self.server.stop()
        else:
            closing = None
        if closing is not None:
            await self._bounded("mDNS/server", closing, min(self.mdns_stop_s, left()))
        if self.link:
            await self._bounded("BLE link", self.link.close(), left())

    @staticmethod
    async def _bounded(what: str, coro, timeout: float) -> None:
        try:
            async with asyncio.timeout(timeout):
                await coro
        except (TimeoutError, Exception) as e:  # noqa: BLE001
            log.warning("shutdown incomplete (%s): %r", what, e)


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

    def stop(self, timeout: float = 1.0, clock=time.monotonic) -> None:
        """Cancel what is still running (Bridge.run), let it unwind, then stop the loop.

        Everything here shares ``timeout``: Quit blocks the menu while it runs.
        """
        end = clock() + timeout
        left = lambda: max(0.05, end - clock())  # noqa: E731

        async def drain():
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for t in tasks:
                t.cancel()
            await asyncio.wait(tasks, timeout=timeout / 2) if tasks else None

        try:
            self.call(drain(), left())
        except Exception as e:  # noqa: BLE001 - quitting must always finish
            log.warning("quit: tasks did not unwind: %r", e)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=left())


def shutdown(bridge: Bridge, runner: LoopThread, budget: float = QUIT_BUDGET_S,
             clock=time.monotonic) -> None:
    """Quit path: stop the bridge (BLE + mDNS) on its own loop, then the loop.

    One deadline from the click (#32): each step only gets what the previous one left.
    """
    end = clock() + budget
    try:
        runner.call(bridge.stop(), max(0.1, end - clock()))
    except Exception as e:  # noqa: BLE001 - quitting must always finish
        log.warning("quit: %r", e)
    runner.stop(max(0.1, end - clock()), clock=clock)


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
    try:
        return _main(tray_factory, bridge_factory)
    except Exception:  # noqa: BLE001
        # Exit 0 on purpose: the LaunchAgent (#3) restarts non-zero exits every 10 s,
        # and a broken install (e.g. pystray missing) would otherwise loop forever.
        log.exception("whoosh-connect could not start")
        return 0


def _main(tray_factory, bridge_factory) -> int:
    from .launchagent import LoginItem
    from .tray import Tray

    model = StatusModel()
    bridge = bridge_factory(model)
    runner = LoopThread()
    quit = lambda: shutdown(bridge, runner)  # noqa: E731
    def diagnostics() -> bool:
        from . import diagnostics as d
        return d.copy(d.collect(model.snapshot().line, bridge.zwift_seen))

    if tray_factory:
        tray = tray_factory(quit=quit)
        model.on_change = tray.update
    else:
        from .onboarding import Onboarding

        login = LoginItem()
        if login.refresh():
            log.info("start at login: app moved, LaunchAgent updated to %s", login.args()[0])
        tray = Tray(quit=quit, login=login, diagnostics=diagnostics)
        guide = Onboarding(notify=macos.notify, show=tray.set_setup)
        model.on_change = lambda s: (tray.update(s), guide.update(s))
    tray.update(model.snapshot())
    fut = runner.start(bridge.run())
    fut.add_done_callback(lambda f: _bridge_ended(f, model))
    log.info("whoosh-connect menu-bar app started")
    tray.run()  # blocks the main thread until Quit
    return 0
