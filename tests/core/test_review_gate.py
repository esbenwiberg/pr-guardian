"""The review gate is the bound that keeps a trigger from starting N pipelines at once."""

from __future__ import annotations

import asyncio

import pytest

from pr_guardian.core.review_gate import (
    DEFAULT_MAX_CONCURRENT_REVIEWS,
    max_concurrent_reviews,
    review_slot,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("4", 4),
        ("1", 1),
        # Anything that is not a positive integer must fall back rather than be
        # read as "no limit" — unbounded is the failure this module prevents, so a
        # typo in the deployment must not be able to reach it.
        ("0", DEFAULT_MAX_CONCURRENT_REVIEWS),
        ("-3", DEFAULT_MAX_CONCURRENT_REVIEWS),
        ("many", DEFAULT_MAX_CONCURRENT_REVIEWS),
        ("", DEFAULT_MAX_CONCURRENT_REVIEWS),
    ],
)
def test_limit_parsing_never_yields_unbounded(monkeypatch, raw, expected):
    monkeypatch.setenv("GUARDIAN_MAX_CONCURRENT_REVIEWS", raw)
    assert max_concurrent_reviews() == expected


def test_limit_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("GUARDIAN_MAX_CONCURRENT_REVIEWS", raising=False)
    assert max_concurrent_reviews() == DEFAULT_MAX_CONCURRENT_REVIEWS


async def test_gate_caps_simultaneous_holders(monkeypatch):
    """Twenty detached callers must never be inside the block more than `limit` at once.

    This is the production scenario in miniature: the readiness reconciler
    detaching a review per recoverable candidate. Before the gate, `concurrent`
    here would reach 20.
    """
    monkeypatch.setenv("GUARDIAN_MAX_CONCURRENT_REVIEWS", "3")

    concurrent = 0
    peak = 0
    release = asyncio.Event()

    async def holder() -> None:
        nonlocal concurrent, peak
        async with review_slot(label="test"):
            concurrent += 1
            peak = max(peak, concurrent)
            await release.wait()
            concurrent -= 1

    tasks = [asyncio.create_task(holder()) for _ in range(20)]
    # Let everything that can acquire do so, then check nothing extra slipped in.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert peak == 3

    release.set()
    await asyncio.gather(*tasks)
    assert peak == 3
    assert concurrent == 0


async def test_slot_is_released_when_the_body_raises(monkeypatch):
    """A failing review must not permanently consume a slot.

    Reviews fail routinely (platform 401s, oversized diffs). If a raised exception
    leaked a permit the gate would ratchet closed and the replica would silently
    stop reviewing anything.
    """
    monkeypatch.setenv("GUARDIAN_MAX_CONCURRENT_REVIEWS", "1")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            async with review_slot(label="test"):
                raise RuntimeError("pipeline blew up")

    # Still acquirable, so no permit was lost.
    async with review_slot(label="test"):
        pass
