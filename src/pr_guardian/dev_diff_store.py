"""Dev-only stored-diff sidecar.

Local dev has no platform connection, so the dashboard's *live* diff fetch fails
and the review-detail "Show code", Chapters, and Wizard surfaces can't render
real hunks. ``scripts/dev_seed.py`` writes a JSON sidecar of realistic diffs
keyed by review id; the dashboard diff + capabilities endpoints prefer it when
present.

This is strictly a development affordance: production never ships the sidecar
file and always has a platform connection, so the live-fetch path is untouched
there. The endpoints consult this store only when ``load()`` actually returns
something — i.e. the file exists and has an entry for the review.

The stored shape per review mirrors the diff endpoint's own response:

    {
      "<review_id>": {
        "pr_id": "117",
        "repo": "owner/name",
        "files": [
          {"path": "...", "status": "modified", "old_path": null,
           "additions": 3, "deletions": 0, "patch": "@@ -.. @@\\n+..."}
        ]
      }
    }
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_DEFAULT_PATH = ".dev_seed_diffs.json"


def store_path() -> Path:
    """Where the sidecar lives. Override with ``GUARDIAN_DEV_DIFF_STORE``.

    Defaults to a CWD-relative file; the seed and the app both launch from the
    repo root via ``scripts/agent-serve.sh``, so they agree on the location.
    """
    return Path(os.environ.get("GUARDIAN_DEV_DIFF_STORE", _DEFAULT_PATH))


def save_all(diffs: dict[str, dict]) -> Path:
    """Write the full review-id -> diff mapping, replacing any previous file."""
    path = store_path()
    path.write_text(json.dumps(diffs, indent=2))
    return path


def load(review_id: object) -> dict | None:
    """Return the stored diff for ``review_id``, or None when unavailable.

    Never raises: a missing or unreadable sidecar simply means "no stored diff",
    which lets callers fall through to the live platform path.
    """
    path = store_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    entry = data.get(str(review_id))
    return entry if isinstance(entry, dict) else None
