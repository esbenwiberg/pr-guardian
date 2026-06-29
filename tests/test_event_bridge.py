"""Cross-replica event fan-out (Postgres NOTIFY bridge).

The SSE event bus is in-process; under multiple replicas a scan's progress
events publish on a different replica than the open SSE stream and the live
modal freezes. The bridge re-broadcasts events via NOTIFY and re-injects them
into each replica's local bus. These tests cover the bus hook + the
encode/decode/origin-skip logic without needing a live Postgres.
"""

from __future__ import annotations

import asyncio
import json

import pr_guardian.core.event_bridge as eb
from pr_guardian.core.event_bridge import PgEventBridge, _asyncpg_connect_args, _decode, _encode
from pr_guardian.core.events import EventBus, ReviewEvent


def _event(**kw) -> ReviewEvent:
    base = dict(review_id="r1", pr_id="", repo="org/repo", stage="scan_analysis", detail="d")
    base.update(kw)
    return ReviewEvent(**base)


# --- EventBus remote-publisher hook -----------------------------------------


async def test_publish_still_fans_out_locally_without_a_bridge():
    bus = EventBus()
    async with bus.subscription() as q:
        bus.publish(_event())
        ev = await asyncio.wait_for(q.get(), timeout=1)
        assert ev.stage == "scan_analysis"


async def test_publish_invokes_remote_publisher_when_registered():
    bus = EventBus()
    seen: list[ReviewEvent] = []

    async def remote(ev: ReviewEvent) -> None:
        seen.append(ev)

    bus.set_remote_publisher(remote)
    async with bus.subscription() as q:
        bus.publish(_event(stage="scan_report"))
        # local delivery is synchronous…
        local = await asyncio.wait_for(q.get(), timeout=1)
        # …remote is scheduled as a task, so let the loop run it.
        await asyncio.sleep(0)
    assert local.stage == "scan_report"
    assert [e.stage for e in seen] == ["scan_report"]


async def test_clear_remote_publisher_stops_remote_delivery():
    bus = EventBus()
    seen: list[ReviewEvent] = []
    bus.set_remote_publisher(lambda ev: seen.append(ev) or asyncio.sleep(0))
    bus.clear_remote_publisher()
    bus.publish(_event())
    await asyncio.sleep(0)
    assert seen == []


# --- encode / decode / origin skip ------------------------------------------


def test_encode_decode_round_trip_preserves_event():
    payload = _encode(_event(detail="hello"))
    assert payload is not None
    # A *different* replica decodes it back into the same event.
    saved_origin = eb._ORIGIN
    eb._ORIGIN = "some-other-replica"
    try:
        out = _decode(payload)
    finally:
        eb._ORIGIN = saved_origin
    assert out is not None
    assert out.review_id == "r1"
    assert out.detail == "hello"
    assert out.stage == "scan_analysis"


def test_decode_skips_own_origin_echo():
    # Encoded with our origin → decoding on the SAME replica must skip it
    # (it was already delivered locally; re-injecting would duplicate).
    payload = _encode(_event())
    assert _decode(payload) is None


def test_decode_rejects_garbage_and_non_dict():
    assert _decode("not json") is None
    assert _decode(json.dumps(["a", "list"])) is None
    assert _decode(json.dumps({"unexpected": "field"})) is None  # bad kwargs → None


def test_encode_trims_detail_when_over_payload_cap():
    huge = "x" * (eb._MAX_PAYLOAD_BYTES + 500)
    payload = _encode(_event(detail=huge))
    assert payload is not None
    assert len(payload.encode("utf-8")) <= eb._MAX_PAYLOAD_BYTES
    data = json.loads(payload)
    assert data["detail"] == ""  # dropped to fit
    assert data["stage"] == "scan_analysis"  # stage (drives the UI) preserved


# --- listener callback re-injects into the local bus ------------------------


async def test_on_notify_reinjects_remote_event_into_local_bus(monkeypatch):
    # Build a payload as if it came from another replica.
    monkeypatch.setattr(eb, "_ORIGIN", "replica-A")
    payload = _encode(_event(stage="scan_report"))
    monkeypatch.setattr(eb, "_ORIGIN", "replica-B")  # we are a different replica

    delivered: list[ReviewEvent] = []
    monkeypatch.setattr(eb.event_bus, "fanout_local", lambda ev: delivered.append(ev))

    PgEventBridge()._on_notify(None, 0, eb._CHANNEL, payload)
    assert [e.stage for e in delivered] == ["scan_report"]


async def test_on_notify_ignores_own_echo(monkeypatch):
    payload = _encode(_event())  # encoded with current _ORIGIN
    delivered: list[ReviewEvent] = []
    monkeypatch.setattr(eb.event_bus, "fanout_local", lambda ev: delivered.append(ev))
    PgEventBridge()._on_notify(None, 0, eb._CHANNEL, payload)
    assert delivered == []


# --- DSN parsing ------------------------------------------------------------


def test_asyncpg_connect_args_prod_ssl_url():
    dsn, ssl = _asyncpg_connect_args("postgresql+asyncpg://u:p@host:5432/db?ssl=require")
    assert dsn == "postgresql://u:p@host:5432/db"
    assert ssl is True


def test_asyncpg_connect_args_local_no_ssl():
    dsn, ssl = _asyncpg_connect_args("postgresql+asyncpg://guardian:guardian@localhost:5432/g")
    assert dsn == "postgresql://guardian:guardian@localhost:5432/g"
    assert ssl is None


async def test_bridge_start_is_noop_on_non_postgres(monkeypatch):
    # sqlite/no-DB: single process, bridge must do nothing and register no
    # remote publisher (local bus already reaches every subscriber).
    monkeypatch.setattr("pr_guardian.persistence.leader_lock._is_postgres", lambda: False)
    b = PgEventBridge()
    await b.start()
    assert b._started is False
    assert b._conn is None
