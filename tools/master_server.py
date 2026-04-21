#!/usr/bin/env python3
"""Cypress Master Server - async server browser registry + global moderation.

Built on aiohttp to handle thousands of concurrent clients.
Servers POST heartbeats with auth tokens; clients GET a lite server list
and fetch icons individually on demand.

Install:
    pip install aiohttp bcrypt aiosqlite

Usage:
    python master_server.py --bind 0.0.0.0 --port 27900

Endpoints:
    POST /heartbeat          - Register/update a server (returns auth token on first call)
    POST /deregister         - Remove a server (requires auth token)
    GET  /servers            - Lite server list (no icons, cached)
    GET  /icon?key=ip:port   - Fetch a single server's icon
    GET  /health             - Health check

    POST /mod/register       - Register a global moderator (requires secret)
    POST /mod/login          - Login as global moderator (returns session token)
    GET  /mod/me             - Get current moderator info (requires token)
    POST /mod/logout         - Invalidate session token

    POST /mod/global-ban     - Add a global ban (mod only)
    POST /mod/global-unban   - Remove a global ban (mod only)
    GET  /mod/global-bans    - List all global bans (mod only)

    POST /bans/check         - Check if a player is globally banned (server token required)

    POST /mod/ban-server     - Ban a server from browser (mod only)
    POST /mod/unban-server   - Unban a server from browser (mod only)
    GET  /mod/banned-servers  - List banned servers (mod only)
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import aiosqlite
import bcrypt
from aiohttp import web

STALE_TIMEOUT = 90
CLEANUP_INTERVAL = 30
MAX_SERVERS = 1000
MAX_BODY_SIZE = 64 * 1024
ICON_MAX_B64 = 15_000       # ~11KB decoded (128x128 JPEG @ q60 is ~3-5KB)
LIST_CACHE_TTL = 5

RATE_HEARTBEAT = (6, 60)
RATE_SERVERS = (10, 10)
RATE_ICON = (60, 60)
RATE_MOD_LOGIN = (25, 900)   # 25 attempts per 15 min window before blacklist
RATE_BAN_CHECK = (120, 60)   # 120 ban checks per minute per server ip

MOD_TOKEN_EXPIRY = 86400
BLACKLIST_DURATION = 3600    # 1 hour auto-expire for ip blacklist
SECRET_FILE = "moderator_secret.txt"
DB_FILE = "cypress_master.db"

REQUIRED_FIELDS = {"address", "port", "game"}
ALLOWED_GAMES = {"GW1", "GW2", "BFN"}

PRIVATE_IP_RE = re.compile(
    r"^(127\.|10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)"
)

class RateLimiter:

    def __init__(self):
        self._buckets: Dict[Tuple[str, str], list] = defaultdict(list)

    def check(self, ip: str, scope: str, max_req: int, per_sec: float) -> bool:
        key = (ip, scope)
        now = time.monotonic()
        bucket = self._buckets[key]
        cutoff = now - per_sec
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)
        if len(bucket) >= max_req:
            return False
        bucket.append(now)
        return True

    def cleanup(self):
        now = time.monotonic()
        stale = [k for k, v in self._buckets.items() if not v or v[-1] < now - 120]
        for k in stale:
            del self._buckets[k]

    async def persist(self, db: "aiosqlite.Connection"):
        \"\"\"save hot IPs to db so rate limits survive restarts for repeat offenders\"\"\"
        now = time.monotonic()
        hot = []
        for (ip, scope), bucket in self._buckets.items():
            if scope == "heartbeat" and len(bucket) >= 3:
                hot.append((ip, scope, len(bucket), now))
        if hot:
            await db.executemany(
                "INSERT OR REPLACE INTO rate_limit_persist (ip, scope, hit_count, saved_at) VALUES (?, ?, ?, ?)",
                hot
            )
            await db.commit()

    async def restore(self, db: "aiosqlite.Connection"):
        \"\"\"reload persisted rate limit state on startup\"\"\"
        try:
            async with db.execute(
                "SELECT ip, scope, hit_count, saved_at FROM rate_limit_persist WHERE saved_at > ?",
                (time.time() - 300,)  # only restore entries less than 5 min old
            ) as cursor:
                rows = await cursor.fetchall()
                now = time.monotonic()
                for ip, scope, count, _ in rows:
                    key = (ip, scope)
                    self._buckets[key] = [now] * min(count, 10)  # cap restored hits
            # clear old persisted data
            await db.execute("DELETE FROM rate_limit_persist WHERE saved_at < ?", (time.time() - 300,))
            await db.commit()
        except Exception:
            pass  # table might not exist yet on first run
            del self._buckets[k]


@dataclass
class ServerEntry:
    address: str
    port: int
    game: str
    token: str
    players: int = 0
    maxPlayers: int = 24
    motd: str = ""
    icon: str = ""
    modded: bool = False
    modpackUrl: str = ""
    level: str = ""
    mode: str = ""
    relayAddress: str = ""
    relayKey: str = ""
    relayCode: str = ""
    hasPassword: bool = False
    gamePort: int = 0
    lastHeartbeat: float = field(default_factory=time.time)

    @property
    def key(self) -> str:
        return f"{self.address}:{self.port}"

    def to_lite(self) -> dict:
        d: dict = {
            "address": self.address,
            "port": self.port,
            "game": self.game,
            "players": self.players,
            "maxPlayers": self.maxPlayers,
        }
        if self.motd:
            d["motd"] = self.motd
        if self.modded:
            d["modded"] = True
        if self.modpackUrl:
            d["modpackUrl"] = self.modpackUrl
        if self.level:
            d["level"] = self.level
        if self.mode:
            d["mode"] = self.mode
        if self.icon:
            d["hasIcon"] = True
        if self.relayAddress:
            d["relayAddress"] = self.relayAddress
        if self.relayKey:
            d["relayKey"] = self.relayKey
        if self.relayCode:
            d["relayCode"] = self.relayCode
        if self.hasPassword:
            d["hasPassword"] = True
        if self.gamePort > 0:
            d["gamePort"] = self.gamePort
        return d


class MasterState:
    def __init__(self):
        self.servers: Dict[str, ServerEntry] = {}
        self.rate_limiter = RateLimiter()
        self._list_cache: Optional[bytes] = None
        self._list_cache_time: float = 0
        self.db: Optional[aiosqlite.Connection] = None
        self._banned_server_ips: set[str] = set()  # cached for fast filtering

    def get_cached_list(self) -> bytes:
        now = time.time()
        if self._list_cache is not None and (now - self._list_cache_time) < LIST_CACHE_TTL:
            return self._list_cache
        server_list = [
            v.to_lite() for v in self.servers.values()
            if v.address not in self._banned_server_ips
        ]
        self._list_cache = json.dumps({"servers": server_list}, separators=(",", ":")).encode()
        self._list_cache_time = now
        return self._list_cache

    def invalidate_cache(self):
        self._list_cache = None


# set via --behind-proxy cli flag
_trust_proxy_headers = False


def get_real_ip(request: web.Request) -> str:
    if _trust_proxy_headers:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
    peername = request.transport.get_extra_info("peername")
    return peername[0] if peername else "0.0.0.0"


def is_private_ip(ip: str) -> bool:
    return bool(PRIVATE_IP_RE.match(ip))


def json_response(data: object, status: int = 200) -> web.Response:
    return web.Response(
        body=json.dumps(data, separators=(",", ":")).encode(),
        status=status,
        content_type="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )


def error_response(status: int, msg: str) -> web.Response:
    return json_response({"error": msg}, status)


def get_or_create_secret() -> str:
    """generate mod registration secret on first run, load it on subsequent runs"""
    if os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, "r") as f:
            return f.read().strip()
    secret = secrets.token_urlsafe(48)
    with open(SECRET_FILE, "w") as f:
        f.write(secret)
    print(f"[!] Generated moderator secret. Keep this safe: {secret}")
    print(f"[!] Saved to {SECRET_FILE}")
    return secret


async def init_db(state: MasterState):
    state.db = await aiosqlite.connect(DB_FILE)
    state.db.row_factory = aiosqlite.Row
    await state.db.executescript("""
        CREATE TABLE IF NOT EXISTS moderators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mod_sessions (
            token TEXT PRIMARY KEY,
            mod_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            FOREIGN KEY (mod_id) REFERENCES moderators(id)
        );
        CREATE TABLE IF NOT EXISTS global_bans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hwid TEXT NOT NULL,
            components TEXT NOT NULL DEFAULT '[]',
            reason TEXT NOT NULL DEFAULT '',
            banned_by TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS banned_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT UNIQUE NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            banned_by TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ip_blacklist (
            ip TEXT PRIMARY KEY,
            fail_count INTEGER NOT NULL DEFAULT 0,
            first_fail_at REAL NOT NULL,
            blacklisted_at REAL
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            mod_username TEXT,
            details TEXT,
            ip TEXT,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rate_limit_persist (
            ip TEXT NOT NULL,
            scope TEXT NOT NULL,
            hit_count INTEGER NOT NULL,
            saved_at REAL NOT NULL,
            PRIMARY KEY (ip, scope)
        );
    """)
    await state.db.commit()

    async with state.db.execute("SELECT ip FROM banned_servers") as cursor:
        rows = await cursor.fetchall()
        state._banned_server_ips = {row[0] for row in rows}

    await state.rate_limiter.restore(state.db)


async def audit_log(state: MasterState, action: str, mod_username: str = "", details: str = "", ip: str = ""):
    await state.db.execute(
        "INSERT INTO audit_log (action, mod_username, details, ip, created_at) VALUES (?, ?, ?, ?, ?)",
        (action, mod_username, details, ip, time.time())
    )
    await state.db.commit()


async def check_ip_blacklisted(state: MasterState, ip: str) -> bool:
    """check if ip is blacklisted, auto-expire old entries"""
    async with state.db.execute("SELECT blacklisted_at FROM ip_blacklist WHERE ip = ?", (ip,)) as cursor:
        row = await cursor.fetchone()
        if not row or not row[0]:
            return False
        if time.time() - row[0] > BLACKLIST_DURATION:
            await state.db.execute("DELETE FROM ip_blacklist WHERE ip = ?", (ip,))
            await state.db.commit()
            return False
        return True


async def record_login_failure(state: MasterState, ip: str):
    """track failed login attempts, blacklist if threshold exceeded"""
    now = time.time()
    async with state.db.execute("SELECT fail_count, first_fail_at FROM ip_blacklist WHERE ip = ?", (ip,)) as cursor:
        row = await cursor.fetchone()

    if row:
        fail_count = row[0]
        first_fail = row[1]
        # sliding window - reset if outside 15min
        if now - first_fail > 900:
            await state.db.execute(
                "UPDATE ip_blacklist SET fail_count = 1, first_fail_at = ?, blacklisted_at = NULL WHERE ip = ?",
                (now, ip)
            )
        else:
            fail_count += 1
            if fail_count >= 25:
                await state.db.execute(
                    "UPDATE ip_blacklist SET fail_count = ?, blacklisted_at = ? WHERE ip = ?",
                    (fail_count, now, ip)
                )
                await audit_log(state, "ip_blacklisted", details=f"too many login failures", ip=ip)
            else:
                await state.db.execute(
                    "UPDATE ip_blacklist SET fail_count = ? WHERE ip = ?",
                    (fail_count, ip)
                )
    else:
        await state.db.execute(
            "INSERT INTO ip_blacklist (ip, fail_count, first_fail_at) VALUES (?, 1, ?)",
            (ip, now)
        )
    await state.db.commit()


async def validate_mod_token(state: MasterState, request: web.Request) -> Optional[dict]:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    now = time.time()
    async with state.db.execute(
        "SELECT s.mod_id, s.expires_at, m.username FROM mod_sessions s JOIN moderators m ON s.mod_id = m.id WHERE s.token = ?",
        (token,)
    ) as cursor:
        row = await cursor.fetchone()
    if not row or row[1] < now:
        return None
    return {"mod_id": row[0], "username": row[2], "token": token}


async def handle_servers(request: web.Request) -> web.Response:
    state: MasterState = request.app["state"]
    ip = get_real_ip(request)
    if not state.rate_limiter.check(ip, "servers", *RATE_SERVERS):
        return error_response(429, "Rate limited")

    body = state.get_cached_list()
    return web.Response(
        body=body,
        status=200,
        content_type="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )


async def handle_icon(request: web.Request) -> web.Response:
    state: MasterState = request.app["state"]
    ip = get_real_ip(request)
    if not state.rate_limiter.check(ip, "icon", *RATE_ICON):
        return error_response(429, "Rate limited")

    key = request.query.get("key", "")
    entry = state.servers.get(key)
    if not entry or not entry.icon:
        return error_response(404, "No icon")

    return json_response({"icon": entry.icon})


async def handle_health(request: web.Request) -> web.Response:
    state: MasterState = request.app["state"]
    return json_response({"status": "ok", "servers": len(state.servers)})


async def handle_heartbeat(request: web.Request) -> web.Response:
    state: MasterState = request.app["state"]
    ip = get_real_ip(request)
    if not state.rate_limiter.check(ip, "heartbeat", *RATE_HEARTBEAT):
        return error_response(429, "Rate limited")

    try:
        data = await request.json()
    except Exception:
        return error_response(400, "Invalid JSON")

    if not isinstance(data, dict):
        return error_response(400, "Expected object")

    missing = REQUIRED_FIELDS - set(data.keys())
    if missing:
        return error_response(400, f"Missing: {', '.join(missing)}")

    if data["game"] not in ALLOWED_GAMES:
        return error_response(400, f"Invalid game: {data['game']}")

    try:
        port = int(data["port"])
    except (ValueError, TypeError):
        return error_response(400, "Invalid port")

    addr = str(data.get("address", "")).strip()
    if not addr or is_private_ip(addr):
        addr = ip

    key = f"{addr}:{port}"
    existing = state.servers.get(key)

    if existing:
        token = data.get("token", "")
        if token != existing.token:
            return error_response(403, "Invalid token")

        existing.players = int(data.get("players", existing.players))
        existing.maxPlayers = int(data.get("maxPlayers", existing.maxPlayers))
        existing.motd = str(data.get("motd", existing.motd))[:256]
        existing.modded = bool(data.get("modded", existing.modded))
        existing.modpackUrl = str(data.get("modpackUrl", existing.modpackUrl))[:512]
        existing.level = str(data.get("level", existing.level))[:128]
        existing.mode = str(data.get("mode", existing.mode))[:128]
        existing.relayAddress = str(data.get("relayAddress", existing.relayAddress))[:256]
        existing.relayKey = str(data.get("relayKey", existing.relayKey))[:256]
        existing.relayCode = str(data.get("relayCode", existing.relayCode))[:16]
        existing.hasPassword = bool(data.get("hasPassword", existing.hasPassword))
        existing.gamePort = int(data.get("gamePort", existing.gamePort))
        existing.lastHeartbeat = time.time()

        if "icon" in data:
            existing.icon = str(data["icon"])[:ICON_MAX_B64]

        state.invalidate_cache()
        return json_response({"ok": True, "key": key, "token": existing.token})
    else:
        if len(state.servers) >= MAX_SERVERS:
            return error_response(503, "Server list full")

        token = secrets.token_urlsafe(32)
        entry = ServerEntry(
            address=addr,
            port=port,
            game=data["game"],
            token=token,
            players=int(data.get("players", 0)),
            maxPlayers=int(data.get("maxPlayers", 24)),
            motd=str(data.get("motd", ""))[:256],
            icon=str(data.get("icon", ""))[:ICON_MAX_B64],
            modded=bool(data.get("modded", False)),
            modpackUrl=str(data.get("modpackUrl", ""))[:512],
            level=str(data.get("level", ""))[:128],
            mode=str(data.get("mode", ""))[:128],
            relayAddress=str(data.get("relayAddress", ""))[:256],
            relayKey=str(data.get("relayKey", ""))[:256],
            relayCode=str(data.get("relayCode", ""))[:16],
            hasPassword=bool(data.get("hasPassword", False)),
            gamePort=int(data.get("gamePort", 0)),
        )
        state.servers[key] = entry
        state.invalidate_cache()
        print(f"[+] New server: {key} ({entry.game})")
        return json_response({"ok": True, "key": key, "token": token})


async def handle_deregister(request: web.Request) -> web.Response:
    state: MasterState = request.app["state"]
    ip = get_real_ip(request)

    try:
        data = await request.json()
    except Exception:
        return error_response(400, "Invalid JSON")

    addr = str(data.get("address", "")).strip()
    port = int(data.get("port", 0))
    token = data.get("token", "")
    if not addr or is_private_ip(addr):
        addr = ip
    key = f"{addr}:{port}"

    existing = state.servers.get(key)
    if not existing:
        return json_response({"ok": True})

    if token != existing.token:
        return error_response(403, "Invalid token")

    del state.servers[key]
    state.invalidate_cache()
    print(f"[-] Deregistered: {key}")
    return json_response({"ok": True})


async def handle_options(request: web.Request) -> web.Response:
    return web.Response(
        status=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        },
    )


async def handle_mod_register(request: web.Request) -> web.Response:
    state: MasterState = request.app["state"]
    ip = get_real_ip(request)

    if await check_ip_blacklisted(state, ip):
        return error_response(403, "IP blacklisted")

    try:
        data = await request.json()
    except Exception:
        return error_response(400, "Invalid JSON")

    username = str(data.get("username", "")).strip().lower()
    password = str(data.get("password", ""))
    secret = str(data.get("secret", ""))

    if not username or not password:
        return error_response(400, "Username and password required")
    if len(username) < 3 or len(username) > 32:
        return error_response(400, "Username must be 3-32 characters")
    if len(password) < 8:
        return error_response(400, "Password must be at least 8 characters")
    if not re.match(r'^[a-z0-9_]+$', username):
        return error_response(400, "Username must be alphanumeric/underscores only")

    expected_hash = hashlib.sha256(request.app["mod_secret"].encode()).hexdigest()
    given_hash = hashlib.sha256(secret.encode()).hexdigest()
    if not secrets.compare_digest(expected_hash, given_hash):
        await record_login_failure(state, ip)
        return error_response(403, "Invalid secret")

    async with state.db.execute("SELECT id FROM moderators WHERE username = ?", (username,)) as cursor:
        if await cursor.fetchone():
            return error_response(409, "Username already taken")

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    await state.db.execute(
        "INSERT INTO moderators (username, password_hash, created_at) VALUES (?, ?, ?)",
        (username, password_hash, time.time())
    )
    await state.db.commit()
    await audit_log(state, "mod_register", username, ip=ip)
    print(f"[mod] Registered new moderator: {username}")
    return json_response({"ok": True})


async def handle_mod_login(request: web.Request) -> web.Response:
    state: MasterState = request.app["state"]
    ip = get_real_ip(request)

    if await check_ip_blacklisted(state, ip):
        return error_response(403, "IP blacklisted")

    try:
        data = await request.json()
    except Exception:
        return error_response(400, "Invalid JSON")

    username = str(data.get("username", "")).strip().lower()
    password = str(data.get("password", ""))

    if not username or not password:
        return error_response(400, "Username and password required")

    async with state.db.execute(
        "SELECT id, password_hash FROM moderators WHERE username = ?", (username,)
    ) as cursor:
        row = await cursor.fetchone()

    if not row or not bcrypt.checkpw(password.encode(), row[1].encode()):
        await record_login_failure(state, ip)
        return error_response(401, "Invalid credentials")

    token = secrets.token_urlsafe(48)
    now = time.time()
    await state.db.execute(
        "INSERT INTO mod_sessions (token, mod_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, row[0], now, now + MOD_TOKEN_EXPIRY)
    )
    await state.db.commit()
    await audit_log(state, "mod_login", username, ip=ip)
    return json_response({"ok": True, "token": token, "username": username, "expires_at": now + MOD_TOKEN_EXPIRY})


async def handle_mod_me(request: web.Request) -> web.Response:
    state: MasterState = request.app["state"]
    mod = await validate_mod_token(state, request)
    if not mod:
        return error_response(401, "Not authenticated")
    return json_response({"ok": True, "username": mod["username"]})


async def handle_mod_verify(request: web.Request) -> web.Response:
    """challenge-response mod verification for game servers.
    server sends nonce to client, client HMACs it with their token,
    server forwards nonce+sig here. we check all active sessions."""
    state: MasterState = request.app["state"]
    nonce = request.query.get("nonce", "")
    sig = request.query.get("sig", "")
    if not nonce or not sig:
        return json_response({"ok": False})
    now = time.time()
    async with state.db.execute(
        "SELECT s.token, m.username FROM mod_sessions s JOIN moderators m ON s.mod_id = m.id WHERE s.expires_at > ?",
        (now,)
    ) as cursor:
        rows = await cursor.fetchall()
    for token, username in rows:
        expected = hmac.new(token.encode(), nonce.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, sig):
            return json_response({"ok": True, "username": username})
    return json_response({"ok": False})


async def handle_mod_logout(request: web.Request) -> web.Response:
    state: MasterState = request.app["state"]
    mod = await validate_mod_token(state, request)
    if not mod:
        return error_response(401, "Not authenticated")
    await state.db.execute("DELETE FROM mod_sessions WHERE token = ?", (mod["token"],))
    await state.db.commit()
    return json_response({"ok": True})


async def handle_global_ban(request: web.Request) -> web.Response:
    state: MasterState = request.app["state"]
    ip = get_real_ip(request)
    mod = await validate_mod_token(state, request)
    if not mod:
        return error_response(401, "Not authenticated")

    try:
        data = await request.json()
    except Exception:
        return error_response(400, "Invalid JSON")

    hwid = str(data.get("hwid", "")).strip()
    components = data.get("components", [])
    reason = str(data.get("reason", ""))[:512]

    if not isinstance(components, list):
        components = []
    components = [str(c)[:128] for c in components[:64]]

    # if no explicit hwid, promote first component to hwid so the db constraint is satisfied
    if not hwid and components:
        hwid = components[0]

    if not hwid:
        return error_response(400, "HWID or at least one component required")

    await state.db.execute(
        "INSERT INTO global_bans (hwid, components, reason, banned_by, created_at) VALUES (?, ?, ?, ?, ?)",
        (hwid, json.dumps(components), reason, mod["username"], time.time())
    )
    await state.db.commit()
    await audit_log(state, "global_ban", mod["username"], f"hwid={hwid[:16]}... reason={reason}", ip)
    print(f"[mod] {mod['username']} globally banned HWID {hwid[:16]}...")
    return json_response({"ok": True})


async def handle_global_unban(request: web.Request) -> web.Response:
    state: MasterState = request.app["state"]
    ip = get_real_ip(request)
    mod = await validate_mod_token(state, request)
    if not mod:
        return error_response(401, "Not authenticated")

    try:
        data = await request.json()
    except Exception:
        return error_response(400, "Invalid JSON")

    ban_id = data.get("id")
    if ban_id is None:
        return error_response(400, "Ban ID required")

    await state.db.execute("DELETE FROM global_bans WHERE id = ?", (int(ban_id),))
    await state.db.commit()
    await audit_log(state, "global_unban", mod["username"], f"ban_id={ban_id}", ip)
    return json_response({"ok": True})


async def handle_global_bans_list(request: web.Request) -> web.Response:
    state: MasterState = request.app["state"]
    mod = await validate_mod_token(state, request)
    if not mod:
        return error_response(401, "Not authenticated")

    bans = []
    async with state.db.execute("SELECT id, hwid, components, reason, banned_by, created_at FROM global_bans ORDER BY created_at DESC") as cursor:
        async for row in cursor:
            bans.append({
                "id": row[0], "hwid": row[1],
                "components": json.loads(row[2]),
                "reason": row[3], "banned_by": row[4],
                "created_at": row[5]
            })
    return json_response({"ok": True, "bans": bans})


async def handle_ban_check(request: web.Request) -> web.Response:
    state: MasterState = request.app["state"]
    ip = get_real_ip(request)

    if not state.rate_limiter.check(ip, "ban_check", *RATE_BAN_CHECK):
        return error_response(429, "Rate limited")

    try:
        data = await request.json()
    except Exception:
        return error_response(400, "Invalid JSON")

    hwid = str(data.get("hwid", "")).strip()
    components = data.get("components", [])

    if not hwid and not components:
        return error_response(400, "HWID or components required")

    if not isinstance(components, list):
        components = []

    async with state.db.execute("SELECT id, reason FROM global_bans WHERE hwid = ?", (hwid,)) as cursor:
        row = await cursor.fetchone()
        if row:
            return json_response({"banned": True, "reason": row[1]})

    # check component match - load all ban components and check intersection
    if components:
        component_set = set(str(c) for c in components)
        async with state.db.execute("SELECT id, components, reason FROM global_bans") as cursor:
            async for row in cursor:
                ban_components = set(json.loads(row[1]))
                if component_set & ban_components:
                    return json_response({"banned": True, "reason": row[2]})

    return json_response({"banned": False})


async def handle_ban_server(request: web.Request) -> web.Response:
    state: MasterState = request.app["state"]
    ip = get_real_ip(request)
    mod = await validate_mod_token(state, request)
    if not mod:
        return error_response(401, "Not authenticated")

    try:
        data = await request.json()
    except Exception:
        return error_response(400, "Invalid JSON")

    server_ip = str(data.get("ip", "")).strip()
    reason = str(data.get("reason", ""))[:512]

    if not server_ip:
        return error_response(400, "Server IP required")

    try:
        await state.db.execute(
            "INSERT INTO banned_servers (ip, reason, banned_by, created_at) VALUES (?, ?, ?, ?)",
            (server_ip, reason, mod["username"], time.time())
        )
        await state.db.commit()
    except Exception:
        return error_response(409, "Server already banned")

    state._banned_server_ips.add(server_ip)
    state.invalidate_cache()
    await audit_log(state, "server_ban", mod["username"], f"ip={server_ip} reason={reason}", ip)
    print(f"[mod] {mod['username']} banned server {server_ip}")
    return json_response({"ok": True})


async def handle_unban_server(request: web.Request) -> web.Response:
    state: MasterState = request.app["state"]
    ip = get_real_ip(request)
    mod = await validate_mod_token(state, request)
    if not mod:
        return error_response(401, "Not authenticated")

    try:
        data = await request.json()
    except Exception:
        return error_response(400, "Invalid JSON")

    server_ip = str(data.get("ip", "")).strip()
    if not server_ip:
        return error_response(400, "Server IP required")

    await state.db.execute("DELETE FROM banned_servers WHERE ip = ?", (server_ip,))
    await state.db.commit()
    state._banned_server_ips.discard(server_ip)
    state.invalidate_cache()
    await audit_log(state, "server_unban", mod["username"], f"ip={server_ip}", ip)
    return json_response({"ok": True})


async def handle_banned_servers_list(request: web.Request) -> web.Response:
    state: MasterState = request.app["state"]
    mod = await validate_mod_token(state, request)
    if not mod:
        return error_response(401, "Not authenticated")

    servers = []
    async with state.db.execute("SELECT id, ip, reason, banned_by, created_at FROM banned_servers ORDER BY created_at DESC") as cursor:
        async for row in cursor:
            servers.append({"id": row[0], "ip": row[1], "reason": row[2], "banned_by": row[3], "created_at": row[4]})
    return json_response({"ok": True, "servers": servers})


async def cleanup_task(state: MasterState):
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL)
        now = time.time()
        stale = [k for k, v in state.servers.items() if now - v.lastHeartbeat > STALE_TIMEOUT]
        for k in stale:
            del state.servers[k]
        if stale:
            state.invalidate_cache()
            print(f"[cleanup] Removed {len(stale)} stale server(s), {len(state.servers)} active")
        state.rate_limiter.cleanup()

        # persist rate limiter hot entries so they survive restarts
        if state.db:
            await state.rate_limiter.persist(state.db)

        await state.db.execute("DELETE FROM mod_sessions WHERE expires_at < ?", (now,))
        await state.db.execute("DELETE FROM ip_blacklist WHERE blacklisted_at IS NOT NULL AND blacklisted_at < ?", (now - BLACKLIST_DURATION,))
        await state.db.commit()


def create_app(mod_secret: str) -> web.Application:
    app = web.Application(client_max_size=MAX_BODY_SIZE)
    state = MasterState()
    app["state"] = state
    app["mod_secret"] = mod_secret

    app.router.add_get("/servers", handle_servers)
    app.router.add_get("/icon", handle_icon)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/heartbeat", handle_heartbeat)
    app.router.add_post("/deregister", handle_deregister)

    app.router.add_post("/mod/register", handle_mod_register)
    app.router.add_post("/mod/login", handle_mod_login)
    app.router.add_get("/mod/me", handle_mod_me)
    app.router.add_get("/mod/verify", handle_mod_verify)
    app.router.add_post("/mod/logout", handle_mod_logout)

    app.router.add_post("/mod/global-ban", handle_global_ban)
    app.router.add_post("/mod/global-unban", handle_global_unban)
    app.router.add_get("/mod/global-bans", handle_global_bans_list)

    app.router.add_post("/bans/check", handle_ban_check)

    app.router.add_post("/mod/ban-server", handle_ban_server)
    app.router.add_post("/mod/unban-server", handle_unban_server)
    app.router.add_get("/mod/banned-servers", handle_banned_servers_list)

    app.router.add_route("OPTIONS", "/{path:.*}", handle_options)

    async def start_background(app):
        await init_db(state)
        app["cleanup_task"] = asyncio.create_task(cleanup_task(state))

    async def stop_background(app):
        app["cleanup_task"].cancel()
        try:
            await app["cleanup_task"]
        except asyncio.CancelledError:
            pass
        if state.db:
            await state.db.close()

    app.on_startup.append(start_background)
    app.on_cleanup.append(stop_background)
    return app


def main():
    parser = argparse.ArgumentParser(description="Cypress Master Server (aiohttp)")
    parser.add_argument("--bind", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=27900, help="HTTP port")
    parser.add_argument("--behind-proxy", action="store_true", help="Trust X-Forwarded-For header (only enable behind a reverse proxy)")
    args = parser.parse_args()

    global _trust_proxy_headers
    _trust_proxy_headers = args.behind_proxy

    mod_secret = get_or_create_secret()
    app = create_app(mod_secret)
    print(f"Cypress Master Server listening on {args.bind}:{args.port}")
    web.run_app(app, host=args.bind, port=args.port, print=None)


if __name__ == "__main__":
    main()
