from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WebhookPayload:
    """Raw webhook payload before normalization."""
    platform: str  # "ado" or "github"
    event_type: str
    headers: dict[str, str]
    body: dict
