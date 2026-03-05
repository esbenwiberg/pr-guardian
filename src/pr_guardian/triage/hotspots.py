from __future__ import annotations


async def load_hotspots(repo: str) -> set[str]:
    """Fetch pre-computed hotspot file paths.

    In full implementation, this reads from database.
    For now, returns empty set (hotspot computation is a nightly job).
    """
    # TODO: implement DB lookup once persistence layer is wired
    return set()


def check_hotspot_hits(
    changed_files: list[str],
    hotspots: set[str],
) -> list[str]:
    """Return list of changed files that are hotspots."""
    return [f for f in changed_files if f in hotspots]
