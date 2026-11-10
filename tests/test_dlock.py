"""D-Lock signing, capture picking, framing search and the passive spy (all offline)."""
import asyncio
import json

import pytest

from whoosh_connect import cli, dlock, spy as spy_mod
from whoosh_connect.capture import read_capture

CHAL_HEX = ("ff03000a21032950e0de470264b569b3ec2c9e06fade5022235fdd163d03ac24009d0772d4b3"
            "1080808c101a28cfe88c530576f4c0e0d3b5d4db88b00f5853bb8fc202165489c4b485954c66b"
            "0b1b11a5218a543d4")
ANSWER = bytes(range(23))


# --- challenge extraction -------------------------------------------------

def test_challenge_from_fixture(fix):
    chs = dlock.challenges(read_capture(fix / "probe2_burst.jsonl"))
    assert len(chs) == 1
    assert chs[0].raw.hex() == CHAL_HEX
    assert len(chs[0].raw) == 85 and len(chs[0].payload) == 82
    assert chs[0].payload.startswith(bytes([0x0A, 0x21]))


def test_no_challenge_when_unlocked(fix):
    for name in ("probe3_locked.jsonl", "probe5_unlocked.jsonl"):
        assert dlock.challenges(read_capture(fix / name)) == []


def test_challenges_ignore_other_frames_and_keep_order():
    rows = [
        {"ev": "rx", "ch": "00000002", "hex": "2308ffffffff0f", "t": 1.0},
        {"ev": "rx", "ch": "00000002", "hex": CHAL_HEX, "t": 2.0},
        {"ev": "rx", "ch": "00000004", "hex": CHAL_HEX, "t": 3.0},
        {"ev": "tx", "ch": "00000003", "hex": CHAL_HEX, "t": 4.0},
        {"ev": "rx", "ch": "00000002", "hex": "ff030004aabbccdd", "t": 5.0},
    ]
    chs = dlock.challenges(rows)
    assert [c.t for c in chs] == [2.0, 3.0, 5.0]
    assert chs[0].payload == bytes.fromhex(CHAL_HEX)[3:]


def test_payload_without_header_is_kept():
    raw = bytes.fromhex("0a21aabb")
    c = dlock.Challenge(raw)
    assert c.payload == raw


# --- framing candidates ---------------------------------------------------

def test_framing_candidates_order():
    cands = dlock.framing_candidates(ANSWER)
    assert list(cands) == ["ff0400+answer", "ff04+answer", "answer"]
    assert cands["ff0400+answer"] == bytes([0xFF, 0x04, 0x00]) + ANSWER
    assert cands["ff04+answer"] == bytes([0xFF, 0x04]) + ANSWER
    assert cands["answer"] == ANSWER


def test_unwrap_field1_peels_the_21_byte_signature():
    wrapped = bytes.fromhex("0a15350723904da4e3051a654e2766ae1b87500b044b6d")
    assert dlock.unwrap_field1(wrapped) == wrapped[2:]
    assert len(dlock.unwrap_field1(wrapped)) == 21
    assert dlock.unwrap_field1(ANSWER) is None          # not a field-1 wrapper
    assert dlock.unwrap_field1(b"\x0a\x15short") is None


def test_write_candidates_cover_sig_and_write_types():
    wrapped = bytes.fromhex("0a15350723904da4e3051a654e2766ae1b87500b044b6d")
    cands = dlock.write_candidates(wrapped)
    names = [c.framing for c in cands]
    assert names[:4] == ["ff0400+answer", "ff0400+sig", "ff04+answer", "answer"]
    assert names[-1] == "answer->0004"
    assert all(c.char == dlock.SYNC_RX for c in cands[:-1])
    assert [c.response for c in cands] == [False] * 4 + [True] * 4 + [False]
    sig = next(c for c in cands if c.framing == "ff0400+sig")
    assert sig.data == bytes([0xFF, 0x04, 0x00]) + wrapped[2:]


# --- signing --------------------------------------------------------------

async def test_sign_posts_payload_with_protobuf_and_bearer():
    calls = []

    def post(url, headers, body, timeout):
        calls.append((url, headers, body))
        return 200, ANSWER

    chal = dlock.Challenge(bytes.fromhex(CHAL_HEX))
    res = await dlock.sign(chal, "tok-123", post=post)
    url, headers, body = calls[0]
    assert url == dlock.ENDPOINT
    assert headers["Content-Type"] == "application/protobuf"
    assert headers["Authorization"] == "Bearer tok-123"
    assert body == chal.payload and len(body) == 82
    assert res.signed and res.body == ANSWER
    assert "23 B signed" in res.describe()


async def test_sign_reports_empty_and_error_bodies():
    chal = dlock.Challenge(bytes.fromhex(CHAL_HEX))
    empty = await dlock.sign(chal, "t", post=lambda *a: (204, b""))
    assert not empty.signed
    assert "0 B" in empty.describe()

    err = await dlock.sign(chal, "t", post=lambda *a: (400, b'ExceptionDto(key=client_error)'))
    assert not err.signed
    assert "400" in err.describe()


