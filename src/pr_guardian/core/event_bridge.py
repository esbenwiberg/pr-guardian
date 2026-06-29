"""Cross-replica fan-out for ``ReviewEvent``s via Postgres LISTEN/NOTIFY.

The SSE :data:`~pr_guardian.core.events.event_bus` is in-process: a subscriber
on one replica never sees events published on another. On Container Apps with
``maxReplicas > 1`` that means live scan/review progress freezes whenever the
work lands on a different replica than the open SSE stream (the scan still
completes — its state is in the shared DB — but the live modal never advances).

This bridge re-broadcasts every published event through a Postgres ``NOTIFY``
channel. Each replica holds one dedicated ``LISTEN`` connection and re-injects
received events into its *local* bus. Events carry an origin id so the
originating replica ignores its own ``NOTIFY`` (it already delivered locally).

The dedicated connection is opened directly via asyncpg (not from the request
pool), mirroring ``leader_lock`` — one extra connection per replica, negligible
against the server ``max_connections``.

No-DB / sqlite (local, tests): single process, so the bridge is a no-op — the
in-process bus already reaches every subscriber.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

import structlog

from pr_guardian.core.events import ReviewEvent, event_bus

log = structlog.get_logger()

_CHANNEL = "guardian_events"
# Unique per process: lets a replica ignore the NOTIFY echo of its own publish.
_ORIGIN = uuid.uuid4().hex
# Postgres caps a NOTIFY payload at 8000 bytes; stay well under it.
_MAX_PAYLOAD_BYTES = 7800


def _asyncpg_connect_args(sqlalchemy_url: str) -> tuple[str, bool | None]:
    """Turn a SQLAlchemy asyncpg URL into a raw asyncpg DSN + ssl flag.

    ``database._get_database_url`` yields e.g.
    ``postgresql+asyncpg://u:p@host:5432/db?ssl=require``. asyncpg.connect wants
    a plain ``postgresql://`` DSN and ``ssl`` as a keyword, not a query param.
    """
    bare = sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://")
    parts = urlsplit(bare)
    query = parse_qs(parts.query)
    ssl: bool | None = None
    raw_ssl = (query.get("ssl") or query.get("sslmode") or [""])[0].lower()
    if raw_ssl in ("require", "true", "1", "verify-full", "verify-ca", "prefer", "allow"):
        ssl = True
    elif raw_ssl in ("disable", "false", "0"):
        ssl = False
    dsn = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return dsn, ssl


class PgEventBridge:
    """Bridges the in-process event bus across replicas via Postgres NOTIFY."""

    def __init__(self) -> None:
        self._conn: Any = None  # asyncpg.Connection (untyped) | None
        self._send_lock = asyncio.Lock()
        self._started = False

    async def start(self) -> None:
        from pr_guardian.persistence.leader_lock import _is_postgres

        if self._started or not _is_postgres():
            return
        try:
            import asyncpg  # type: ignore[import-untyped]

            from pr_guardian.persistence.database import _get_database_url

            dsn, ssl = _asyncpg_connect_args(_get_database_url())
            self._conn = await asyncpg.connect(dsn, ssl=ssl, timeout=10)
            await self._conn.add_listener(_CHANNEL, self._on_notify)
        except Exception as e:
            # Never let a bridge failure block startup — fall back to in-process
            # delivery (live progress degrades on multi-replica, nothing breaks).
            log.warning("event_bridge_start_failed", error=str(e))
            if self._conn is not None:
                try:
                    await self._conn.close()
                except Exception:
                    pass
                self._conn = None
            return
        event_bus.set_remote_publisher(self._publish_remote)
        self._started = True
        log.info("event_bridge_started", origin=_ORIGIN)

    async def stop(self) -> None:
        event_bus.clear_remote_publisher()
        self._started = False
        if self._conn is not None:
            try:
                await self._conn.remove_listener(_CHANNEL, self._on_notify)
            except Exception:
                pass
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _on_notify(self, _conn, _pid, _channel, payload: str) -> None:
        """asyncpg listener callback: re-inject a remote event into the local bus."""
        event = _decode(payload)
        if event is None:
            return
        event_bus.fanout_local(event)

    async def _publish_remote(self, event: ReviewEvent) -> None:
        if self._conn is None:
            return
        payload = _encode(event)
        if payload is None:
            return
        try:
            async with self._send_lock:
                await self._conn.execute("SELECT pg_notify($1, $2)", _CHANNEL, payload)
        except Exception as e:
            log.warning("event_bridge_publish_failed", error=str(e))


def _encode(event: ReviewEvent) -> str | None:
    """Serialize an event for NOTIFY, tagging origin and trimming to the cap."""
    data = asdict(event)
    data["_origin"] = _ORIGIN
    payload = json.dumps(data)
    if len(payload.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        # The stage is what drives the UI; detail is a nice-to-have. Drop it
        # rather than overflow the NOTIFY payload (which would raise).
        data["detail"] = ""
        payload = json.dumps(data)
    return payload


def _decode(payload: str) -> ReviewEvent | None:
    """Parse a NOTIFY payload back into a ReviewEvent, skipping our own echo."""
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.pop("_origin", None) == _ORIGIN:
        return None  # our own publish — already delivered locally
    try:
        return ReviewEvent(**data)
    except TypeError:
        return None


# Singleton, started/stopped in the app lifespan.
bridge = PgEventBridge()
