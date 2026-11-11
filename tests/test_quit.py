"""#32: one Quit deadline, and Quit while mDNS is still registering."""
import asyncio
import time

from test_app import FakeAdv, make_bridge, until
from whoosh_connect import app
from whoosh_connect.obc_server import ObcServer
from whoosh_connect.status import StatusModel


class SlowAdv(FakeAdv):
    """Registration takes ``delay`` s (zeroconf probing); ``None`` = never finishes."""

    def __init__(self, delay):
        super().__init__()
        self.delay = delay

    async def register(self, name, port, ip, txt):
        self.calls.append("register...")
        await (asyncio.sleep(self.delay) if self.delay is not None else asyncio.Event().wait())
        self.calls.append("register")


def slow_bridge(model, delay, **kw):
    adv = SlowAdv(delay)
    b, _ = make_bridge(model, server_factory=lambda port: ObcServer(
        host="127.0.0.1", port=0, advertiser=adv), **kw)
    return b, adv


async def test_quit_during_registration_says_goodbye_after_it(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    b, adv = slow_bridge(StatusModel(), delay=0.1)
    task = asyncio.create_task(b.run())
    await until(lambda: adv.calls == ["register..."])
    await b.stop()
    assert adv.calls == ["register...", "register", "unregister"]  # no announce after the goodbye
    await asyncio.wait_for(task, 1)  # run() notices it is stopping and returns
    assert b.link is None and b.pipeline is None


async def test_quit_during_stuck_registration_still_finishes_in_time(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("HOME", str(tmp_path))
    b, adv = slow_bridge(StatusModel(), delay=None, stop_s=0.3, mdns_stop_s=0.1)
    task = asyncio.create_task(b.run())
    await until(lambda: adv.calls == ["register..."])
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await b.stop()
    assert loop.time() - t0 < 0.5
    assert b._starting.cancelled() and "register" not in adv.calls
    assert "registration still running" in caplog.text
    task.cancel()


def test_shutdown_shares_one_deadline():
    now = [0.0]
    seen = []

    class Runner:
        def call(self, coro, timeout):
            coro.close()
            seen.append(("call", timeout))
            now[0] += 3.0  # Bridge.stop used most of the budget

        def stop(self, timeout, clock):
            seen.append(("stop", timeout))

    app.shutdown(app.Bridge(StatusModel()), Runner(), budget=3.5, clock=lambda: now[0])
    assert seen == [("call", 3.5), ("stop", 0.5)]


def test_shutdown_never_hands_out_a_zero_timeout():
    now = [0.0]
    seen = []

    class Runner:
        def call(self, coro, timeout):
            coro.close()
            now[0] += 10.0
            raise TimeoutError

        def stop(self, timeout, clock):
            seen.append(timeout)

    app.shutdown(app.Bridge(StatusModel()), Runner(), budget=3.5, clock=lambda: now[0])
    assert seen == [0.1]


def test_loop_thread_stop_is_bounded_by_its_timeout():
    released = [False]

    async def stubborn():
        while True:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                if released[0]:
                    raise
                # otherwise ignore Quit, like a wedged library call

    r = app.LoopThread()
    r.start(stubborn())
    t0 = time.monotonic()
    r.stop(timeout=0.3)
    assert time.monotonic() - t0 < 0.6
    released[0] = True  # tidy up so the test leaves no pending task behind
    r.thread.join(1)
    tasks = asyncio.all_tasks(r.loop)
    for t in tasks:
        t.cancel()
    r.loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
    r.loop.close()