async def test_sign_status_200_without_body_is_not_success():
    chal = dlock.Challenge(bytes.fromhex(CHAL_HEX))
    res = await dlock.sign(chal, "t", post=lambda *a: (200, b""))
    assert not res.signed


# --- token ----------------------------------------------------------------

def test_token_from_ps_takes_access_token_only():
    line = ('/x/ZwiftAppSilicon --launcher_version=1.1.19 '
            '--token={"access_token":"AAA.bbb.ccc","refresh_token":"rrr","expires_in":21600}')
    assert dlock.token_from_ps(line) == "AAA.bbb.ccc"
    assert dlock.token_from_ps("no tokens here\n/unrelated --token={oops}") is None
    assert dlock.token_from_ps("") is None


def test_acquire_token_never_runs_ps_in_tests():
    text = '--token={"access_token":"t0k","refresh_token":"r"}'
    assert dlock.acquire_token(text) == "t0k"


# --- framing search on the puck ------------------------------------------

class FakeClient:
    def __init__(self, pucks=()):
        self.writes = []
        self.subs = []
        self.pucks = list(pucks)

    async def start_notify(self, ch, cb):
        self.subs.append((ch, cb))

    async def write_gatt_char(self, ch, data, response=False):
        self.writes.append(bytes(data))
        if self.pucks:
            self.subs[0][1](ch, bytes([0x23, 0x08, 0xFF]))


async def test_push_stops_at_first_accepted_framing():
    c = FakeClient(pucks=["ok"])
    res = await dlock.push(c, ANSWER, sleep=lambda s: asyncio.sleep(0), listen=0)
    assert [r.framing for r in res] == ["ff0400+answer"]
    assert res[0].buttons == 1
    assert c.writes[0] == bytes([0xFF, 0x04, 0x00]) + ANSWER


async def test_push_tries_all_framings_when_silent():
    c = FakeClient()
    res = await dlock.push(c, ANSWER, sleep=lambda s: asyncio.sleep(0), listen=0)
    assert [r.framing for r in res] == ["ff0400+answer", "ff04+answer", "answer",
                                        "ff0400+answer resp", "ff04+answer resp",
                                        "answer resp", "answer->0004"]
    assert all(r.buttons == 0 for r in res)
    assert len(c.writes) == 7


async def test_push_prompts_to_press_and_counts_any_traffic():
    c = FakeClient()
    prompts, seen = [], {"n": 0}

    def prompt(name, secs):
        prompts.append(name)

    res = await dlock.push(c, ANSWER, sleep=lambda s: asyncio.sleep(0), listen=0, seen=seen,
                           prompt=prompt)
    assert prompts == [r.framing for r in res]   # one prompt per write window
    assert [r.wrote for r in res] == [True] * 7


async def test_push_records_unwritable_candidate_and_keeps_going():
    class PickyClient(FakeClient):
        async def write_gatt_char(self, ch, data, response=False):
            if ch == dlock.SYNC_TX:
                raise RuntimeError("characteristic does not support write")
            await super().write_gatt_char(ch, data, response=response)

    c = PickyClient()
    res = await dlock.push(c, ANSWER, sleep=lambda s: asyncio.sleep(0), listen=0)
    assert res[-1].wrote is False and "does not support write" in res[-1].error


# --- passive spy ----------------------------------------------------------

class FakeAdv:
    def __init__(self, mfr: bytes):
        self.manufacturer_data = {0x094A: mfr}
        self.rssi = -50


class FakeDevice:
    def __init__(self, address):
        self.address = address


class FakeScanner:
    def __init__(self, cb):
        self._cb = cb

    async def __aenter__(self):
        self._cb(FakeDevice("1501-LEFT"), FakeAdv(bytes.fromhex("0b2d7e")))
        return self

    async def __aexit__(self, *a):
        return False


class SpyClient(FakeClient):
    async def connect(self):
        pass

    async def read_gatt_char(self, ch):
        return b""

    async def disconnect(self):
        pass


async def test_spy_subscribes_but_never_writes(tmp_path):
    clients = []
    frames = []

    def factory(device):
        c = SpyClient()
        clients.append(c)
        return c

    from whoosh_connect.capture import CaptureWriter
    writer = CaptureWriter(tmp_path / "cap.jsonl")
    s = spy_mod.Spy(on_frame=lambda dev, data: frames.append((dev, data)), capture=writer,
                    scanner_factory=FakeScanner, client_factory=factory, sleep=_sleep, scan_s=0)
    task = asyncio.create_task(s.run())
    await _until(lambda: clients and clients[0].subs)
    clients[0].subs[0][1](None, bytes.fromhex("2308ffffffff0f"))
    await s.close()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    writer.close()

    assert clients[0].writes == []                       # passive: nothing written
    assert [ch[:8] for ch, _ in clients[0].subs] == ["00000002", "00000004"]
    assert frames and frames[0][1][:2] == bytes([0x23, 0x08])
    rows = list(read_capture(tmp_path / "cap.jsonl"))
    assert {r["ev"] for r in rows} >= {"adv", "rx", "sub", "link"}


