#!/usr/bin/env python3
"""Multi-server UDP relay manager for Cypress proxy mode.

This keeps a single public UDP relay port open while routing traffic to many
different Cypress dedicated servers by relay key. An HTTP API issues leases,
which the launcher can request via a Get Relay button.

Uses asyncio for both the UDP relay and HTTP API on a single event loop,
avoiding threading overhead and GIL contention on the hot path.

Example tmux usage:

    tmux new -s cypress-relay 'python3 relay.py --bind 0.0.0.0 --port 25200 --api-port 8080 --public-domain v0e.dev --relay-host relay.v0e.dev'

Requires:
    pip install aiohttp
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
import socket
import string
import struct
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from aiohttp import web


REGISTER_PREFIX = b"CYPRESS_PROXY_REGISTER|SERVER|"
REGISTER_PREFIX_LEN = len(REGISTER_PREFIX)
CLIENT_HEADER_PACK = struct.Struct("!4sH")  # 4-byte ip + 2-byte port
DEFAULT_PUBLIC_PREFIX = "cypress"
EXPIRY_INTERVAL = 10
SOCKET_BUF_SIZE = 2 * 1024 * 1024
CODE_LENGTH = 6
CODE_ALPHABET = string.ascii_uppercase + string.digits
RANDOM_WORDS = (
    "soldier", "allstar", "engineer", "scientist", "imp", "superbrainz", "deadbeard", "zomboss",
    "peashooter", "cactus", "sunflower", "chomper", "citron", "kernelcorn", "rose", "torchwood", "hovergoat",
)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "server"


@dataclass
class RelayLease:
    key: str
    code: str
    slug: str
    server_name: str
    relay_address: str
    display_host: str
    join_link: str
    signing_secret: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    game: str = "GW2"
    created_at: float = field(default_factory=time.time)
    server_addr: Optional[Tuple[str, int]] = None
    server_last_seen: float = 0.0
    client_last_seen: Dict[Tuple[str, int], float] = field(default_factory=dict)
    tunnel: Optional["TunnelState"] = None

    def to_payload(self) -> dict:
        return {
            "relayAddress": self.relay_address,
            "relayKey": self.key,
            "signingSecret": self.signing_secret.hex(),
            "code": self.code,
            "displayHost": self.display_host,
            "joinLink": self.join_link,
            "serverName": self.server_name,
            "slug": self.slug,
            "game": self.game,
            "serverRegistered": self.server_addr is not None,
        }


class LeaseStore:
    """all access from the asyncio event loop thread only, no locks needed."""

    def __init__(self, relay_address: str, relay_host: str, public_domain: str | None,
                 public_prefix: str, server_timeout: int, client_timeout: int, verbose: bool) -> None:
        self.relay_address = relay_address
        self.relay_host = relay_host
        self.public_domain = public_domain
        self.public_prefix = public_prefix
        self.server_timeout = server_timeout
        self.client_timeout = client_timeout
        self.verbose = verbose
        self.leases_by_key: Dict[str, RelayLease] = {}
        self.leases_by_code: Dict[str, str] = {}  # code -> key
        self.leases_by_server_addr: Dict[Tuple[str, int], str] = {}
        self._used_slugs: set[str] = set()

    def log(self, message: str) -> None:
        if self.verbose:
            print(message, flush=True)

    def _build_display_host(self, slug: str) -> str:
        if self.public_domain:
            return f"{self.public_prefix}.{slug}.{self.public_domain}" if self.public_prefix else f"{slug}.{self.public_domain}"
        return self.relay_host

    def _make_unique_slug(self, server_name: str) -> str:
        base_slug = slugify(server_name)
        slug = base_slug
        while slug in self._used_slugs:
            slug = f"{base_slug}-{secrets.choice(RANDOM_WORDS)}-{secrets.token_hex(2)}"
        self._used_slugs.add(slug)
        return slug

    def _make_unique_code(self) -> str:
        for _ in range(100):
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            if code not in self.leases_by_code:
                return code
        return secrets.token_hex(4).upper()

    def create_lease(self, server_name: str, game: str) -> RelayLease:
        slug = self._make_unique_slug(server_name)
        key = secrets.token_urlsafe(18)
        code = self._make_unique_code()
        display_host = self._build_display_host(slug)
        join_link = f"cypress://{display_host}?relay={self.relay_address}&key={key}"
        lease = RelayLease(
            key=key,
            code=code,
            slug=slug,
            server_name=server_name,
            relay_address=self.relay_address,
            display_host=display_host,
            join_link=join_link,
            game=game,
        )
        self.leases_by_key[key] = lease
        self.leases_by_code[code] = key
        self.log(f"created lease {code} ({key}) for {server_name} -> {display_host}")
        return lease

    def resolve_code(self, code: str) -> Optional[RelayLease]:
        key = self.leases_by_code.get(code.upper())
        return self.leases_by_key.get(key) if key else None

    def register_server(self, key: str, addr: Tuple[str, int], timestamp: str = "", signature: str = "") -> Optional[RelayLease]:
        lease = self.leases_by_key.get(key)
        if lease is None:
            return None
        # verify HMAC signature if provided (new protocol)
        if timestamp and signature:
            try:
                ts = float(timestamp)
                if abs(time.time() - ts) > 30:
                    self.log(f"register rejected: timestamp too old for {lease.server_name}")
                    return None
            except ValueError:
                return None
            expected = hmac.new(lease.signing_secret, (key + timestamp).encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                self.log(f"register rejected: bad signature for {lease.server_name}")
                return None
        if lease.server_addr and lease.server_addr != addr:
            self.leases_by_server_addr.pop(lease.server_addr, None)
        lease.server_addr = addr
        lease.server_last_seen = time.monotonic()
        self.leases_by_server_addr[addr] = key
        return lease

    def expire_old_entries(self) -> None:
        now = time.monotonic()
        # snapshot the values list so we don't mutate during iteration
        for lease in list(self.leases_by_key.values()):
            if lease.server_addr and now - lease.server_last_seen > self.server_timeout:
                self.log(f"server expired: {lease.server_name} ({lease.key})")
                self.leases_by_server_addr.pop(lease.server_addr, None)
                lease.server_addr = None
                lease.server_last_seen = 0.0

            stale = [a for a, t in lease.client_last_seen.items() if now - t > self.client_timeout]
            for a in stale:
                del lease.client_last_seen[a]

    def list_leases(self) -> list[dict]:
        return [lease.to_payload() for lease in self.leases_by_key.values()]


class RelayProtocol(asyncio.DatagramProtocol):
    """hot path - all packet routing happens here with zero locks."""

    def __init__(self, store: LeaseStore, verbose: bool) -> None:
        self.store = store
        self.verbose = verbose
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self.transport = transport
        # bump socket buffers for burst traffic
        sock = transport.get_extra_info("socket")
        if sock:
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUF_SIZE)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_BUF_SIZE)
            except OSError:
                pass

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        if not data:
            return

        if data[:REGISTER_PREFIX_LEN] == REGISTER_PREFIX:
            payload = data[REGISTER_PREFIX_LEN:].decode("utf-8", errors="ignore").strip()
            parts = payload.split("|")
            if len(parts) == 3:
                key, ts, sig = parts
                lease = self.store.register_server(key, addr, timestamp=ts, signature=sig)
            else:
                if self.verbose:
                    print(f"dropping unsigned registration from {addr[0]}:{addr[1]}", flush=True)
                return
            if lease is None:
                if self.verbose:
                    print(f"dropping registration from {addr[0]}:{addr[1]} - unknown key", flush=True)
                return
            if self.verbose:
                print(f"server registered: {lease.server_name} @ {addr[0]}:{addr[1]}", flush=True)
            self.transport.sendto(b"CYPRESS_PROXY_ACK", addr)
            return

        store = self.store

        # known server -> forward to client
        key = store.leases_by_server_addr.get(addr)
        if key is not None:
            lease = store.leases_by_key.get(key)
            if lease is None:
                return
            lease.server_last_seen = time.monotonic()
            if len(data) < 6:
                return
            ip_bytes, port = CLIENT_HEADER_PACK.unpack_from(data)
            target = (socket.inet_ntoa(ip_bytes), port)
            lease.client_last_seen[target] = time.monotonic()
            self.transport.sendto(data[6:], target)
            return

        # client -> forward to server
        key_len = data[0]
        if len(data) < key_len + 1:
            return
        relay_key = data[1:key_len + 1].decode("utf-8", errors="ignore")
        lease = store.leases_by_key.get(relay_key)
        if lease is None or lease.server_addr is None:
            return
        lease.client_last_seen[addr] = time.monotonic()
        header = CLIENT_HEADER_PACK.pack(socket.inet_aton(addr[0]), addr[1])
        self.transport.sendto(header + data[key_len + 1:], lease.server_addr)

    def error_received(self, exc: Exception) -> None:
        pass


async def expiry_loop(store: LeaseStore) -> None:
    while True:
        await asyncio.sleep(EXPIRY_INTERVAL)
        store.expire_old_entries()


# tcp side-channel tunnel
# server registers outbound, relay multiplexes client connections through it
# binary frame: [1B cmd][4B client_id BE][4B data_len BE][data]
# cmd: 1=OPEN, 2=DATA, 3=CLOSE

CMD_OPEN = 1
CMD_DATA = 2
CMD_CLOSE = 3
FRAME_HDR = struct.Struct("!BII")
SIDE_CHANNEL_PORT = 14638
TCP_RELAY_BUF = 8192


@dataclass
class TunnelState:
    """per-server tunnel state, lives on the lease while server is connected."""
    writer: asyncio.StreamWriter
    clients: Dict[int, asyncio.StreamWriter] = field(default_factory=dict)
    next_id: int = 1
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


async def _write_frame(writer: asyncio.StreamWriter, lock: asyncio.Lock,
                       cmd: int, client_id: int, data: bytes = b"") -> None:
    hdr = FRAME_HDR.pack(cmd, client_id, len(data))
    async with lock:
        writer.write(hdr + data)
        await writer.drain()


async def _read_frame(reader: asyncio.StreamReader) -> Tuple[int, int, bytes]:
    hdr = await reader.readexactly(FRAME_HDR.size)
    cmd, client_id, data_len = FRAME_HDR.unpack(hdr)
    data = await reader.readexactly(data_len) if data_len > 0 else b""
    return cmd, client_id, data


async def _tunnel_dispatcher(reader: asyncio.StreamReader, tunnel: TunnelState,
                             verbose: bool) -> None:
    """read frames from server tunnel and route to clients."""
    try:
        while True:
            cmd, cid, data = await _read_frame(reader)
            if cmd == CMD_DATA:
                cw = tunnel.clients.get(cid)
                if cw:
                    cw.write(data)
                    await cw.drain()
            elif cmd == CMD_CLOSE:
                cw = tunnel.clients.pop(cid, None)
                if cw:
                    try:
                        cw.close()
                    except Exception:
                        pass
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError, OSError):
        pass


async def _client_to_tunnel(reader: asyncio.StreamReader, tunnel: TunnelState,
                            client_id: int) -> None:
    """read raw data from client, frame and send to server tunnel."""
    try:
        while True:
            data = await reader.read(TCP_RELAY_BUF)
            if not data:
                break
            await _write_frame(tunnel.writer, tunnel.lock, CMD_DATA, client_id, data)
    except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError, OSError):
        pass


async def handle_tcp_conn(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                          store: LeaseStore, verbose: bool) -> None:
    """handle incoming TCP on side-channel port - server register or client relay."""
    peer = writer.get_extra_info("peername")
    try:
        raw = await asyncio.wait_for(reader.readline(), timeout=5.0)
        if not raw:
            writer.close()
            return

        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            if verbose:
                print(f"tcp tunnel: bad handshake from {peer}", flush=True)
            writer.close()
            return

        msg_type = msg.get("type")
        key = msg.get("key", "")
        lease = store.leases_by_key.get(key)

        if msg_type == "register":
            # server registering its side-channel tunnel
            if not lease:
                if verbose:
                    print(f"tcp tunnel: unknown key from server {peer}", flush=True)
                writer.close()
                return

            if verbose:
                print(f"tcp tunnel: server registered for '{lease.server_name}' from {peer}", flush=True)

            tunnel = TunnelState(writer=writer)
            lease.tunnel = tunnel

            try:
                await _tunnel_dispatcher(reader, tunnel, verbose)
            finally:
                lease.tunnel = None
                for cw in tunnel.clients.values():
                    try:
                        cw.close()
                    except Exception:
                        pass
                tunnel.clients.clear()
                if verbose:
                    print(f"tcp tunnel: server disconnected for '{lease.server_name}'", flush=True)

        elif msg_type == "relay":
            # client wanting side-channel through tunnel
            if not lease or not lease.tunnel:
                if verbose:
                    reason = "no tunnel" if lease else "unknown key"
                    print(f"tcp relay: client {peer} rejected ({reason})", flush=True)
                writer.close()
                return

            tunnel = lease.tunnel
            client_id = tunnel.next_id
            tunnel.next_id += 1
            tunnel.clients[client_id] = writer

            if verbose:
                print(f"tcp relay: client {peer} -> cid={client_id} ({lease.server_name})", flush=True)

            await _write_frame(tunnel.writer, tunnel.lock, CMD_OPEN, client_id)

            try:
                await _client_to_tunnel(reader, tunnel, client_id)
            finally:
                tunnel.clients.pop(client_id, None)
                try:
                    await _write_frame(tunnel.writer, tunnel.lock, CMD_CLOSE, client_id)
                except Exception:
                    pass
                if verbose:
                    print(f"tcp relay: client cid={client_id} disconnected", flush=True)

        elif msg_type == "serverInfo":
            # browser ping - require key to identify which server to query
            key = msg.get("key", "")
            if not key:
                if verbose:
                    print(f"tcp tunnel: serverInfo from {peer} rejected (no key)", flush=True)
                writer.close()
                return

            target_lease = store.leases_by_key.get(key)
            if not target_lease or not target_lease.tunnel:
                if verbose:
                    print(f"tcp tunnel: serverInfo from {peer} but no tunnel available", flush=True)
                writer.close()
                return

            tunnel = target_lease.tunnel
            client_id = tunnel.next_id
            tunnel.next_id += 1
            tunnel.clients[client_id] = writer

            if verbose:
                print(f"tcp tunnel: serverInfo {peer} -> cid={client_id} ({target_lease.server_name})", flush=True)

            await _write_frame(tunnel.writer, tunnel.lock, CMD_OPEN, client_id)
            # forward the original serverInfo line to the real server
            await _write_frame(tunnel.writer, tunnel.lock, CMD_DATA, client_id, raw)

            try:
                await _client_to_tunnel(reader, tunnel, client_id)
            finally:
                tunnel.clients.pop(client_id, None)
                try:
                    await _write_frame(tunnel.writer, tunnel.lock, CMD_CLOSE, client_id)
                except Exception:
                    pass

        else:
            if verbose:
                print(f"tcp tunnel: unknown type '{msg_type}' from {peer}", flush=True)
            writer.close()

    except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError, OSError) as e:
        if verbose:
            print(f"tcp tunnel: error from {peer}: {e}", flush=True)
    finally:
        try:
            writer.close()
        except Exception:
            pass


# http api using aiohttp on the same event loop
async def api_get_relays(request: web.Request) -> web.Response:
    store: LeaseStore = request.app["store"]
    return web.json_response({"leases": store.list_leases()})

async def api_create_relay(request: web.Request) -> web.Response:
    store: LeaseStore = request.app["store"]
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)
    server_name = str(payload.get("serverName") or "Cypress Server").strip()
    game = str(payload.get("game") or "GW2").strip()
    lease = store.create_lease(server_name, game)
    return web.json_response(lease.to_payload(), status=201)

async def api_resolve_code(request: web.Request) -> web.Response:
    store: LeaseStore = request.app["store"]
    code = request.match_info.get("code", "").strip().upper()
    if not code:
        return web.json_response({"error": "missing code"}, status=400)
    lease = store.resolve_code(code)
    if lease is None:
        return web.json_response({"error": "unknown code"}, status=404)
    return web.json_response(lease.to_payload())


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a multi-server UDP relay manager for Cypress proxy mode.")
    parser.add_argument("--bind", default="0.0.0.0", help="UDP bind address. Default: 0.0.0.0")
    parser.add_argument("--port", default=25200, type=int, help="UDP relay port. Default: 25200")
    parser.add_argument("--api-bind", default="0.0.0.0", help="HTTP API bind address. Default: 0.0.0.0")
    parser.add_argument("--api-port", default=8080, type=int, help="HTTP API port. Default: 8080")
    parser.add_argument("--relay-host", default="relay.local", help="Public host clients should use for the relay. Default: relay.local")
    parser.add_argument("--public-domain", default="", help="Optional vanity domain suffix, for example v0e.dev")
    parser.add_argument("--public-prefix", default=DEFAULT_PUBLIC_PREFIX, help="Prefix for vanity hosts. Default: cypress")
    parser.add_argument("--client-timeout", default=180, type=int, help="Seconds to keep idle client mappings. Default: 180")
    parser.add_argument("--server-timeout", default=90, type=int, help="Seconds before a server registration expires. Default: 90")
    parser.add_argument("--quiet", action="store_true", help="Reduce per-event logging.")
    return parser.parse_args(argv)


async def run(argv: list[str]) -> None:
    args = parse_args(argv)
    relay_address = f"{args.relay_host}:{args.port}"
    verbose = not args.quiet
    store = LeaseStore(
        relay_address=relay_address,
        relay_host=args.relay_host,
        public_domain=args.public_domain.strip() or None,
        public_prefix=args.public_prefix.strip(),
        server_timeout=args.server_timeout,
        client_timeout=args.client_timeout,
            verbose=verbose,
    )

    loop = asyncio.get_running_loop()

    transport, _ = await loop.create_datagram_endpoint(
        lambda: RelayProtocol(store, verbose),
        local_addr=(args.bind, args.port),
    )
    print(f"relay listening on udp://{args.bind}:{args.port}", flush=True)

    tcp_server = await asyncio.start_server(
        lambda r, w: handle_tcp_conn(r, w, store, verbose),
        args.bind, SIDE_CHANNEL_PORT,
    )
    print(f"tcp side-channel relay listening on tcp://{args.bind}:{SIDE_CHANNEL_PORT}", flush=True)

    logging.getLogger("aiohttp.server").setLevel(logging.CRITICAL)

    app = web.Application()
    app["store"] = store
    app.router.add_get("/api/relays", api_get_relays)
    app.router.add_post("/api/relays", api_create_relay)
    app.router.add_get("/api/relays/{code}", api_resolve_code)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, args.api_bind, args.api_port)
    await site.start()
    print(f"relay API listening on http://{args.api_bind}:{args.api_port}/api/relays", flush=True)

    asyncio.create_task(expiry_loop(store))

    try:
        await asyncio.Event().wait()
    finally:
        transport.close()
        tcp_server.close()
        await runner.cleanup()


def main(argv: list[str]) -> int:
    try:
        asyncio.run(run(argv))
    except KeyboardInterrupt:
        print("stopping relay", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))