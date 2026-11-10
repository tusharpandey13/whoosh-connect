"""OpenBikeControl TCP server + mDNS advertiser."""
from __future__ import annotations

import asyncio
import errno
import logging
import socket
import time

from . import obc

log = logging.getLogger(__name__)
SERVICE_TYPE = "_openbikecontrol._tcp.local."
DEFAULT_PORT = 36867
PORT_SPAN = 11


class PortsBusy(Exception):
    pass


def primary_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # UDP connect sends nothing
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class AlreadyRunning(Exception):
    """Another whoosh-connect already advertises the OpenBikeControl name."""


class ZeroconfAdvertiser:
    def __init__(self):
        self._az = self._info = None

    async def register(self, name: str, port: int, ip: str, txt: dict[str, str]) -> None:
        from zeroconf import ServiceInfo
        from zeroconf.asyncio import AsyncZeroconf
        try:
            from zeroconf.exceptions import NonUniqueNameException
        except ModuleNotFoundError:  # zeroconf >= 0.132 keeps it private
            from zeroconf._exceptions import NonUniqueNameException

        self._info = ServiceInfo(
            SERVICE_TYPE, f"{name}.{SERVICE_TYPE}", port=port,
            addresses=[socket.inet_aton(ip)], properties=txt, server=f"{name}.local.")
        self._az = AsyncZeroconf()
        try:
            await self._az.async_register_service(self._info)
        except NonUniqueNameException as e:
            await self.unregister()
            raise AlreadyRunning(
                f"{name} is already advertised on this machine: another `whoosh-connect run` is "
                f"still alive. Stop it first (pkill -f whoosh_connect) or use that one.") from e

    async def unregister(self) -> None:
        if self._az:
            await self._az.async_unregister_service(self._info)
            await self._az.async_close()
            self._az = None


class ObcServer:
    def __init__(self, host="0.0.0.0", port=DEFAULT_PORT, advertiser=None,
                 name="whoosh-connect", span=PORT_SPAN, clock=time.monotonic):
        self.host, self.port, self.name, self.span, self.clock = host, port, name, span, clock
        self.advertiser = advertiser or ZeroconfAdvertiser()
        self._server: asyncio.AbstractServer | None = None
        self._clients: dict[asyncio.StreamWriter, obc.AppInfo | None] = {}
        self._status = obc.encode_device_status(None, False)
        self._last_warn = float("-inf")

    @property
    def app_infos(self) -> list[obc.AppInfo]:
        return [i for i in self._clients.values() if i]

    def supports(self, button_id: int) -> bool:
        """False if a connected app explicitly lists buttons without this one."""
        return all(not i.buttons or button_id in i.buttons for i in self.app_infos)

    async def start(self) -> int:
        ports = [0] if self.port == 0 else range(self.port, self.port + self.span)
        for p in ports:
            try:
                self._server = await asyncio.start_server(self._handle, self.host, p)
                break
            except OSError as e:
                if e.errno != errno.EADDRINUSE:
                    raise
        else:
            raise PortsBusy(f"no free TCP port in {ports[0]}-{ports[-1]}")
        self.port = self._server.sockets[0].getsockname()[1]
        ip = primary_ip() if self.host in ("0.0.0.0", "") else self.host
        await self.advertiser.register(self.name, self.port, ip, obc.txt_record(self.name, self.name))
        log.info("OpenBikeControl listening on %s:%d", ip, self.port)
        return self.port

    async def _handle(self, reader, writer):
        self._clients[writer] = None
        peer = writer.get_extra_info("peername")
        sock = writer.get_extra_info("socket")
        if sock is not None:  # tiny shift messages must not wait for Nagle batching
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
        log.info("app connected from %s", peer)
        writer.write(self._status)
        buf = b""
        try:
            while data := await reader.read(1024):
                msgs, buf = obc.split_messages(buf + data)
                for m in msgs:
                    if m[0] == obc.MSG_APP_INFO:
                        try:
                            info = self._clients[writer] = obc.parse_app_info(m)
                            log.info("MyWhoosh connected (app %s v%s, supports: %s)", info.app_id,
                                     info.version, " ".join(f"{b:02x}" for b in sorted(info.buttons)) or "all")
                            writer.write(self._status)  # status after the handshake, not only before
                        except ValueError:
                            log.debug("bad app info %s", m.hex())
                    elif m[0] == 0x00:
                        # MyWhoosh pings every ~4 s; answer with the current status so it
                        # keeps believing a controller is present.
                        log.debug("ping from app, replying with status %s", self._status.hex())
                        writer.write(self._status)
        except (ConnectionError, OSError):
            pass
        finally:
            self._clients.pop(writer, None)
            writer.close()
            log.info("app disconnected (%s)", peer)

    def _send_all(self, data: bytes) -> int:
        n = 0
        for w in list(self._clients):
            try:
                if w.is_closing():
                    raise ConnectionError
                w.write(data)
                n += 1
            except (ConnectionError, OSError, RuntimeError):
                self._clients.pop(w, None)
        return n

    def broadcast(self, data: bytes) -> None:
        if not self._send_all(data):
            now = self.clock()
            if now - self._last_warn >= 30:
                self._last_warn = now
                log.warning("no app connected; dropping %s", data.hex())

    def set_status(self, battery: int | None, connected: bool) -> None:
        self._status = obc.encode_device_status(battery, connected)
        self._send_all(self._status)

    async def stop(self) -> None:
        try:
            await self.advertiser.unregister()
        except Exception:
            log.exception("mDNS unregister failed")
        for w in list(self._clients):
            w.close()
        self._clients.clear()
        if self._server:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), 1)
            except asyncio.TimeoutError:
                pass


class ObcSink:
    def __init__(self, server: ObcServer):
        self.server = server
        self._warned: set[int] = set()

    async def send(self, changes):
        for a, _ in changes:
            if a.obc not in self._warned and not self.server.supports(a.obc):
                self._warned.add(a.obc)
                log.warning("MyWhoosh may ignore %s", a.name)
        self.server.broadcast(obc.encode_button_state([(a.obc, d) for a, d in changes]))
        log.debug("-> %s", obc.encode_button_state([(a.obc, d) for a, d in changes]).hex())

    async def status(self, battery, connected):
        self.server.set_status(battery, connected)

    async def close(self):
        await self.server.stop()