async def _sleep(_s):
    await asyncio.sleep(0)


async def _until(cond, n=200):
    for _ in range(n):
        if cond():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not met")


# --- CLI ------------------------------------------------------------------

def test_cli_dlock_without_challenge(tmp_path, capsys):
    p = tmp_path / "cap.jsonl"
    p.write_text(json.dumps({"ev": "rx", "ch": "00000002", "hex": "2308ffffffff0f"}) + "\n")
    assert cli.main(["dlock", str(p)]) == 2
    assert "no ff 03 challenge" in capsys.readouterr().err


def test_cli_dlock_signs_and_reports(fix, monkeypatch, capsys):
    async def fake_sign(challenge, token, **kw):
        assert len(challenge.payload) == 82
        return dlock.SignResult(200, ANSWER)

    monkeypatch.setattr(dlock, "sign", fake_sign)
    monkeypatch.setattr(dlock, "acquire_token", lambda *a: "tok")
    rc = cli.main(["dlock", str(fix / "probe2_burst.jsonl")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "23 B signed" in out


def test_cli_dlock_unsigned_returns_5(fix, monkeypatch, capsys):
    async def fake_sign(challenge, token, **kw):
        return dlock.SignResult(204, b"")

    monkeypatch.setattr(dlock, "sign", fake_sign)
    monkeypatch.setattr(dlock, "acquire_token", lambda *a: "tok")
    assert cli.main(["dlock", str(fix / "probe2_burst.jsonl")]) == 5
    assert "stale" in capsys.readouterr().err


def test_cli_dlock_needs_token(fix, monkeypatch, capsys):
    monkeypatch.setattr(dlock, "acquire_token", lambda *a: None)
    assert cli.main(["dlock", str(fix / "probe2_burst.jsonl")]) == 2
    assert "no Zwift token" in capsys.readouterr().err


# --- live: catch, sign, push ---------------------------------------------

class LiveClient(FakeClient):
    def __init__(self, challenge: bytes | None, buttons_after=None):
        super().__init__()
        self.challenge = challenge
        self.buttons_after = buttons_after
        self.rideon = []

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def write_gatt_char(self, ch, data, response=False):
        self.writes.append(bytes(data))
        if data == b"RideOn":
            self.rideon.append(True)
        if self.challenge and data == b"RideOn":
            self.subs[0][1](ch, self.challenge)
        if self.buttons_after and bytes(data).startswith(self.buttons_after):
            self.subs[0][1](ch, bytes([0x23, 0x08, 0xFF]))


async def test_live_signs_and_pushes_challenge():
    chal = bytes.fromhex(CHAL_HEX)
    c = LiveClient(chal, buttons_after=bytes([0xFF, 0x04, 0x00]))
    seen = []

    async def fake_sign(challenge, token, **kw):
        seen.append((challenge.raw, token))
        return dlock.SignResult(200, ANSWER)

    res = await dlock.live(c, "tok", sleep=lambda s: asyncio.sleep(0), wait=1, listen=0,
                           signer=fake_sign, on_log=lambda *_: None)
    assert c.rideon == [True]
    assert seen == [(chal, "tok")]
    assert res.answer == ANSWER
    assert res.push and res.push[0].buttons == 1
    assert c.writes[1] == bytes([0xFF, 0x04, 0x00]) + ANSWER


async def test_live_reports_still_unlocked():
    c = LiveClient(None)
    res = await dlock.live(c, "tok", sleep=lambda s: asyncio.sleep(0), wait=0.1, listen=0,
                           signer=lambda *a, **k: None, on_log=lambda *_: None)
    assert res.challenge is None and res.answer == b""
    assert c.writes == [b"RideOn"]


async def test_live_warns_when_the_answer_is_late(monkeypatch):
    chal = bytes.fromhex(CHAL_HEX)
    c = LiveClient(chal)
    monkeypatch.setattr(dlock, "GRACE_S", 0.0)
    logs = []

    async def fake_sign(challenge, token, **kw):
        return dlock.SignResult(200, ANSWER)

    await dlock.live(c, "tok", sleep=lambda s: asyncio.sleep(0), wait=1, listen=0,
                     signer=fake_sign, on_log=logs.append)
    assert any("late" in m for m in logs)


async def test_live_stops_when_service_signs_nothing():
    c = LiveClient(bytes.fromhex(CHAL_HEX))

    async def fake_sign(challenge, token, **kw):
        return dlock.SignResult(204, b"")

    res = await dlock.live(c, "tok", sleep=lambda s: asyncio.sleep(0), wait=1, listen=0,
                           signer=fake_sign, on_log=lambda *_: None)
    assert res.challenge is not None and res.answer == b""
    assert res.push is None
    assert len(c.writes) == 1  # RideOn only, no answer to push
